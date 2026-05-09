from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.hl_client.rest import HLRestClient
from src.hl_client.types import AccountState
from src.logger import get_logger
from src.strategy.base import Signal

if TYPE_CHECKING:
    from src.strategy.market_maker import HLMarketMaker

log = get_logger(__name__)


@dataclass
class RiskCheck:
    passed: bool
    reason: str = ""


class RiskManager:
    def __init__(self, client: HLRestClient, cfg: dict) -> None:
        self._client = client

        risk_cfg = cfg.get("risk", {})
        self._max_drawdown = risk_cfg.get("max_drawdown_pct", 0.05)
        self._margin_warn = risk_cfg.get("margin_utilization_warn", 0.70)
        self._margin_halt = risk_cfg.get("margin_utilization_halt", 0.85)
        self._max_position_usd = risk_cfg.get("max_position_size_usd", 10000)
        self._kill_switch = risk_cfg.get("kill_switch_enabled", True)

        # MM-specific risk limits
        mm_risk_cfg = risk_cfg.get("market_maker", {})
        self._mm_per_coin_limit = mm_risk_cfg.get("per_coin_inventory_usd", 50000)
        self._mm_total_exposure_limit = mm_risk_cfg.get("total_exposure_usd", 100000)
        self._mm_max_drawdown_pct = mm_risk_cfg.get("max_drawdown_pct", 0.02)

        self._peak_equity: float = 0.0
        self._halted = False
        self._daily_trade_count: int = 0
        self._consecutive_losses: int = 0
        self._halt_after_consecutive: int = risk_cfg.get("halt_after_consecutive_losses", 5)
        self._max_daily_trades: int = risk_cfg.get("max_daily_trades", 500)

        # MM-specific state
        self._mm_paused = False
        self._mm_peak_pnl: float = 0.0

    @property
    def is_halted(self) -> bool:
        return self._halted

    @property
    def is_mm_paused(self) -> bool:
        return self._mm_paused

    def on_trade_close(self, pnl: float) -> None:
        self._daily_trade_count += 1
        if pnl < 0:
            self._consecutive_losses += 1
            if self._consecutive_losses >= self._halt_after_consecutive:
                self._halted = True
                log.error(
                    "HALT: %d consecutive losses",
                    self._consecutive_losses,
                )
        else:
            self._consecutive_losses = 0

    def reset_daily(self) -> None:
        self._daily_trade_count = 0
        self._consecutive_losses = 0
        self._halted = False

    def check_signal(self, signal: Signal, account: AccountState) -> RiskCheck:
        if self._halted:
            return RiskCheck(passed=False, reason="Trading halted by risk manager")

        if self._daily_trade_count >= self._max_daily_trades:
            return RiskCheck(passed=False, reason=f"Max daily trades {self._max_daily_trades} reached")

        if self._consecutive_losses >= self._halt_after_consecutive:
            return RiskCheck(passed=False, reason=f"Consecutive losses: {self._consecutive_losses}")

        dd_check = self._check_drawdown(account)
        if not dd_check.passed:
            return dd_check

        margin_check = self._check_margin(account)
        if not margin_check.passed:
            return margin_check

        size_check = self._check_position_size(signal, account)
        if not size_check.passed:
            return size_check

        return RiskCheck(passed=True)

    def check_mm_exposure(self, coin: str, mm_strategy: HLMarketMaker) -> bool:
        """Check MM-specific risk limits.

        Args:
            coin: The coin to check.
            mm_strategy: The market maker strategy instance.

        Returns:
            True if within limits, False if exposure would be exceeded.
        """
        # Per-coin inventory limit
        coin_inv = abs(mm_strategy.get_inventory(coin))
        if coin_inv >= self._mm_per_coin_limit:
            log.warning(
                "MM per-coin inventory limit reached",
                extra={"coin": coin, "inventory": coin_inv, "limit": self._mm_per_coin_limit},
            )
            return False

        # Total MM exposure limit across all coins
        total_exposure = mm_strategy.get_total_exposure()
        if total_exposure >= self._mm_total_exposure_limit:
            log.warning(
                "MM total exposure limit reached",
                extra={"total_exposure": total_exposure, "limit": self._mm_total_exposure_limit},
            )
            return False

        return True

    def update_mm_pnl(self, mm_strategy: HLMarketMaker) -> list[str]:
        """Update MM P&L tracking and check for drawdown pause.

        Args:
            mm_strategy: The market maker strategy instance.

        Returns:
            List of alert messages.
        """
        alerts: list[str] = []
        current_pnl = mm_strategy.get_realized_pnl()

        if current_pnl > self._mm_peak_pnl:
            self._mm_peak_pnl = current_pnl

        if self._mm_peak_pnl > 0:
            mm_drawdown = (self._mm_peak_pnl - current_pnl) / self._mm_peak_pnl
            if mm_drawdown >= self._mm_max_drawdown_pct:
                self._mm_paused = True
                msg = f"MM PAUSED: drawdown {mm_drawdown:.2%} exceeds {self._mm_max_drawdown_pct:.2%}"
                alerts.append(msg)
                log.warning(msg)

        return alerts

    def reset_mm_pause(self) -> None:
        """Resume MM after manual review."""
        self._mm_paused = False
        log.info("MM pause manually reset")

    def update(self, account: AccountState) -> list[str]:
        alerts = []

        if account.equity > self._peak_equity:
            self._peak_equity = account.equity

        dd_check = self._check_drawdown(account)
        if not dd_check.passed:
            self._halted = True
            alerts.append(f"HALT: {dd_check.reason}")
            log.error("Risk halt triggered", extra={"reason": dd_check.reason})

        margin_check = self._check_margin(account)
        if not margin_check.passed:
            self._halted = True
            alerts.append(f"HALT: {margin_check.reason}")
        elif margin_check.reason:
            alerts.append(f"WARNING: {margin_check.reason}")

        return alerts

    def reset_halt(self, new_peak_equity: float = 0.0) -> None:
        self._halted = False
        if new_peak_equity > 0:
            self._peak_equity = new_peak_equity
        log.info("Risk halt manually reset", extra={"peak_equity": self._peak_equity})

    def _check_drawdown(self, account: AccountState) -> RiskCheck:
        if self._peak_equity == 0:
            self._peak_equity = account.equity
            return RiskCheck(passed=True)

        drawdown = (self._peak_equity - account.equity) / self._peak_equity
        if drawdown >= self._max_drawdown:
            return RiskCheck(
                passed=False,
                reason=f"Drawdown {drawdown:.2%} exceeds max {self._max_drawdown:.2%}",
            )
        return RiskCheck(passed=True)

    def _check_margin(self, account: AccountState) -> RiskCheck:
        util = account.margin_utilization
        if util >= self._margin_halt:
            return RiskCheck(
                passed=False,
                reason=f"Margin utilization {util:.2%} exceeds halt threshold {self._margin_halt:.2%}",
            )
        if util >= self._margin_warn:
            log.warning("Margin utilization high", extra={"utilization": f"{util:.2%}"})
            return RiskCheck(
                passed=True,
                reason=f"Margin utilization {util:.2%} exceeds warning threshold {self._margin_warn:.2%}",
            )
        return RiskCheck(passed=True)

    def _check_position_size(self, signal: Signal, account: AccountState) -> RiskCheck:
        for order in signal.orders:
            mids = self._client.get_all_mids()
            price = mids.get(order.coin, 0)
            notional = order.size * price
            if notional > self._max_position_usd:
                return RiskCheck(
                    passed=False,
                    reason=f"{order.coin} notional ${notional:.0f} exceeds max ${self._max_position_usd}",
                )
        return RiskCheck(passed=True)
