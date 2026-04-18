from __future__ import annotations

from dataclasses import dataclass

from src.hl_client.rest import HLRestClient
from src.hl_client.types import AccountState
from src.logger import get_logger
from src.strategy.base import Signal

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

        self._peak_equity: float = 0.0
        self._halted = False

    @property
    def is_halted(self) -> bool:
        return self._halted

    def check_signal(self, signal: Signal, account: AccountState) -> RiskCheck:
        if self._halted:
            return RiskCheck(passed=False, reason="Trading halted by risk manager")

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
            if account.margin_utilization >= self._margin_halt:
                self._halted = True
                alerts.append(f"HALT: {margin_check.reason}")
            else:
                alerts.append(f"WARNING: {margin_check.reason}")

        return alerts

    def reset_halt(self) -> None:
        self._halted = False
        log.info("Risk halt manually reset")

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
            return RiskCheck(
                passed=False,
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
