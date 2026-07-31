from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.logger import get_logger

logger = get_logger(__name__)


@dataclass
class RiskCheck:
    allowed: bool
    reasons: list[str] = field(default_factory=list)


@dataclass
class HaltRisk:
    level: str
    action: str
    reason: str = ""


class RiskManager:
    def __init__(self, config: dict[str, Any]) -> None:
        rc = config.get("risk", {})
        self.max_daily_loss: float = rc.get("max_daily_loss", 10000)
        self.max_daily_loss_pct: float = rc.get("max_daily_loss_pct", 0.005)
        self.max_single_loss_pct: float = rc.get("max_single_loss_pct", 0.01)
        self.max_daily_trades: int = rc.get("max_daily_trades", 200)
        self.halt_after_consecutive_losses: int = rc.get("halt_after_consecutive_losses", 3)
        self.max_capital_usage: float = rc.get("max_capital_usage", 0.80)
        self.max_per_bond: float = rc.get("max_per_bond", 200000)

        pc = config.get("position", {})
        self.total_capital: float = pc.get("total_capital", 2_000_000)
        self.max_positions: int = pc.get("max_positions", 10)
        self.liquidity_limit_pct: float = pc.get("liquidity_limit_pct", 0.01)

        self.daily_pnl: float = 0.0
        self.trade_count: int = 0
        self.consecutive_losses: int = 0
        self.is_halted: bool = False

    def reset_daily(self) -> None:
        self.daily_pnl = 0.0
        self.trade_count = 0
        self.consecutive_losses = 0
        self.is_halted = False

    def check_pre_trade(self, current_positions: int, used_capital: float) -> RiskCheck:
        reasons = []

        if self.is_halted:
            reasons.append("Trading halted by risk manager")

        if self.daily_pnl < -self.max_daily_loss:
            reasons.append(f"Daily loss limit: {self.daily_pnl:.0f} < -{self.max_daily_loss:.0f}")

        loss_pct = abs(self.daily_pnl) / self.total_capital if self.daily_pnl < 0 else 0
        if loss_pct > self.max_daily_loss_pct:
            reasons.append(f"Daily loss pct: {loss_pct:.4f} > {self.max_daily_loss_pct:.4f}")

        if self.trade_count >= self.max_daily_trades:
            reasons.append(f"Max trades reached: {self.trade_count}")

        if self.consecutive_losses >= self.halt_after_consecutive_losses:
            reasons.append(f"Consecutive losses: {self.consecutive_losses}")

        if current_positions >= self.max_positions:
            reasons.append(f"Max positions reached: {current_positions}")

        available = self.total_capital * self.max_capital_usage - used_capital
        if available <= 0:
            reasons.append(f"Capital usage exceeded: used {used_capital:.0f}")

        allowed = len(reasons) == 0
        if not allowed:
            logger.warning("Trade blocked: %s", "; ".join(reasons))
        return RiskCheck(allowed=allowed, reasons=reasons)

    def calc_position_size(
        self,
        signal_strength: float,
        current_price: float,
        daily_volume_cny: float,
        used_capital: float,
    ) -> int:
        available = self.total_capital * self.max_capital_usage - used_capital
        if available <= 0 or current_price <= 0:
            return 0

        base_pct = min(abs(signal_strength) / 3.0, 1.0)
        base_amount = self.max_per_bond * base_pct

        liq_limit = daily_volume_cny * self.liquidity_limit_pct
        amount = min(base_amount, liq_limit, available)

        shares = int(amount / current_price / 10) * 10
        return max(shares, 0)

    def on_trade_close(self, pnl: float) -> None:
        self.daily_pnl += pnl
        self.trade_count += 1

        if pnl < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0

        if self.daily_pnl < -self.max_daily_loss:
            self.is_halted = True
            logger.warning("Trading HALTED: daily loss %.0f exceeded limit %.0f", self.daily_pnl, self.max_daily_loss)

    def check_halt_risk(self, current_price: float, open_price: float) -> HaltRisk:
        if open_price <= 0:
            return HaltRisk(level="LOW", action="NORMAL")

        change_pct = (current_price - open_price) / open_price

        if abs(change_pct) > 0.25:
            return HaltRisk(
                level="CRITICAL",
                action="CLOSE_IMMEDIATELY",
                reason=f"Near 30% halt: {change_pct:.1%}",
            )
        elif abs(change_pct) > 0.18:
            return HaltRisk(
                level="HIGH",
                action="NO_NEW_POSITIONS",
                reason=f"Near 20% halt: {change_pct:.1%}",
            )
        return HaltRisk(level="LOW", action="NORMAL")

    def should_close_all(self, hour: int, minute: int) -> bool:
        return hour == 14 and minute >= 50
