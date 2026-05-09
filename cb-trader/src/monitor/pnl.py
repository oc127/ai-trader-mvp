from __future__ import annotations

from datetime import datetime

from src.data.store import DataStore
from src.logger import get_logger
from src.monitor.alerts import AlertManager

logger = get_logger(__name__)


class PnLTracker:
    def __init__(self, store: DataStore, alerts: AlertManager, initial_capital: float = 2_000_000) -> None:
        self._store = store
        self._alerts = alerts
        self._initial_capital = initial_capital
        self._peak_equity: float = initial_capital
        self._current_equity: float = initial_capital
        self._daily_pnl: float = 0.0
        self._total_trades: int = 0

    def update(self, equity: float, daily_pnl: float, trade_count: int) -> None:
        self._current_equity = equity
        self._daily_pnl = daily_pnl
        self._total_trades = trade_count

        if equity > self._peak_equity:
            self._peak_equity = equity

    @property
    def drawdown_pct(self) -> float:
        if self._peak_equity <= 0:
            return 0.0
        return (self._peak_equity - self._current_equity) / self._peak_equity

    @property
    def total_return_pct(self) -> float:
        if self._initial_capital <= 0:
            return 0.0
        return (self._current_equity - self._initial_capital) / self._initial_capital

    def build_daily_summary(self) -> str:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        return_pct = self.total_return_pct * 100
        dd_pct = self.drawdown_pct * 100
        lines = [
            f"*CB T+0 日报 — {now}*",
            "",
            f"权益: ¥{self._current_equity:,.0f}",
            f"日盈亏: ¥{self._daily_pnl:+,.0f}",
            f"累计收益: {return_pct:+.2f}%",
            f"最大回撤: {dd_pct:.2f}%",
            f"今日交易: {self._total_trades}笔",
        ]
        return "\n".join(lines)

    def send_daily_summary(self) -> None:
        summary = self.build_daily_summary()
        self._alerts.send(summary)
