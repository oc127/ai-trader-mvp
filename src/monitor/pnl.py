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
