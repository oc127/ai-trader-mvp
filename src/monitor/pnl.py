from __future__ import annotations

from datetime import datetime, timezone

from src.data.store import DataStore
from src.hl_client.rest import HLRestClient
from src.logger import get_logger

log = get_logger(__name__)


class PnLTracker:
    def __init__(self, client: HLRestClient, store: DataStore) -> None:
        self._client = client
        self._store = store
        self._initial_equity: float | None = None
        self._last_daily_summary: str = ""

    def snapshot(self) -> dict:
        account = self._client.get_account_state()

        if self._initial_equity is None:
            self._initial_equity = account.equity

        total_unrealized = sum(p.unrealized_pnl for p in account.positions)

        snapshot = {
            "equity": account.equity,
            "available_balance": account.available_balance,
            "unrealized_pnl": total_unrealized,
            "realized_pnl": account.equity - self._initial_equity - total_unrealized,
            "margin_used": account.margin_used,
            "margin_utilization": account.margin_utilization,
            "num_positions": len(account.positions),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        self._store.save_pnl_snapshot(
            equity=snapshot["equity"],
            available_balance=snapshot["available_balance"],
            unrealized_pnl=snapshot["unrealized_pnl"],
            realized_pnl=snapshot["realized_pnl"],
        )

        log.info("PnL snapshot", extra=snapshot)
        return snapshot

    def should_send_daily_summary(self) -> bool:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        hour = datetime.now(timezone.utc).hour
        if hour == 0 and today != self._last_daily_summary:
            return True
        return False

    def build_daily_summary(self, positions: dict | None = None) -> str:
        account = self._client.get_account_state()
        total_unrealized = sum(p.unrealized_pnl for p in account.positions)

        if self._initial_equity is None:
            self._initial_equity = account.equity

        total_return = account.equity - self._initial_equity
        return_pct = (total_return / self._initial_equity * 100) if self._initial_equity > 0 else 0

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self._last_daily_summary = today

        lines = [
            f"*HL | Daily Summary — {today}*",
            "",
            f"💰 Equity: ${account.equity:,.2f}",
            f"📈 Total Return: ${total_return:+,.2f} ({return_pct:+.2f}%)",
            f"📉 Unrealized PnL: ${total_unrealized:+,.2f}",
            f"🏦 Available: ${account.available_balance:,.2f}",
            f"⚖️ Margin Used: {account.margin_utilization:.1%}",
            f"📌 Open Positions: {len(account.positions)}",
        ]

        for p in account.positions:
            lines.append(f"  • {p.coin}: size={p.size:.4f}, PnL=${p.unrealized_pnl:+,.2f}")

        return "\n".join(lines)
