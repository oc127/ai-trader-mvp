"""Risk management for Polymarket bot.

Four-layer circuit breaker hierarchy:
  Layer 1: Daily loss cap (e.g. 5% or $25)
  Layer 2: Monthly loss cap (e.g. 15% or $150)
  Layer 3: Max drawdown from equity peak (e.g. 25%)
  Layer 4: Total lifetime loss halt (e.g. 40% of initial capital)

Dynamic position sizing:
  - Reduce 20% per consecutive loss (compounds)
  - Increase 10% per consecutive win (capped at base size)
  - Streak resets on direction change
"""

from __future__ import annotations

from dataclasses import dataclass

from src.logger import get_logger
from src.polymarket.types import BotState, Opportunity

log = get_logger(__name__)


@dataclass
class RiskCheck:
    passed: bool
    reason: str = ""


@dataclass
class RiskConfig:
    max_position_usd: float = 500
    max_total_exposure: float = 2000
    max_single_market_pct: float = 0.20
    max_daily_trades: int = 50
    max_daily_loss: float = 500
    min_kelly: float = 0.02
    max_kelly: float = 0.10
    min_edge: float = 0.05
    min_confidence: str = "low"
    max_errors: int = 10

    # multi-layer circuit breakers
    max_monthly_loss: float = 1500.0
    max_drawdown_pct: float = 0.25
    max_total_loss_pct: float = 0.40

    # dynamic sizing
    loss_shrink_pct: float = 0.20
    win_grow_pct: float = 0.10
    max_streak_adjustment: float = 0.50  # floor: size can shrink to 50% of base
    min_size_usd: float = 1.0


def load_risk_config(cfg: dict) -> RiskConfig:
    rc = cfg.get("polymarket", {}).get("risk", {})
    return RiskConfig(
        max_position_usd=rc.get("max_position_usd", 500),
        max_total_exposure=rc.get("max_total_exposure_usd", 2000),
        max_single_market_pct=rc.get("max_single_market_pct", 0.20),
        max_daily_trades=rc.get("max_daily_trades", 50),
        max_daily_loss=rc.get("max_daily_loss_usd", 500),
        min_kelly=rc.get("min_kelly", 0.02),
        max_kelly=rc.get("max_kelly", 0.10),
        min_edge=rc.get("min_edge", 0.05),
        min_confidence=rc.get("min_confidence", "low"),
        max_errors=rc.get("max_errors_before_halt", 10),
        max_monthly_loss=rc.get("max_monthly_loss_usd", 1500.0),
        max_drawdown_pct=rc.get("max_drawdown_pct", 0.25),
        max_total_loss_pct=rc.get("max_total_loss_pct", 0.40),
        loss_shrink_pct=rc.get("loss_shrink_pct", 0.20),
        win_grow_pct=rc.get("win_grow_pct", 0.10),
        max_streak_adjustment=rc.get("max_streak_adjustment", 0.50),
    )


