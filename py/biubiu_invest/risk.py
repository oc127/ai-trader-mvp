"""Risk management for US stock trading."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .broker import AccountInfo, Order, Position

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RiskLimits:
    max_position_pct: float = 0.10  # Max 10% of equity per position
    max_total_exposure_pct: float = 0.95  # Max 95% of equity invested
    stop_loss_pct: float = 0.05  # 5% stop loss
    max_daily_trades: int = 3  # PDT-safe for margin accounts
    min_cash_reserve: float = 500.0  # Keep at least $500 cash
    max_single_order_value: float = 2000.0  # Max $2000 per order


@dataclass(frozen=True)
class RiskCheck:
    allowed: bool
    reason: str


class RiskManager:
    def __init__(self, limits: RiskLimits = RiskLimits()):
        self.limits = limits
        self._daily_trade_count = 0

    def reset_daily_count(self) -> None:
        self._daily_trade_count = 0

    def check_order(
        self,
        order: Order,
        account: AccountInfo,
        positions: list[Position],
    ) -> RiskCheck:
        # Account blocked
        if account.account_blocked:
            return RiskCheck(False, "Account is blocked")

        # PDT flag with insufficient equity
        if account.pattern_day_trader and account.equity < 25000:
            return RiskCheck(False, "PDT flagged with equity < $25,000")

        # Daily trade limit (pre-PDT safety)
        if order.side == "buy" and self._daily_trade_count >= self.limits.max_daily_trades:
            return RiskCheck(
                False,
                f"Daily trade limit reached ({self.limits.max_daily_trades})",
            )

        if order.side == "buy":
            # Cash reserve
            if account.cash < self.limits.min_cash_reserve:
                return RiskCheck(
                    False,
                    f"Cash ${account.cash:.0f} below reserve ${self.limits.min_cash_reserve:.0f}",
                )

            # Position concentration
            current_value = sum(
                p.market_value for p in positions if p.symbol == order.symbol
            )
            max_value = account.equity * self.limits.max_position_pct
            if current_value >= max_value:
                return RiskCheck(
                    False,
                    f"{order.symbol} at max concentration "
                    f"({self.limits.max_position_pct:.0%} of equity)",
                )

            # Total exposure
            total_invested = sum(p.market_value for p in positions)
            max_exposure = account.equity * self.limits.max_total_exposure_pct
            if total_invested >= max_exposure:
                return RiskCheck(
                    False,
                    f"Total exposure at max ({self.limits.max_total_exposure_pct:.0%})",
                )

        return RiskCheck(True, "OK")

    def compute_position_size(
        self,
        symbol: str,
        price: float,
        account: AccountInfo,
        positions: list[Position],
    ) -> int:
        """Compute number of whole shares to buy, respecting risk limits."""
        if price <= 0:
            return 0

        max_by_position = account.equity * self.limits.max_position_pct
        max_by_order = self.limits.max_single_order_value
        max_by_cash = account.cash - self.limits.min_cash_reserve

        total_invested = sum(p.market_value for p in positions)
        max_by_exposure = (
            account.equity * self.limits.max_total_exposure_pct
        ) - total_invested

        # Subtract existing position value
        existing = sum(p.market_value for p in positions if p.symbol == symbol)
        max_by_position -= existing

        max_value = max(
            0, min(max_by_position, max_by_order, max_by_cash, max_by_exposure)
        )
        return max(0, int(max_value / price))

    def check_stop_losses(self, positions: list[Position]) -> list[str]:
        """Return symbols that should be sold due to stop-loss."""
        to_sell: list[str] = []
        for p in positions:
            if p.unrealized_plpc <= -self.limits.stop_loss_pct:
                log.warning(
                    "Stop-loss triggered: %s at %.2f%% loss",
                    p.symbol,
                    p.unrealized_plpc * 100,
                )
                to_sell.append(p.symbol)
        return to_sell

    def record_trade(self) -> None:
        self._daily_trade_count += 1
