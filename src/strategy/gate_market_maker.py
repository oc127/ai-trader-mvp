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
    coin_balance: float = 0.0
    coin_balance_usd: float = 0.0
    realized_pnl: float = 0.0
    total_fills: int = 0
    total_volume_usd: float = 0.0


class GateMarketMaker:
    """Spot market maker on Gate.io.

    Every tick: query real balances, cancel all orders, place new ones.
    No internal fill tracking — uses exchange as source of truth.
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
        self._skew_intensity: float = cfg.get("skew_intensity", 1.0)

        self._states: dict[str, MMState] = {}
        self._prev_mids: dict[str, float] = {}
        self._last_refresh: dict[str, float] = {}

    @property
    def pairs(self) -> list[str]:
        return list(self._pairs)

    def get_state(self, pair: str) -> MMState:
        if pair not in self._states:
            self._states[pair] = MMState(pair=pair)
        return self._states[pair]

    def seed_inventory(self, seed_usd: float | None = None) -> None:
        """Market buy initial inventory for each pair."""
        amount = seed_usd or self._base_order_usd * 3
        for pair in self._pairs:
            coin = pair.split("_")[0]
            try:
                balances = self._client.get_spot_balances()
                book = self._client.get_order_book(pair, limit=1)
                mid = (float(book["bids"][0][0]) + float(book["asks"][0][0])) / 2
                current_usd = balances.get(coin, 0.0) * mid
                if current_usd >= amount * 0.5:
                    log.info("Already have $%.2f of %s, skipping seed", current_usd, coin)
                    continue
            except Exception:
                pass

            try:
                self._client.spot_market_buy(pair, amount)
                log.info("Seeded %s: market bought $%.2f", pair, amount)
            except Exception:
                log.warning("Failed to seed inventory for %s", pair)

    def tick(self, pair: str) -> dict:
        """Main loop tick for one pair."""
        state = self.get_state(pair)
        now = time.time()

        last = self._last_refresh.get(pair, 0)
        if now - last < self._refresh_interval_sec:
            return {"action": "skip", "reason": "too_soon"}

        # 1. Get orderbook
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

        # 2. Get real balances from exchange
        try:
            balances = self._client.get_spot_balances()
        except Exception:
            log.warning("Failed to fetch balances for %s", pair)
            return {"action": "error", "reason": "balance_fetch_failed"}

        coin = pair.split("_")[0]
        coin_balance = balances.get(coin, 0.0)
        usdt_balance = balances.get("USDT", 0.0)
        state.coin_balance = coin_balance
        state.coin_balance_usd = coin_balance * mid

        # 3. Calculate spread and quotes
        spread_bps = self._calculate_spread(pair, mid, market_spread_bps)
        skew = self._calculate_skew(pair, state.coin_balance_usd)
        quotes = self._generate_quotes(pair, mid, spread_bps, skew, coin_balance, usdt_balance)

        # 4. Cancel all and place new
        try:
            self._client.cancel_all_orders(pair)
        except Exception:
            log.warning("Failed to cancel orders for %s", pair)

        placed_bids = 0
        placed_asks = 0
        for quote in quotes:
            if quote.amount <= 0 or quote.price <= 0:
                continue
            try:
                if quote.side == "buy":
                    self._client.spot_limit_buy(pair, quote.price, quote.amount)
                    placed_bids += 1
                else:
                    self._client.spot_limit_sell(pair, quote.price, quote.amount)
                    placed_asks += 1
            except Exception:
                log.warning("Failed to place %s %s @ %.6f x %.0f",
                            quote.side, pair, quote.price, quote.amount)

        self._last_refresh[pair] = now

        return {
            "action": "refreshed",
            "pair": pair,
            "mid": mid,
            "spread_bps": spread_bps,
            "market_spread_bps": market_spread_bps,
            "skew": skew,
            "coin_balance": coin_balance,
            "coin_balance_usd": state.coin_balance_usd,
            "usdt_balance": usdt_balance,
            "bids": placed_bids,
            "asks": placed_asks,
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

    def _calculate_skew(self, pair: str, inventory_usd: float) -> float:
        if self._max_inventory_usd == 0:
            return 0.0
        ratio = inventory_usd / self._max_inventory_usd
        skew = ratio * self._skew_intensity
        return max(-1.0, min(1.0, skew))

    def _generate_quotes(
        self, pair: str, mid: float, spread_bps: float, skew: float,
        coin_balance: float, usdt_balance: float,
    ) -> list[Quote]:
        half_spread = spread_bps / 2 / 10000
        skewed_mid = mid * (1 - skew * half_spread)

        coin_balance_usd = coin_balance * mid
        can_buy = (
            coin_balance_usd < self._max_inventory_usd
            and usdt_balance > self._base_order_usd * 0.5
        )
        can_sell = coin_balance > 0

        price_prec = self._price_precision.get(pair, 6)
        amount_prec = self._amount_precision.get(pair, 2)

        remaining_sell = coin_balance if can_sell else 0.0
        remaining_buy_usd = min(
            usdt_balance * 0.8,
            self._max_inventory_usd - coin_balance_usd,
        )

        quotes: list[Quote] = []
        for tier in range(self._num_tiers):
            offset = half_spread + (tier * self._tier_spacing_bps / 10000)
            size_usd = self._base_order_usd * (self._tier_size_multiplier ** tier)
            size_coins = size_usd / mid

            if can_buy and remaining_buy_usd >= size_usd * 0.5:
                bid = round(skewed_mid * (1 - offset), price_prec)
                buy_amount = round(size_coins, amount_prec)
                if buy_amount > 0:
                    quotes.append(Quote(side="buy", price=bid, amount=buy_amount, tier=tier))
                    remaining_buy_usd -= size_usd

            if can_sell and remaining_sell > 0:
                sell_amount = min(round(size_coins, amount_prec), round(remaining_sell, amount_prec))
                if sell_amount > 0:
                    ask = round(skewed_mid * (1 + offset), price_prec)
                    quotes.append(Quote(side="sell", price=ask, amount=sell_amount, tier=tier))
                    remaining_sell -= sell_amount

        return quotes

    def cancel_all(self) -> None:
        """Emergency: cancel all orders on all pairs."""
        for pair in self._pairs:
            try:
                self._client.cancel_all_orders(pair)
                log.info("Cancelled all orders for %s", pair)
            except Exception:
                log.warning("Failed to cancel orders for %s", pair)

    def get_summary(self) -> str:
        lines = ["=== Market Maker Status ==="]
        for pair in self._pairs:
            state = self.get_state(pair)
            lines.append(
                f"  {pair}: mid=${state.mid_price:.6f} spread={state.spread:.1f}bps "
                f"coins={state.coin_balance:.0f}(${state.coin_balance_usd:.2f}) "
                f"pnl=${state.realized_pnl:.4f}"
            )
        return "\n".join(lines)