class PolymarketRiskManager:
    """Position limits, exposure caps, multi-layer circuit breakers, and dynamic sizing."""

    def __init__(self, cfg: dict) -> None:
        self._cfg = load_risk_config(cfg)

        self._max_position_usd = self._cfg.max_position_usd
        self._max_total_exposure = self._cfg.max_total_exposure
        self._max_single_market_pct = self._cfg.max_single_market_pct
        self._max_daily_trades = self._cfg.max_daily_trades
        self._max_daily_loss = self._cfg.max_daily_loss
        self._min_kelly = self._cfg.min_kelly
        self._max_kelly = self._cfg.max_kelly
        self._min_edge = self._cfg.min_edge
        self._min_confidence = self._cfg.min_confidence
        self._max_errors = self._cfg.max_errors

        # multi-layer state
        self._monthly_pnl = 0.0
        self._total_pnl = 0.0
        self._initial_capital = 0.0
        self._peak_equity = 0.0
        self._current_equity = 0.0

        # dynamic sizing streak tracker
        self._consecutive_wins = 0
        self._consecutive_losses = 0
        self._size_multiplier = 1.0

    def set_initial_capital(self, capital: float) -> None:
        self._initial_capital = capital
        self._peak_equity = capital
        self._current_equity = capital

    def update_equity(self, equity: float) -> None:
        self._current_equity = equity
        if equity > self._peak_equity:
            self._peak_equity = equity

    def record_trade_result(self, pnl: float) -> None:
        """Update PnL trackers and dynamic sizing streak."""
        self._monthly_pnl += pnl
        self._total_pnl += pnl

        if pnl > 0:
            self._consecutive_wins += 1
            self._consecutive_losses = 0
        elif pnl < 0:
            self._consecutive_losses += 1
            self._consecutive_wins = 0

        self._update_size_multiplier()

    def _update_size_multiplier(self) -> None:
        if self._consecutive_losses > 0:
            shrink = (1.0 - self._cfg.loss_shrink_pct) ** self._consecutive_losses
            self._size_multiplier = max(shrink, self._cfg.max_streak_adjustment)
        elif self._consecutive_wins > 0:
            grow = (1.0 + self._cfg.win_grow_pct) ** self._consecutive_wins
            self._size_multiplier = min(grow, 1.0 / self._cfg.max_streak_adjustment)
        else:
            self._size_multiplier = 1.0

    @property
    def size_multiplier(self) -> float:
        return self._size_multiplier

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
        """Calculate position size in USDC with dynamic streak adjustment."""
        kelly = min(opp.kelly_fraction, self._max_kelly)
        kelly = max(kelly, 0.0)

        # half-Kelly for safety
        size = balance * kelly / 2.0

        # apply dynamic streak multiplier
        size *= self._size_multiplier

        # cap at single position limit
        size = min(size, self._max_position_usd)

        # cap at remaining exposure room
        remaining_room = max(self._max_total_exposure - current_exposure, 0)
        size = min(size, remaining_room)

        # cap at single market percentage of portfolio
        max_market = balance * self._max_single_market_pct
        size = min(size, max_market)

        # floor at $1 (Polymarket minimum)
        if size < self._cfg.min_size_usd:
            return 0.0

        return round(size, 2)

    def check_circuit_breakers(self, state: BotState, current_pnl: float) -> RiskCheck:
        """Four-layer circuit breaker hierarchy."""
        # Layer 1: Daily loss
        if current_pnl < -self._max_daily_loss:
            state.halted = True
            state.halt_reason = f"Daily loss ${current_pnl:.2f} exceeds limit ${self._max_daily_loss:.2f}"
            log.warning(f"CIRCUIT BREAKER L1 (daily): {state.halt_reason}")
            return RiskCheck(False, state.halt_reason)

        # Layer 2: Monthly loss
        if self._monthly_pnl < -self._cfg.max_monthly_loss:
            state.halted = True
            state.halt_reason = (
                f"Monthly loss ${self._monthly_pnl:.2f} exceeds limit "
                f"${self._cfg.max_monthly_loss:.2f}"
            )
            log.warning(f"CIRCUIT BREAKER L2 (monthly): {state.halt_reason}")
            return RiskCheck(False, state.halt_reason)

        # Layer 3: Drawdown from peak
        if self._peak_equity > 0 and self._current_equity > 0:
            drawdown = (self._peak_equity - self._current_equity) / self._peak_equity
            if drawdown >= self._cfg.max_drawdown_pct:
                state.halted = True
                state.halt_reason = (
                    f"Drawdown {drawdown:.1%} from peak ${self._peak_equity:.2f} "
                    f"exceeds limit {self._cfg.max_drawdown_pct:.0%}"
                )
                log.warning(f"CIRCUIT BREAKER L3 (drawdown): {state.halt_reason}")
                return RiskCheck(False, state.halt_reason)

        # Layer 4: Total lifetime loss
        if self._initial_capital > 0:
            total_loss_pct = -self._total_pnl / self._initial_capital
            if total_loss_pct >= self._cfg.max_total_loss_pct:
                state.halted = True
                state.halt_reason = (
                    f"Total loss {total_loss_pct:.1%} of initial capital "
                    f"exceeds limit {self._cfg.max_total_loss_pct:.0%}"
                )
                log.warning(f"CIRCUIT BREAKER L4 (total): {state.halt_reason}")
                return RiskCheck(False, state.halt_reason)

        # Error count
        if state.errors_today >= self._max_errors:
            state.halted = True
            state.halt_reason = f"Error count {state.errors_today} >= {self._max_errors}"
            log.warning(f"CIRCUIT BREAKER (errors): {state.halt_reason}")
            return RiskCheck(False, state.halt_reason)

        return RiskCheck(True)

    def reset_daily(self) -> None:
        """Reset daily counters. Monthly/total/streak persist."""
        pass

    def reset_monthly(self) -> None:
        self._monthly_pnl = 0.0

    def status(self) -> dict:
        drawdown = 0.0
        if self._peak_equity > 0:
            drawdown = (self._peak_equity - self._current_equity) / self._peak_equity

        return {
            "monthly_pnl": round(self._monthly_pnl, 4),
            "total_pnl": round(self._total_pnl, 4),
            "peak_equity": round(self._peak_equity, 2),
            "current_equity": round(self._current_equity, 2),
            "drawdown_pct": round(drawdown * 100, 2),
            "consecutive_wins": self._consecutive_wins,
            "consecutive_losses": self._consecutive_losses,
            "size_multiplier": round(self._size_multiplier, 4),
        }
