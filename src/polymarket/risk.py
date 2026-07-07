"""Risk management for Polymarket bot."""

from __future__ import annotations

from dataclasses import dataclass

from src.logger import get_logger
from src.polymarket.types import BotState, Opportunity

log = get_logger(__name__)


@dataclass
class RiskCheck:
    passed: bool
    reason: str = ""


class PolymarketRiskManager:
    """Position limits, exposure caps, and circuit breakers for the PM bot."""

    def __init__(self, cfg: dict) -> None:
        pm_cfg = cfg.get("polymarket", {})
        risk_cfg = pm_cfg.get("risk", {})

        self._max_position_usd = risk_cfg.get("max_position_usd", 500)
        self._max_total_exposure = risk_cfg.get("max_total_exposure_usd", 2000)
        self._max_single_market_pct = risk_cfg.get("max_single_market_pct", 0.20)
        self._max_daily_trades = risk_cfg.get("max_daily_trades", 50)
        self._max_daily_loss = risk_cfg.get("max_daily_loss_usd", 500)
        self._min_kelly = risk_cfg.get("min_kelly", 0.02)
        self._max_kelly = risk_cfg.get("max_kelly", 0.10)  # cap at 10% even if Kelly says more
        self._min_edge = risk_cfg.get("min_edge", 0.05)
        self._min_confidence = risk_cfg.get("min_confidence", "low")
        self._max_errors = risk_cfg.get("max_errors_before_halt", 10)

    def check_opportunity(
        self, opp: Opportunity, state: BotState, current_exposure: float, balance: float,
    ) -> RiskCheck:
        """Check if an opportunity passes risk filters."""
        if state.halted:
            return RiskCheck(False, f"Bot halted: {state.halt_reason}")

        if state.trades_today >= self._max_daily_trades:
            return RiskCheck(False, f"Daily trade limit ({self._max_daily_trades})")

        if opp.edge < self._min_edge:
            return RiskCheck(False, f"Edge {opp.edge:.1%} below minimum {self._min_edge:.1%}")

        if opp.kelly_fraction < self._min_kelly:
            return RiskCheck(False, f"Kelly {opp.kelly_fraction:.1%} below minimum {self._min_kelly:.1%}")

        conf_levels = {"low": 0, "medium": 1, "high": 2}
        min_conf = conf_levels.get(self._min_confidence, 0)
        opp_conf = conf_levels.get(opp.confidence, 0)
        if opp_conf < min_conf:
            return RiskCheck(False, f"Confidence '{opp.confidence}' below '{self._min_confidence}'")

        if current_exposure >= self._max_total_exposure:
            return RiskCheck(False, f"Total exposure ${current_exposure:.0f} >= limit ${self._max_total_exposure:.0f}")

        return RiskCheck(True)

    def size_position(self, opp: Opportunity, balance: float, current_exposure: float) -> float:
        """Calculate position size in USDC."""
        kelly = min(opp.kelly_fraction, self._max_kelly)
        kelly = max(kelly, 0.0)

        # half-Kelly for safety
        size = balance * kelly / 2.0

        # cap at single position limit
        size = min(size, self._max_position_usd)

        # cap at remaining exposure room
        remaining_room = max(self._max_total_exposure - current_exposure, 0)
        size = min(size, remaining_room)

        # cap at single market percentage of portfolio
        max_market = balance * self._max_single_market_pct
        size = min(size, max_market)

        # floor at $1 (Polymarket minimum)
        if size < 1.0:
            return 0.0

        return round(size, 2)

    def check_circuit_breakers(self, state: BotState, current_pnl: float) -> RiskCheck:
        """Check if circuit breakers should trigger a halt."""
        if current_pnl < -self._max_daily_loss:
            state.halted = True
            state.halt_reason = f"Daily loss ${current_pnl:.2f} exceeds limit ${self._max_daily_loss:.2f}"
            log.warning(f"CIRCUIT BREAKER: {state.halt_reason}")
            return RiskCheck(False, state.halt_reason)

        if state.errors_today >= self._max_errors:
            state.halted = True
            state.halt_reason = f"Error count {state.errors_today} >= {self._max_errors}"
            log.warning(f"CIRCUIT BREAKER: {state.halt_reason}")
            return RiskCheck(False, state.halt_reason)

        return RiskCheck(True)
