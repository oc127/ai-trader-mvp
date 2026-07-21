"""Market scanner — find trading opportunities across Polymarket markets."""

from __future__ import annotations

import math
from typing import Optional

from src.logger import get_logger
from src.polymarket.client import PolymarketClient
from src.polymarket.types import Market, Opportunity, Outcome, Side

log = get_logger(__name__)


class MarketScanner:
    """Scans markets for +EV opportunities based on edge detection."""

    def __init__(self, client: PolymarketClient, cfg: dict) -> None:
        self._client = client
        pm_cfg = cfg.get("polymarket", {})
        scan_cfg = pm_cfg.get("scanner", {})

        self._min_edge = scan_cfg.get("min_edge", 0.05)  # 5% minimum edge
        self._min_volume = scan_cfg.get("min_volume", 10000)  # $10K volume
        self._min_liquidity = scan_cfg.get("min_liquidity", 5000)  # $5K liquidity
        self._max_price = scan_cfg.get("max_price", 0.90)  # skip >90c (near-certain)
        self._min_price = scan_cfg.get("min_price", 0.10)  # skip <10c (near-impossible)
        self._market_limit = scan_cfg.get("market_limit", 200)

    def scan(self, model_probs: Optional[dict[str, float]] = None) -> list[Opportunity]:
        """Scan all active markets for opportunities.

        model_probs: optional {condition_id: model_probability_yes} overrides.
        If not provided, uses heuristic edge detection (mean-reversion, mispricing signals).
        """
        markets = self._client.get_markets(
            active=True,
            limit=self._market_limit,
            min_volume=self._min_volume,
            min_liquidity=self._min_liquidity,
        )
        log.info(f"Scanned {len(markets)} markets (vol>=${self._min_volume}, liq>=${self._min_liquidity})")

        opportunities: list[Opportunity] = []
        for market in markets:
            opps = self._evaluate_market(market, model_probs)
            opportunities.extend(opps)

        opportunities.sort(key=lambda x: abs(x.ev), reverse=True)
        log.info(f"Found {len(opportunities)} opportunities")
        return opportunities

    def _evaluate_market(
        self, market: Market, model_probs: Optional[dict[str, float]]
    ) -> list[Opportunity]:
        if not market.active:
            return []

        yes_price = market.yes_price
        no_price = market.no_price

        if yes_price < self._min_price or yes_price > self._max_price:
            return []

        opps: list[Opportunity] = []

        if model_probs and market.condition_id in model_probs:
            model_yes = model_probs[market.condition_id]
        else:
            model_yes = self._heuristic_prob(market)

        if model_yes is None:
            return []

        # check YES side
        yes_edge = model_yes - yes_price
        if yes_edge >= self._min_edge:
            opp = self._build_opportunity(market, Outcome.YES, Side.BUY, model_yes, yes_price, yes_edge)
            if opp:
                opps.append(opp)

        # check NO side
        model_no = 1.0 - model_yes
        no_edge = model_no - no_price
        if no_edge >= self._min_edge:
            opp = self._build_opportunity(market, Outcome.NO, Side.BUY, model_no, no_price, no_edge)
            if opp:
                opps.append(opp)

        return opps

    def _heuristic_prob(self, market: Market) -> Optional[float]:
        """Heuristic edge detection for thin markets at extreme prices.

        Only fires on markets with very low volume-to-liquidity ratio
        (suggesting stale or manipulated pricing) AND sufficient liquidity
        (so we can exit the position).

        Returns None if no actionable signal.
        """
        yes_price = market.yes_price

        if market.liquidity <= 0:
            return None

        vol_liq = market.volume / market.liquidity

        # Well-priced markets: high volume near 50/50 — skip
        if 0.35 <= yes_price <= 0.65 and vol_liq > 3:
            return None

        # Need minimum liquidity to enter/exit safely
        if market.liquidity < 5000:
            return None

        # Only fire on VERY thin markets (vol/liq < 1.5) at extreme prices
        if vol_liq < 1.5 and (yes_price < 0.15 or yes_price > 0.85):
            # Scale reversion by how extreme the price is
            if yes_price < 0.15:
                depth = (0.15 - yes_price) / 0.15
                reversion = 0.08 + 0.07 * depth
                return yes_price + reversion
            if yes_price > 0.85:
                depth = (yes_price - 0.85) / 0.15
                reversion = 0.08 + 0.07 * depth
                return yes_price - reversion

        return None

    def _build_opportunity(
        self,
        market: Market,
        outcome: Outcome,
        side: Side,
        model_prob: float,
        market_prob: float,
        edge: float,
    ) -> Optional[Opportunity]:
        if edge <= 0:
            return None

        # EV per dollar: E[payoff] - cost = model_prob * $1 - market_prob
        ev = model_prob - market_prob

        # Kelly: f* = (bp - q) / b where b = payoff/cost = (1-price)/price for prediction markets
        if market_prob > 0 and market_prob < 1:
            b = (1.0 - market_prob) / market_prob
            q = 1.0 - model_prob
            kelly = (b * model_prob - q) / b if b > 0 else 0.0
            kelly = max(kelly, 0.0)
        else:
            kelly = 0.0

        # confidence based on edge size and market depth
        if edge > 0.15 and market.liquidity > 20000:
            confidence = "high"
        elif edge > 0.08:
            confidence = "medium"
        else:
            confidence = "low"

        return Opportunity(
            market=market,
            outcome=outcome,
            side=side,
            model_prob=round(model_prob, 4),
            market_prob=round(market_prob, 4),
            edge=round(edge, 4),
            ev=round(ev, 4),
            kelly_fraction=round(kelly, 6),
            confidence=confidence,
            reason=f"{outcome.value}@{market_prob:.0%} vs model {model_prob:.0%}, edge={edge:.1%}",
        )

    def format_scan_report(self, opportunities: list[Opportunity], top_n: int = 10) -> str:
        lines = [
            "# Polymarket Scan Report",
            "",
            f"Total opportunities: {len(opportunities)}",
            "",
        ]
        if not opportunities:
            lines.append("No actionable opportunities found.")
            return "\n".join(lines)

        lines.extend([
            f"## Top {min(top_n, len(opportunities))} Opportunities",
            "",
            "| # | Market | Side | Price | Model | Edge | EV | Kelly | Conf |",
            "|---|---|---|---:|---:|---:|---:|---:|---|",
        ])
        for i, opp in enumerate(opportunities[:top_n], 1):
            q = opp.market.question[:50] + "..." if len(opp.market.question) > 50 else opp.market.question
            lines.append(
                f"| {i} | {q} | {opp.outcome.value} "
                f"| {opp.market_prob:.0%} | {opp.model_prob:.0%} "
                f"| {opp.edge:+.1%} | {opp.ev:+.4f} "
                f"| {opp.kelly_fraction:.1%} | {opp.confidence} |"
            )
        lines.append("")
        return "\n".join(lines)
