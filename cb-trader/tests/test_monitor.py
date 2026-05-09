from __future__ import annotations

from unittest.mock import MagicMock

from src.monitor.alerts import AlertManager
from src.monitor.pnl import PnLTracker


class TestAlertManager:
    def test_send_logs_message(self) -> None:
        am = AlertManager({"monitor": {"telegram_enabled": False}})
        am.send("test message")

    def test_telegram_disabled_by_default(self) -> None:
        am = AlertManager()
        assert am._telegram_enabled is False

    def test_telegram_disabled_without_credentials(self) -> None:
        am = AlertManager({"monitor": {"telegram_enabled": True}})
        assert am._telegram_enabled is False


class TestPnLTracker:
    def _make_tracker(self, capital: float = 2_000_000) -> PnLTracker:
        store = MagicMock()
        alerts = AlertManager()
        return PnLTracker(store, alerts, initial_capital=capital)

    def test_initial_state(self) -> None:
        tracker = self._make_tracker()
        assert tracker.total_return_pct == 0.0
        assert tracker.drawdown_pct == 0.0

    def test_update_tracks_equity(self) -> None:
        tracker = self._make_tracker(1_000_000)
        tracker.update(equity=1_050_000, daily_pnl=50_000, trade_count=10)
        assert tracker.total_return_pct == 0.05

    def test_drawdown_calculation(self) -> None:
        tracker = self._make_tracker(1_000_000)
        tracker.update(equity=1_100_000, daily_pnl=100_000, trade_count=5)
        tracker.update(equity=1_000_000, daily_pnl=-100_000, trade_count=8)
        expected_dd = (1_100_000 - 1_000_000) / 1_100_000
        assert abs(tracker.drawdown_pct - expected_dd) < 0.001

    def test_daily_summary_format(self) -> None:
        tracker = self._make_tracker(1_000_000)
        tracker.update(equity=1_010_000, daily_pnl=10_000, trade_count=25)
        summary = tracker.build_daily_summary()
        assert "CB T+0" in summary
        assert "10,000" in summary
        assert "25" in summary
