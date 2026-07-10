"""Complete-set arbitrage + resolution sniping for Polymarket.

Complete-set arb: YES + NO < $1.00 → buy both → guaranteed profit at settlement.
Resolution snipe: outcome near-certain (>95c) but not yet $1.00 → buy for free money.

Both are pure-math strategies with near-zero directional risk.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.logger import get_logger
from src.polymarket.types import Market

log = get_logger(__name__)

POLYMARKET_FEE_PCT = 0.02  # 2% fee on winnings


@dataclass
class ArbOpportunity:
    market: Market
    yes_cost: float
    no_cost: float
    total_cost: float
    gross_profit: float   # $1.00 - total_cost
    net_profit: float     # after fees
    roi_pct: float
    arb_type: str         # "complete_set" or "resolution_snipe"
    snipe_side: str = ""  # "YES" or "NO" for snipe


@dataclass
class ArbConfig:
    min_net_profit: float = 0.005    # $0.005 minimum net profit per arb
    min_roi_pct: float = 0.3         # 0.3% minimum ROI
    max_arb_size_usd: float = 50.0   # max per arb trade
    min_liquidity: float = 5000      # skip illiquid markets
    min_volume: float = 1000
    snipe_min_price: float = 0.95    # minimum price to snipe (near-certain)
    snipe_max_price: float = 0.995   # don't buy at $1.00
    scan_limit: int = 200


def load_arb_config(cfg: dict) -> ArbConfig:
    arb = cfg.get("polymarket", {}).get("arbitrage", {})
    return ArbConfig(
        min_net_profit=arb.get("min_net_profit", 0.005),
        min_roi_pct=arb.get("min_roi_pct", 0.3),
        max_arb_size_usd=arb.get("max_arb_size_usd", 50.0),
        min_liquidity=arb.get("min_liquidity", 5000),
        min_volume=arb.get("min_volume", 1000),
        snipe_min_price=arb.get("snipe_min_price", 0.95),
        snipe_max_price=arb.get("snipe_max_price", 0.995),
        scan_limit=arb.get("scan_limit", 200),
    )


class ArbitrageEngine:
    """Scan markets for complete-set arbitrage and resolution sniping opportunities."""

    def __init__(self, cfg: dict) -> None:
        self._config = load_arb_config(cfg)
        self._arb_pnl = 0.0
        self._arb_count = 0
        self._snipe_pnl = 0.0
        self._snipe_count = 0

    @property
    def config(self) -> ArbConfig:
        return self._config

    @property
    def total_pnl(self) -> float:
        return self._arb_pnl + self._snipe_pnl

    def scan_arbs(self, markets: list[Market]) -> list[ArbOpportunity]:
        """Find complete-set arbitrage: YES + NO < $1.00."""
        opportunities: list[ArbOpportunity] = []

        for m in markets:
            if m.liquidity < self._config.min_liquidity:
                continue
            if m.volume < self._config.min_volume:
                continue

            yes_cost = m.yes_price
            no_cost = m.no_price
            total_cost = yes_cost + no_cost

            if total_cost >= 1.0:
                continue

            gross_profit = 1.0 - total_cost
            # fee applies to the winning side ($1.00 payout)
            fee = POLYMARKET_FEE_PCT * 1.0
            net_profit = gross_profit - fee

            if net_profit < self._config.min_net_profit:
                continue

            roi_pct = (net_profit / total_cost) * 100 if total_cost > 0 else 0

            if roi_pct < self._config.min_roi_pct:
                continue

            opportunities.append(ArbOpportunity(
                market=m,
                yes_cost=yes_cost,
                no_cost=no_cost,
                total_cost=total_cost,
                gross_profit=gross_profit,
                net_profit=net_profit,
                roi_pct=roi_pct,
                arb_type="complete_set",
            ))

        opportunities.sort(key=lambda o: o.roi_pct, reverse=True)
        if opportunities:
            log.info(f"Arb scan: {len(opportunities)} complete-set opportunities found")
        return opportunities

    def scan_snipes(self, markets: list[Market]) -> list[ArbOpportunity]:
        """Find resolution sniping: near-certain outcomes priced < $1.00."""
        opportunities: list[ArbOpportunity] = []
        cfg = self._config

        for m in markets:
            if m.liquidity < cfg.min_liquidity:
                continue

            for side, price in [("YES", m.yes_price), ("NO", m.no_price)]:
                if price < cfg.snipe_min_price or price > cfg.snipe_max_price:
                    continue

                cost = price
                gross_profit = 1.0 - cost
                fee = POLYMARKET_FEE_PCT * 1.0
                net_profit = gross_profit - fee

                if net_profit < cfg.min_net_profit:
                    continue

                roi_pct = (net_profit / cost) * 100 if cost > 0 else 0

                opportunities.append(ArbOpportunity(
                    market=m,
                    yes_cost=m.yes_price,
                    no_cost=m.no_price,
                    total_cost=cost,
                    gross_profit=gross_profit,
                    net_profit=net_profit,
                    roi_pct=roi_pct,
                    arb_type="resolution_snipe",
                    snipe_side=side,
                ))

        opportunities.sort(key=lambda o: o.roi_pct, reverse=True)
        if opportunities:
            log.info(f"Snipe scan: {len(opportunities)} resolution snipe opportunities")
        return opportunities

    def scan_all(self, markets: list[Market]) -> list[ArbOpportunity]:
        arbs = self.scan_arbs(markets)
        snipes = self.scan_snipes(markets)
        combined = arbs + snipes
        combined.sort(key=lambda o: o.net_profit, reverse=True)
        return combined

    def record_fill(self, opp: ArbOpportunity, filled_size: float, fill_price: float) -> None:
        """Record a completed arb/snipe trade for PnL tracking."""
        pnl = opp.net_profit * filled_size
        if opp.arb_type == "complete_set":
            self._arb_pnl += pnl
            self._arb_count += 1
        else:
            self._snipe_pnl += pnl
            self._snipe_count += 1

    def status(self) -> dict:
        return {
            "arb_pnl": round(self._arb_pnl, 4),
            "arb_count": self._arb_count,
            "snipe_pnl": round(self._snipe_pnl, 4),
            "snipe_count": self._snipe_count,
            "total_arb_pnl": round(self.total_pnl, 4),
        }

    def reset_daily(self) -> None:
        self._arb_pnl = 0.0
        self._snipe_pnl = 0.0
        self._arb_count = 0
        self._snipe_count = 0
