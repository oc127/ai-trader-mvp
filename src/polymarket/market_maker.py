"""High-frequency market maker for Polymarket.

Core logic:
- Quote both YES and NO sides to earn the bid-ask spread
- Manage inventory to stay market-neutral (zero directional exposure)
- Auto-flatten stale positions — never hold directional risk
- Only trade high-liquidity markets with wide enough spreads
- Ultra-conservative sizing: small, frequent, low-risk

The maker earns when BOTH sides fill (buy YES low, sell YES high).
Risk comes from one-sided fills creating directional inventory.
We manage this by:
1. Skewing quotes away from our inventory (make it cheaper to reduce)
2. Auto-flattening after a time limit
3. Hard position caps per market and total
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from src.logger import get_logger

log = get_logger(__name__)


@dataclass
class MarketInventory:
    """Tracks our inventory in a single market."""
    condition_id: str
    question: str
    yes_token_id: str
    no_token_id: str
    yes_shares: float = 0.0
    no_shares: float = 0.0
    yes_avg_price: float = 0.0
    no_avg_price: float = 0.0
    realized_pnl: float = 0.0
    last_trade_ts: float = 0.0
    trade_count: int = 0

    @property
    def net_exposure(self) -> float:
        """Net directional exposure in USDC (positive = long YES)."""
        return self.yes_shares * self.yes_avg_price - self.no_shares * self.no_avg_price

    @property
    def abs_exposure(self) -> float:
        return abs(self.net_exposure)

    @property
    def is_flat(self) -> bool:
        return self.yes_shares < 0.5 and self.no_shares < 0.5

    @property
    def seconds_since_trade(self) -> float:
        if self.last_trade_ts == 0:
            return 0.0
        return time.monotonic() - self.last_trade_ts


@dataclass
class QuotePair:
    """A pair of quotes (bid + ask) for one market."""
    condition_id: str
    yes_token_id: str
    no_token_id: str
    bid_price: float  # price to buy YES
    ask_price: float  # price to sell YES (= buy NO at 1-ask)
    bid_size: float
    ask_size: float
    spread: float
    reason: str = ""


@dataclass
class MakerConfig:
    """Market maker configuration — conservative defaults."""
    # spread & pricing
    min_half_spread: float = 0.02  # 2 cents minimum half-spread
    max_half_spread: float = 0.05  # widen up to 5 cents under stress
    skew_factor: float = 0.01  # price shift per $10 inventory imbalance

    # sizing
    quote_size_usd: float = 20.0  # $20 per side per quote
    max_position_per_market: float = 100.0  # $100 max per market
    max_total_exposure: float = 300.0  # $300 total across all markets

    # market selection
    min_market_liquidity: float = 20000  # $20K liquidity minimum
    min_market_volume_24h: float = 5000  # $5K 24h volume minimum
    min_book_spread: float = 0.03  # only trade if book spread > 3 cents
    price_band_low: float = 0.15  # don't trade below 15c
    price_band_high: float = 0.85  # don't trade above 85c
    max_markets: int = 5  # max simultaneous markets

    # inventory management
    max_inventory_age_seconds: float = 300  # auto-flatten after 5 minutes
    flatten_at_pct: float = 0.80  # flatten when inventory hits 80% of max

    # risk
    max_daily_loss: float = 50.0  # stop trading if down $50
    max_consecutive_losses: int = 5  # pause after 5 consecutive losing trades
    pause_after_loss_seconds: float = 120  # 2 min cooldown after consecutive losses


class HighFreqMarketMaker:
    """Stateful market maker that manages quotes and inventory across markets."""

    def __init__(self, cfg: dict) -> None:
        pm_cfg = cfg.get("polymarket", {})
        mm_cfg = pm_cfg.get("hf_market_maker", {})

        self._config = MakerConfig(
            min_half_spread=mm_cfg.get("min_half_spread", 0.02),
            max_half_spread=mm_cfg.get("max_half_spread", 0.05),
            skew_factor=mm_cfg.get("skew_factor", 0.01),
            quote_size_usd=mm_cfg.get("quote_size_usd", 20.0),
            max_position_per_market=mm_cfg.get("max_position_per_market", 100.0),
            max_total_exposure=mm_cfg.get("max_total_exposure", 300.0),
            min_market_liquidity=mm_cfg.get("min_market_liquidity", 20000),
            min_market_volume_24h=mm_cfg.get("min_market_volume_24h", 5000),
            min_book_spread=mm_cfg.get("min_book_spread", 0.03),
            price_band_low=mm_cfg.get("price_band_low", 0.15),
            price_band_high=mm_cfg.get("price_band_high", 0.85),
            max_markets=mm_cfg.get("max_markets", 5),
            max_inventory_age_seconds=mm_cfg.get("max_inventory_age_seconds", 300),
            flatten_at_pct=mm_cfg.get("flatten_at_pct", 0.80),
            max_daily_loss=mm_cfg.get("max_daily_loss", 50.0),
            max_consecutive_losses=mm_cfg.get("max_consecutive_losses", 5),
            pause_after_loss_seconds=mm_cfg.get("pause_after_loss_seconds", 120),
        )

        self._inventory: dict[str, MarketInventory] = {}
        self._daily_pnl: float = 0.0
        self._consecutive_losses: int = 0
        self._paused_until: float = 0.0
        self._total_trades: int = 0
        self._winning_trades: int = 0
        self._reward_bands: dict[str, float] = {}

    def set_reward_bands(self, bands: dict[str, float]) -> None:
        """Set LP reward bands: {token_id: max_spread_pct}."""
        self._reward_bands = bands

    @property
    def config(self) -> MakerConfig:
        return self._config

    @property
    def daily_pnl(self) -> float:
        return self._daily_pnl

    @property
    def total_exposure(self) -> float:
        return sum(inv.abs_exposure for inv in self._inventory.values())

    @property
    def is_paused(self) -> bool:
        if self._daily_pnl <= -self._config.max_daily_loss:
            return True
        if time.monotonic() < self._paused_until:
            return True
        return False

    def select_markets(self, markets: list) -> list:
        """Filter markets suitable for market making.

        Prefers markets with moderate liquidity — too liquid means spreads
        are too tight to profit; too illiquid means fills are rare.
        """
        eligible = []
        for m in markets:
            if not m.active:
                continue
            if m.liquidity < self._config.min_market_liquidity:
                continue
            if m.volume_24h < self._config.min_market_volume_24h:
                continue
            if not (self._config.price_band_low <= m.yes_price <= self._config.price_band_high):
                continue
            eligible.append(m)

        def _score(m):
            liq = m.liquidity
            sweet_spot = 100_000
            liq_score = 1.0 / (1.0 + abs(liq - sweet_spot) / sweet_spot)
            vol_score = min(m.volume_24h / 10_000, 2.0)
            base = liq_score * vol_score

            # boost LP-reward-eligible markets (2x priority)
            has_reward = (
                self._reward_bands.get(getattr(m, "yes_token_id", ""), 0) > 0
                or self._reward_bands.get(getattr(m, "no_token_id", ""), 0) > 0
            )
            if has_reward:
                base *= 2.0

            # boost sports/World Cup markets (elevated reward pools)
            q = getattr(m, "question", "").lower()
            if any(kw in q for kw in ("world cup", "fifa", "match", "goal", "soccer", "football")):
                base *= 1.5

            return base

        eligible.sort(key=_score, reverse=True)
        return eligible[: self._config.max_markets]

    def generate_quotes(self, market, book_spread: float) -> Optional[QuotePair]:
        """Generate a bid/ask quote pair for a market.

        Places resting limit orders at min_half_spread distance from mid.
        On tight markets, these sit behind the best bid/ask and fill on
        volatility sweeps — this is intentional for small accounts.
        """
        if self.is_paused:
            return None

        mid = market.yes_price
        cid = market.condition_id

        # get or create inventory
        inv = self._inventory.get(cid)

        # check position limit
        if inv and inv.abs_exposure >= self._config.max_position_per_market:
            return None

        # check total exposure
        if self.total_exposure >= self._config.max_total_exposure:
            return None

        # calculate half-spread — prefer LP reward band when available
        reward_half = None
        yes_band = self._reward_bands.get(market.yes_token_id, 0)
        no_band = self._reward_bands.get(market.no_token_id, 0)
        if yes_band > 0 or no_band > 0:
            band = max(yes_band, no_band)
            reward_half = band * 0.4

        half_spread = max(self._config.min_half_spread, book_spread * 0.4)
        if reward_half and reward_half > self._config.min_half_spread:
            half_spread = min(half_spread, reward_half)
        half_spread = min(half_spread, self._config.max_half_spread)

        # inventory skew: shift mid away from our inventory to encourage reducing it
        skew = 0.0
        if inv and not inv.is_flat:
            # positive net_exposure = long YES → lower bid, raise ask → encourage selling YES
            imbalance = inv.net_exposure / 10.0  # normalize to $10 units
            skew = imbalance * self._config.skew_factor

        adjusted_mid = mid - skew
        bid = round(adjusted_mid - half_spread, 4)
        ask = round(adjusted_mid + half_spread, 4)

        # clamp to valid range
        bid = max(0.01, min(bid, 0.98))
        ask = max(0.02, min(ask, 0.99))

        if ask <= bid:
            return None

        # size: reduce if inventory is building up
        size = self._config.quote_size_usd
        if inv:
            utilization = inv.abs_exposure / self._config.max_position_per_market
            if utilization > self._config.flatten_at_pct:
                size *= 0.5  # halve size when approaching limits

        bid_shares = size / bid if bid > 0 else 0
        no_price = 1.0 - ask
        ask_shares = size / no_price if no_price > 0 else 0

        return QuotePair(
            condition_id=cid,
            yes_token_id=market.yes_token_id,
            no_token_id=market.no_token_id,
            bid_price=bid,
            ask_price=ask,
            bid_size=round(bid_shares, 2),
            ask_size=round(ask_shares, 2),
            spread=round(ask - bid, 4),
            reason=f"mid={mid:.2f} skew={skew:+.3f} hs={half_spread:.3f}{' LP' if reward_half else ''}",
        )

    def on_fill(self, condition_id: str, question: str, yes_tid: str, no_tid: str,
                side: str, token_id: str, price: float, size: float) -> None:
        """Record a fill and update inventory."""
        if condition_id not in self._inventory:
            self._inventory[condition_id] = MarketInventory(
                condition_id=condition_id,
                question=question,
                yes_token_id=yes_tid,
                no_token_id=no_tid,
            )

        inv = self._inventory[condition_id]
        inv.last_trade_ts = time.monotonic()
        inv.trade_count += 1
        self._total_trades += 1

        is_yes = (token_id == yes_tid)

        if side == "BUY":
            if is_yes:
                new_size = inv.yes_shares + size
                if inv.yes_shares > 0:
                    inv.yes_avg_price = (inv.yes_avg_price * inv.yes_shares + price * size) / new_size
                else:
                    inv.yes_avg_price = price
                inv.yes_shares = new_size
            else:
                new_size = inv.no_shares + size
                if inv.no_shares > 0:
                    inv.no_avg_price = (inv.no_avg_price * inv.no_shares + price * size) / new_size
                else:
                    inv.no_avg_price = price
                inv.no_shares = new_size
        elif side == "SELL":
            if is_yes:
                pnl = (price - inv.yes_avg_price) * min(size, inv.yes_shares)
                inv.realized_pnl += pnl
                self._daily_pnl += pnl
                inv.yes_shares = max(0, inv.yes_shares - size)
                if pnl > 0:
                    self._winning_trades += 1
                    self._consecutive_losses = 0
                else:
                    self._consecutive_losses += 1
            else:
                pnl = (price - inv.no_avg_price) * min(size, inv.no_shares)
                inv.realized_pnl += pnl
                self._daily_pnl += pnl
                inv.no_shares = max(0, inv.no_shares - size)
                if pnl > 0:
                    self._winning_trades += 1
                    self._consecutive_losses = 0
                else:
                    self._consecutive_losses += 1

        # check consecutive loss pause
        if self._consecutive_losses >= self._config.max_consecutive_losses:
            self._paused_until = time.monotonic() + self._config.pause_after_loss_seconds
            log.warning(
                f"Pausing {self._config.pause_after_loss_seconds}s after "
                f"{self._consecutive_losses} consecutive losses"
            )

    def get_mergeable_positions(self) -> list[tuple[MarketInventory, float]]:
        """Find positions where we hold both YES and NO — merge to free capital."""
        mergeable = []
        for inv in self._inventory.values():
            merge_size = min(inv.yes_shares, inv.no_shares)
            if merge_size >= 1.0:
                mergeable.append((inv, merge_size))
        return mergeable

    def record_merge(self, condition_id: str, size: float) -> None:
        """Record a merge of YES+NO shares back to USDC."""
        inv = self._inventory.get(condition_id)
        if not inv:
            return
        merge = min(size, inv.yes_shares, inv.no_shares)
        inv.yes_shares -= merge
        inv.no_shares -= merge

    def get_stale_positions(self) -> list[MarketInventory]:
        """Get positions that have been held too long and need flattening."""
        stale = []
        for inv in self._inventory.values():
            if inv.is_flat:
                continue
            if inv.seconds_since_trade > self._config.max_inventory_age_seconds:
                stale.append(inv)
        return stale

    def flatten_inventory(self, condition_id: str) -> Optional[dict]:
        """Generate a flatten order for a stale position.

        Returns dict with order params, or None if position is already flat.
        """
        inv = self._inventory.get(condition_id)
        if not inv or inv.is_flat:
            return None

        # flatten whichever side has more shares
        if inv.yes_shares > inv.no_shares:
            return {
                "token_id": inv.yes_token_id,
                "side": "SELL",
                "size": inv.yes_shares,
                "reason": f"Auto-flatten YES {inv.yes_shares:.1f} shares after {inv.seconds_since_trade:.0f}s",
            }
        elif inv.no_shares > 0:
            return {
                "token_id": inv.no_token_id,
                "side": "SELL",
                "size": inv.no_shares,
                "reason": f"Auto-flatten NO {inv.no_shares:.1f} shares after {inv.seconds_since_trade:.0f}s",
            }
        return None

    def status(self) -> dict:
        active = [inv for inv in self._inventory.values() if not inv.is_flat]
        return {
            "daily_pnl": round(self._daily_pnl, 4),
            "total_exposure": round(self.total_exposure, 2),
            "active_markets": len(active),
            "total_trades": self._total_trades,
            "win_rate": round(self._winning_trades / self._total_trades, 4) if self._total_trades > 0 else 0,
            "consecutive_losses": self._consecutive_losses,
            "is_paused": self.is_paused,
            "positions": [
                {
                    "market": inv.question[:40],
                    "yes": round(inv.yes_shares, 2),
                    "no": round(inv.no_shares, 2),
                    "exposure": round(inv.net_exposure, 2),
                    "pnl": round(inv.realized_pnl, 4),
                    "age_s": round(inv.seconds_since_trade, 0),
                }
                for inv in active
            ],
        }

    def reset_daily(self) -> None:
        """Reset daily counters (call at midnight UTC)."""
        self._daily_pnl = 0.0
        self._consecutive_losses = 0
        self._paused_until = 0.0
