from __future__ import annotations

from datetime import datetime
from typing import Any

from src.models import Action, Signal


class CompositeStrategy:
    def __init__(self, config: dict[str, Any]) -> None:
        sc = config.get("strategy", {})
        self.weights: dict[str, float] = sc.get(
            "weights",
            {
                "stock_lead": 0.30,
                "premium_revert": 0.25,
                "intraday_regime": 0.20,
                "volume_anomaly": 0.15,
                "redemption": 0.10,
            },
        )
        thresholds = sc.get("thresholds", {})
        self.strong_buy: float = thresholds.get("strong_buy", 1.5)
        self.buy: float = thresholds.get("buy", 0.8)
        self.sell: float = thresholds.get("sell", -0.8)
        self.strong_sell: float = thresholds.get("strong_sell", -1.5)

    def combine(
        self,
        code: str,
        timestamp: datetime,
        stock_lead: float,
        premium_revert: float,
        intraday_regime: float,
        volume_anomaly: float,
        redemption: float,
    ) -> Signal:
        composite = (
            self.weights.get("stock_lead", 0.30) * stock_lead
            + self.weights.get("premium_revert", 0.25) * premium_revert
            + self.weights.get("intraday_regime", 0.20) * intraday_regime
            + self.weights.get("volume_anomaly", 0.15) * volume_anomaly
            + self.weights.get("redemption", 0.10) * redemption
        )

        action = self._determine_action(composite)

        return Signal(
            code=code,
            timestamp=timestamp,
            stock_lead=stock_lead,
            premium_revert=premium_revert,
            intraday_regime=intraday_regime,
            volume_anomaly=volume_anomaly,
            redemption=redemption,
            composite=composite,
            action=action,
        )

    def _determine_action(self, composite: float) -> Action:
        if composite >= self.strong_buy:
            return Action.STRONG_BUY
        elif composite >= self.buy:
            return Action.BUY
        elif composite <= self.strong_sell:
            return Action.STRONG_SELL
        elif composite <= self.sell:
            return Action.SELL
        return Action.HOLD
