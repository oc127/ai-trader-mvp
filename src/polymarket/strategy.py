"""Trading strategies for Polymarket prediction markets."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from src.logger import get_logger
from src.polymarket.client import PolymarketClient
from src.polymarket.types import Market, Opportunity, Outcome, Side

log = get_logger(__name__)


class PMStrategy(ABC):
    """Base strategy interface for Polymarket."""

    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def evaluate(self, markets: list[Market]) -> list[Opportunity]: ...


class EdgeStrategy(PMStrategy):
    """Value-based edge detection.

    Compares market price to a model probability estimate.
    In auto mode, uses a simple heuristic; can be overridden with
    external model probabilities (e.g., from an LLM or statistical model).
    """

    def __init__(self, cfg: dict, model_probs: Optional[dict[str, float]] = None) -> None:
        pm_cfg = cfg.get("polymarket", {})
        strat_cfg = pm_cfg.get("strategy", {})
        self._min_edge = strat_cfg.get("min_edge", 0.05)
        self._model_probs = model_probs or {}

    def name(self) -> str:
        return "edge"

    def evaluate(self, markets: list[Market]) -> list[Opportunity]:
        opps: list[Opportunity] = []
        for market in markets:
            if not market.active:
                continue
            opps.extend(self._check_market(market))
        return opps

    def _check_market(self, market: Market) -> list[Opportunity]:
        opps: list[Opportunity] = []
        yes_price = market.yes_price

        if market.condition_id in self._model_probs:
            model_yes = self._model_probs[market.condition_id]
        else:
            return []

        model_no = 1.0 - model_yes

        # YES side
        yes_edge = model_yes - yes_price
        if yes_edge >= self._min_edge:
            opps.append(self._make_opp(market, Outcome.YES, Side.BUY, model_yes, yes_price, yes_edge))

        # NO side
        no_edge = model_no - market.no_price
        if no_edge >= self._min_edge:
            opps.append(self._make_opp(market, Outcome.NO, Side.BUY, model_no, market.no_price, no_edge))

        return opps

    def _make_opp(
        self, market: Market, outcome: Outcome, side: Side,
        model_prob: float, market_prob: float, edge: float,
    ) -> Opportunity:
        b = (1.0 - market_prob) / market_prob if 0 < market_prob < 1 else 0
        kelly = max((b * model_prob - (1 - model_prob)) / b, 0) if b > 0 else 0
        ev = model_prob - market_prob
        conf = "high" if edge > 0.15 else "medium" if edge > 0.08 else "low"
        return Opportunity(
            market=market, outcome=outcome, side=side,
            model_prob=round(model_prob, 4), market_prob=round(market_prob, 4),
            edge=round(edge, 4), ev=round(ev, 4), kelly_fraction=round(kelly, 6),
            confidence=conf,
            reason=f"{outcome.value}@{market_prob:.0%} vs model {model_prob:.0%}",
        )

    def set_model_probs(self, probs: dict[str, float]) -> None:
        self._model_probs.update(probs)


class MeanReversionStrategy(PMStrategy):
    """Mean-reversion on thin markets.

    Bets that markets with low volume-to-liquidity ratios at extreme prices
    will revert toward more moderate probabilities.
    """

    def __init__(self, cfg: dict) -> None:
        pm_cfg = cfg.get("polymarket", {})
        strat_cfg = pm_cfg.get("strategy", {})
        self._reversion_size = strat_cfg.get("reversion_size", 0.08)
        self._vol_liq_threshold = strat_cfg.get("vol_liq_threshold", 2.0)
        self._extreme_low = strat_cfg.get("extreme_low", 0.15)
        self._extreme_high = strat_cfg.get("extreme_high", 0.85)

    def name(self) -> str:
        return "mean_reversion"

    def evaluate(self, markets: list[Market]) -> list[Opportunity]:
        opps: list[Opportunity] = []
        for market in markets:
            if not market.active or market.liquidity <= 0:
                continue
            vol_liq = market.volume / market.liquidity
            if vol_liq >= self._vol_liq_threshold:
                continue

            yes_price = market.yes_price

            if yes_price < self._extreme_low:
                model_yes = yes_price + self._reversion_size
                edge = model_yes - yes_price
                opp = self._make_opp(market, Outcome.YES, model_yes, yes_price, edge)
                if opp:
                    opps.append(opp)
            elif yes_price > self._extreme_high:
                model_no = (1.0 - yes_price) + self._reversion_size
                no_price = 1.0 - yes_price
                edge = model_no - no_price
                opp = self._make_opp(market, Outcome.NO, model_no, no_price, edge)
                if opp:
                    opps.append(opp)

        return opps

    def _make_opp(
        self, market: Market, outcome: Outcome,
        model_prob: float, market_prob: float, edge: float,
    ) -> Optional[Opportunity]:
        if edge <= 0 or market_prob <= 0 or market_prob >= 1:
            return None
        b = (1.0 - market_prob) / market_prob
        kelly = max((b * model_prob - (1 - model_prob)) / b, 0) if b > 0 else 0
        ev = model_prob - market_prob
        return Opportunity(
            market=market, outcome=outcome, side=Side.BUY,
            model_prob=round(model_prob, 4), market_prob=round(market_prob, 4),
            edge=round(edge, 4), ev=round(ev, 4), kelly_fraction=round(kelly, 6),
            confidence="low",
            reason=f"Mean reversion: thin market at {market_prob:.0%}",
        )


class MarketMakerStrategy(PMStrategy):
    """Provide liquidity by quoting both sides with a spread.

    Earns the spread when both sides fill. Risk: directional exposure
    if only one side fills.
    """

    def __init__(self, cfg: dict) -> None:
        pm_cfg = cfg.get("polymarket", {})
        mm_cfg = pm_cfg.get("market_maker", {})
        self._half_spread = mm_cfg.get("half_spread", 0.02)
        self._min_liquidity = mm_cfg.get("min_liquidity", 10000)
        self._price_band_low = mm_cfg.get("price_band_low", 0.25)
        self._price_band_high = mm_cfg.get("price_band_high", 0.75)

    def name(self) -> str:
        return "market_maker"

    def evaluate(self, markets: list[Market]) -> list[Opportunity]:
        opps: list[Opportunity] = []
        for market in markets:
            if not market.active or market.liquidity < self._min_liquidity:
                continue
            yes_price = market.yes_price
            if not (self._price_band_low <= yes_price <= self._price_band_high):
                continue

            bid = yes_price - self._half_spread
            ask = yes_price + self._half_spread
            if bid < 0.01 or ask > 0.99:
                continue

            # bid side (buy YES below mid)
            opps.append(Opportunity(
                market=market, outcome=Outcome.YES, side=Side.BUY,
                model_prob=round(yes_price, 4), market_prob=round(bid, 4),
                edge=round(self._half_spread, 4), ev=round(self._half_spread, 4),
                kelly_fraction=0.01, confidence="low",
                reason=f"MM bid YES@{bid:.2f}",
            ))
            # ask side (sell YES above mid = buy NO)
            opps.append(Opportunity(
                market=market, outcome=Outcome.NO, side=Side.BUY,
                model_prob=round(1 - yes_price, 4), market_prob=round(1 - ask, 4),
                edge=round(self._half_spread, 4), ev=round(self._half_spread, 4),
                kelly_fraction=0.01, confidence="low",
                reason=f"MM ask YES@{ask:.2f}",
            ))

        return opps
