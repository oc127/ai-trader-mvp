from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from src.logger import get_logger

if TYPE_CHECKING:
    from src.gate_client.rest import GateClient

log = get_logger(__name__)


@dataclass
class Quote:
    side: str
    price: float
    amount: float
    tier: int
    order_id: str = ""


@dataclass
class MMState:
    pair: str
    mid_price: float = 0.0
    spread: float = 0.0
    inventory: float = 0.0
    inventory_usd: float = 0.0
    active_bids: list[Quote] = field(default_factory=list)
    active_asks: list[Quote] = field(default_factory=list)
    realized_pnl: float = 0.0
    total_fills: int = 0
    total_volume_usd: float = 0.0
    last_fill_time: float = 0.0


class GateMarketMaker:
    """Spot market maker on Gate.io.

    Places tiered limit orders on both sides of the book,
    skews quotes based on inventory, and manages risk.
    """

    def __init__(self, client: GateClient, config: dict) -> None:
        self._client = client
        cfg = config.get("market_maker", {})

        self._pairs: list[str] = cfg.get("pairs", ["DOGE_USDT"])
        self._base_spread_bps: float = cfg.get("base_spread_bps", 10)
        self._num_tiers: int = cfg.get("num_tiers", 3)
        self._tier_spacing_bps: float = cfg.get("tier_spacing_bps", 5)
        self._tier_size_multiplier: float = cfg.get("tier_size_multiplier", 1.5)
        self._base_order_usd: float = cfg.get("base_order_usd", 50)
        self._max_inventory_usd: float = cfg.get("max_inventory_usd", 2000)
        self._max_total_inventory_usd: float = cfg.get("max_total_inventory_usd", 5000)
        self._refresh_interval_sec: float = cfg.get("refresh_interval_sec", 5)
        self._min_spread_bps: float = cfg.get("min_spread_bps", 3)
        self._volatility_widen_factor: float = cfg.get("volatility_widen_factor", 2.0)
        self._volatility_threshold_pct: float = cfg.get("volatility_threshold_pct", 0.005)
        self._price_precision: dict[str, int] = cfg.get("price_precision", {})
        self._amount_precision: dict[str, int] = cfg.get("amount_precision", {})
        self._max_open_orders: int = cfg.get("max_open_orders", 20)
        self._skew_intensity: float = cfg.get("skew_intensity", 1.0)

        self._states: dict[str, MMState] = {}
        self._prev_mids: dict[str, float] = {}
        self._last_refresh: dict[str, float] = {}
        self._running = False

    @property
    def pairs(self) -> list[str]:
        return list(self._pairs)

    def get_state(self, pair: str) -> MMState:
        if pair not in self._states:
            self._states[pair] = MMState(pair=pair)
        return self._states[pair]

    def get_total_inventory_usd(self) -> float:
        return sum(abs(s.inventory_usd) for s in self._states.values())

    def sync_balances(self) -> None:
        """Load actual coin balances from exchange into inventory state."""
        try:
            balances = self._client.get_spot_balances()
        except Exception:
            log.warning("Failed to fetch spot balances")
            return

        for pair in self._pairs:
            coin = pair.split("_")[0]
            amount = balances.get(coin, 0.0)
            state = self.get_state(pair)
            if amount > 0 and state.inventory == 0:
                mid = state.mid_price if state.mid_price > 0 else 1.0
                state.inventory = amount
                state.inventory_usd = amount * mid
                log.info("Loaded balance for %s: %.4f coins ($%.2f)",
                         pair, amount, state.inventory_usd)

    def seed_inventory(self, seed_usd: float | None = None) -> None:
        """Market buy initial inventory for each pair so we can quote both sides."""
        amount = seed_usd or self._base_order_usd * 3
        for pair in self._pairs:
            state = self.get_state(pair)
            if state.inventory_usd >= amount:
                log.info("Already have inventory for %s, skipping seed", pair)
                continue
            try:
                result = self._client.spot_market_buy(pair, amount)
                log.info("Seeded %s: market bought $%.2f", pair, amount)
                # Sync actual balance after buy
                try:
                    balances = self._client.get_spot_balances()
                    coin = pair.split("_")[0]
                    coin_amount = balances.get(coin, 0.0)
                    book = self._client.get_order_book(pair, limit=1)
                    mid = float(book["asks"][0][0])
                    state.inventory = coin_amount
                    state.inventory_usd = coin_amount * mid
                    state.mid_price = mid
                    log.info("Seed balance: %s %.2f coins ($%.2f)",
                             pair, coin_amount, state.inventory_usd)
                except Exception:
                    state.inventory_usd = amount
                    log.info("Seed estimated: %s ~$%.2f", pair, amount)
            except Exception:
                log.warning("Failed to seed inventory for %s", pair)

    def tick(self, pair: str) -> dict:
        """Main loop tick for one pair. Returns action taken."""
        state = self.get_state(pair)
        now = time.time()

        last = self._last_refresh.get(pair, 0)
        if now - last < self._refresh_interval_sec:
            return {"action": "skip", "reason": "too_soon"}

        try:
            book = self._client.get_order_book(pair, limit=10)
        except Exception:
            log.warning("Failed to fetch orderbook for %s", pair)
            return {"action": "error", "reason": "orderbook_fetch_failed"}

        bids = book.get("bids", [])
        asks = book.get("asks", [])
        if not bids or not asks:
            return {"action": "skip", "reason": "empty_book"}

        best_bid = float(bids[0][0])
        best_ask = float(asks[0][0])
        mid = (best_bid + best_ask) / 2
        market_spread_bps = (best_ask - best_bid) / mid * 10000

        state.mid_price = mid
        state.spread = market_spread_bps

        spread_bps = self._calculate_spread(pair, mid, market_spread_bps)
        skew = self._calculate_skew(pair)
        quotes = self._generate_quotes(pair, mid, spread_bps, skew)

        self._sync_fills(pair)

        try:
            self._cancel_and_replace(pair, quotes)
        except Exception:
            log.exception("Failed to refresh quotes for %s", pair)
            return {"action": "error", "reason": "order_placement_failed"}

        self._last_refresh[pair] = now

        return {
            "action": "refreshed",
            "pair": pair,
            "mid": mid,
            "spread_bps": spread_bps,
            "market_spread_bps": market_spread_bps,
            "skew": skew,
            "inventory_usd": state.inventory_usd,
            "n_quotes": len(quotes),
        }

    def _calculate_spread(self, pair: str, mid: float, market_spread_bps: float) -> float:
        spread = max(self._base_spread_bps, market_spread_bps * 0.8)

        prev = self._prev_mids.get(pair)
        if prev and prev > 0:
            change = abs(mid - prev) / prev
            if change > self._volatility_threshold_pct:
                spread *= self._volatility_widen_factor
                log.info("Volatility detected on %s, widening spread to %.1f bps",
                         pair, spread)

        self._prev_mids[pair] = mid
        return max(spread, self._min_spread_bps)

    def _calculate_skew(self, pair: str) -> float:
        state = self.get_state(pair)
        if self._max_inventory_usd == 0:
            return 0.0
        ratio = state.inventory_usd / self._max_inventory_usd
        return ratio * self._skew_intensity

    def _generate_quotes(
        self, pair: str, mid: float, spread_bps: float, skew: float,
    ) -> list[Quote]:
        half_spread = spread_bps / 2 / 10000
        skewed_mid = mid * (1 - skew * half_spread)

        state = self.get_state(pair)
        total_inv = self.get_total_inventory_usd()
        can_buy = (
            state.inventory_usd < self._max_inventory_usd
            and total_inv < self._max_total_inventory_usd
        )
        # Spot MM: can only sell coins we actually hold
        can_sell = state.inventory > 0

        price_prec = self._price_precision.get(pair, 6)
        amount_prec = self._amount_precision.get(pair, 2)

        remaining_sell = state.inventory if can_sell else 0.0

        quotes: list[Quote] = []
        for tier in range(self._num_tiers):
            offset = half_spread + (tier * self._tier_spacing_bps / 10000)
            size_usd = self._base_order_usd * (self._tier_size_multiplier ** tier)
            size = size_usd / mid

            if can_buy:
                bid = round(skewed_mid * (1 - offset), price_prec)
                quotes.append(Quote(
                    side="buy", price=bid,
                    amount=round(size, amount_prec), tier=tier,
                ))

            if can_sell and remaining_sell > 0:
                sell_size = min(size, remaining_sell)
                if sell_size > 0:
                    ask = round(skewed_mid * (1 + offset), price_prec)
                    quotes.append(Quote(
                        side="sell", price=ask,
                        amount=round(sell_size, amount_prec), tier=tier,
                    ))
                    remaining_sell -= sell_size

        return quotes

    def _sync_fills(self, pair: str) -> None:
        """Check for filled orders and update inventory."""
        state = self.get_state(pair)
        if not state.active_bids and not state.active_asks:
            return

        try:
            open_ids = {str(o["id"]) for o in self._client.list_open_orders(pair)}
        except Exception:
            return

        filled_bids = [b for b in state.active_bids if b.order_id and b.order_id not in open_ids]
        filled_asks = [a for a in state.active_asks if a.order_id and a.order_id not in open_ids]

        for bid in filled_bids:
            state.inventory += bid.amount
            state.inventory_usd += bid.amount * bid.price
            state.total_fills += 1
            state.total_volume_usd += bid.amount * bid.price
            state.last_fill_time = time.time()
            log.info("Bid filled: %s %.4f @ %.6f ($%.2f)",
                     pair, bid.amount, bid.price, bid.amount * bid.price)

        for ask in filled_asks:
            pnl = ask.amount * (ask.price - (state.inventory_usd / state.inventory if state.inventory else ask.price))
            state.inventory -= ask.amount
            state.inventory_usd -= ask.amount * ask.price
            state.realized_pnl += pnl
            state.total_fills += 1
            state.total_volume_usd += ask.amount * ask.price
            state.last_fill_time = time.time()
            log.info("Ask filled: %s %.4f @ %.6f ($%.2f, pnl=$%.4f)",
                     pair, ask.amount, ask.price, ask.amount * ask.price, pnl)

    def _cancel_and_replace(self, pair: str, new_quotes: list[Quote]) -> None:
        """Cancel all existing orders and place new ones."""
        state = self.get_state(pair)

        try:
            self._client.cancel_all_orders(pair)
        except Exception:
            log.warning("Failed to cancel orders for %s, continuing", pair)

        state.active_bids = []
        state.active_asks = []

        for quote in new_quotes:
            if quote.amount <= 0 or quote.price <= 0:
                continue
            try:
                if quote.side == "buy":
                    result = self._client.spot_limit_buy(pair, quote.price, quote.amount)
                else:
                    result = self._client.spot_limit_sell(pair, quote.price, quote.amount)

                order_id = result.get("id", "")
                quote.order_id = str(order_id)

                if quote.side == "buy":
                    state.active_bids.append(quote)
                else:
                    state.active_asks.append(quote)

            except Exception:
                log.warning("Failed to place %s %s @ %.6f x %.4f",
                            quote.side, pair, quote.price, quote.amount)

        log.debug("Placed %d bids + %d asks for %s",
                  len(state.active_bids), len(state.active_asks), pair)

    def cancel_all(self) -> None:
        """Emergency: cancel all orders on all pairs."""
        for pair in self._pairs:
            try:
                self._client.cancel_all_orders(pair)
                log.info("Cancelled all orders for %s", pair)
            except Exception:
                log.warning("Failed to cancel orders for %s", pair)
        self._states.clear()

    def get_summary(self) -> str:
        lines = ["=== Market Maker Status ==="]
        total_pnl = 0.0
        total_vol = 0.0
        for pair in self._pairs:
            state = self.get_state(pair)
            lines.append(
                f"  {pair}: mid=${state.mid_price:.6f} spread={state.spread:.1f}bps "
                f"inv={state.inventory:.4f}(${state.inventory_usd:.2f}) "
                f"pnl=${state.realized_pnl:.4f} fills={state.total_fills} "
                f"vol=${state.total_volume_usd:.2f}"
            )
            total_pnl += state.realized_pnl
            total_vol += state.total_volume_usd
        lines.append(f"  TOTAL: pnl=${total_pnl:.4f} volume=${total_vol:.2f}")
        return "\n".join(lines)
