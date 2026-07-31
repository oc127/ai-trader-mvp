from __future__ import annotations

import time
from dataclasses import dataclass

from src.logger import get_logger

log = get_logger(__name__)


@dataclass
class QuoteLevel:
    side: str  # "buy" or "sell"
    price: float
    size: float
    tier: int


@dataclass
class MMSignal:
    coin: str
    action: str  # "quote_refresh", "cancel", "none"
    quotes: list[QuoteLevel]
    reason: str


class HLMarketMaker:
    """Perpetual futures market making strategy with tiered quoting and inventory skew."""

    def __init__(self, cfg: dict) -> None:
        mm_cfg = cfg.get("strategy", {}).get("market_maker", {})
        self._enabled = mm_cfg.get("enabled", False)
        self._spread_bps = mm_cfg.get("spread_bps", 3)  # tight: 3 bps total
        self._num_tiers = mm_cfg.get("num_tiers", 5)
        self._tier_spacing_bps = mm_cfg.get("tier_spacing_bps", 2)
        self._tier_size_multiplier = mm_cfg.get("tier_size_multiplier", 1.5)
        self._base_order_usd = mm_cfg.get("base_order_usd", 500)
        self._max_inventory_usd = mm_cfg.get("max_inventory_usd", 50000)
        self._volatility_pause_pct = mm_cfg.get("volatility_pause_pct", 0.01)
        self._volatility_cooldown_sec = mm_cfg.get("volatility_cooldown_sec", 120)
        self._coins: list[str] = mm_cfg.get("coins", ["BTC", "ETH"])

        # Per-coin state
        self._inventory: dict[str, float] = {}  # coin -> USD value (positive = long, negative = short)
        self._prev_mids: dict[str, float] = {}
        self._prev_mid_times: dict[str, float] = {}
        self._volatility_pause_until: dict[str, float] = {}
        self._realized_pnl: dict[str, float] = {}

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def coins(self) -> list[str]:
        return list(self._coins)

    def get_inventory(self, coin: str) -> float:
        return self._inventory.get(coin, 0.0)

    def get_total_exposure(self) -> float:
        return sum(abs(v) for v in self._inventory.values())

    def get_realized_pnl(self, coin: str | None = None) -> float:
        if coin is not None:
            return self._realized_pnl.get(coin, 0.0)
        return sum(self._realized_pnl.values())

    def generate_quotes(
        self,
        coin: str,
        mid_price: float,
        best_bid: float,
        best_ask: float,
        book_imbalance: float,
        inventory: float,
    ) -> MMSignal:
        """Generate tiered quotes with inventory skew.

        Args:
            coin: The trading pair (e.g. "BTC").
            mid_price: Current mid-market price.
            best_bid: Current best bid price.
            best_ask: Current best ask price.
            book_imbalance: Ratio in [-1, 1]. Positive means bid-heavy, negative means ask-heavy.
            inventory: Current inventory in USD (positive = long).

        Returns:
            MMSignal with quotes to place.
        """
        if not self._enabled:
            return MMSignal(coin=coin, action="none", quotes=[], reason="MM disabled")

        if mid_price <= 0:
            return MMSignal(coin=coin, action="none", quotes=[], reason="Invalid mid price")

        now = time.time()

        # Check volatility cooldown -- cancel all quotes during cooldown
        if now < self._volatility_pause_until.get(coin, 0):
            return MMSignal(
                coin=coin,
                action="cancel",
                quotes=[],
                reason="Volatility cooldown active",
            )

        # Detect volatility: if price moved > threshold since last tick, widen spread 2x
        spread_multiplier = 1.0
        if coin in self._prev_mids and self._prev_mids[coin] > 0:
            price_change = abs(mid_price - self._prev_mids[coin]) / self._prev_mids[coin]
            if price_change > self._volatility_pause_pct:
                spread_multiplier = 2.0
                self._volatility_pause_until[coin] = now + self._volatility_cooldown_sec
                log.info(
                    "Volatility detected, widening spread",
                    extra={"coin": coin, "change_pct": price_change},
                )

        self._prev_mids[coin] = mid_price
        self._prev_mid_times[coin] = now

        # Book imbalance: widen spread when one side is thin
        # imbalance in [-1, 1], 0 = balanced
        imbalance_factor = 1.0 + abs(book_imbalance) * 0.5

        # Calculate effective half-spread in bps
        half_spread_bps = (self._spread_bps / 2.0) * spread_multiplier * imbalance_factor

        # Inventory skew: shift the mid price based on accumulated inventory
        skew = self._calculate_skew(coin)
        skewed_mid = mid_price * (1 - skew)

        # Check max inventory - determine which sides we can quote
        can_quote_buy = True
        can_quote_sell = True
        inv = self._inventory.get(coin, 0.0)
        if inv >= self._max_inventory_usd:
            can_quote_buy = False
            log.info("Max long inventory reached, only quoting sells", extra={"coin": coin})
        elif inv <= -self._max_inventory_usd:
            can_quote_sell = False
            log.info("Max short inventory reached, only quoting buys", extra={"coin": coin})

        quotes: list[QuoteLevel] = []
        for tier in range(self._num_tiers):
            # Tier offset: tier 0 uses half_spread_bps, subsequent tiers add tier_spacing_bps
            tier_offset_bps = half_spread_bps + (tier * self._tier_spacing_bps)
            tier_offset_frac = tier_offset_bps / 10000.0

            # Size increases geometrically per tier
            tier_size_usd = self._base_order_usd * (self._tier_size_multiplier**tier)
            tier_size = tier_size_usd / mid_price

            if can_quote_buy:
                bid_price = skewed_mid * (1 - tier_offset_frac)
                quotes.append(
                    QuoteLevel(side="buy", price=bid_price, size=tier_size, tier=tier)
                )

            if can_quote_sell:
                ask_price = skewed_mid * (1 + tier_offset_frac)
                quotes.append(
                    QuoteLevel(side="sell", price=ask_price, size=tier_size, tier=tier)
                )

        reason = (
            f"spread={half_spread_bps * 2:.1f}bps "
            f"skew={skew:.6f} "
            f"imbalance={book_imbalance:.2f} "
            f"inv=${inv:.0f}"
        )

        return MMSignal(
            coin=coin,
            action="quote_refresh",
            quotes=quotes,
            reason=reason,
        )

    def on_fill(self, coin: str, side: str, size: float, price: float) -> None:
        """Update inventory on fill.

        Args:
            coin: The trading pair.
            side: "buy" or "sell".
            size: Size in coin units.
            price: Fill price.
        """
        notional = size * price
        if coin not in self._inventory:
            self._inventory[coin] = 0.0
        if coin not in self._realized_pnl:
            self._realized_pnl[coin] = 0.0

        if side == "buy":
            self._inventory[coin] += notional
        else:
            self._inventory[coin] -= notional

        log.info(
            "MM fill recorded",
            extra={
                "coin": coin,
                "side": side,
                "size": size,
                "price": price,
                "inventory_usd": self._inventory[coin],
            },
        )

    def _calculate_skew(self, coin: str) -> float:
        """Inventory-based price skew.

        Long inventory -> positive skew -> lower mid (encourage sells, discourage buys).
        Short inventory -> negative skew -> raise mid (encourage buys, discourage sells).

        Returns:
            Skew as a fraction of price (positive = shift mid down).
        """
        inv = self._inventory.get(coin, 0.0)
        if self._max_inventory_usd == 0:
            return 0.0
        spread_frac = self._spread_bps / 10000.0
        skew = (inv / self._max_inventory_usd) * spread_frac * 0.5
        return skew
