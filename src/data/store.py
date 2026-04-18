from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src.data.models import SCHEMA_SQL
from src.hl_client.types import FundingRate
from src.logger import get_logger

log = get_logger(__name__)


class DataStore:
    def __init__(self, db_path: str = "data/trader.db") -> None:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA_SQL)
        log.info("DataStore initialized", extra={"path": str(path)})

    def close(self) -> None:
        self._conn.close()

    def save_funding_rate(self, rate: FundingRate) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO funding_rates (coin, rate, premium, timestamp) VALUES (?, ?, ?, ?)",
            (rate.coin, rate.rate, rate.premium, rate.timestamp.isoformat()),
        )
        self._conn.commit()

    def save_funding_rates(self, rates: list[FundingRate]) -> None:
        rows = [(r.coin, r.rate, r.premium, r.timestamp.isoformat()) for r in rates]
        self._conn.executemany(
            "INSERT OR IGNORE INTO funding_rates (coin, rate, premium, timestamp) VALUES (?, ?, ?, ?)",
            rows,
        )
        self._conn.commit()

    def get_funding_history(
        self, coin: str, start: datetime | None = None, end: datetime | None = None
    ) -> list[FundingRate]:
        query = "SELECT coin, rate, premium, timestamp FROM funding_rates WHERE coin = ?"
        params: list = [coin]
        if start:
            query += " AND timestamp >= ?"
            params.append(start.isoformat())
        if end:
            query += " AND timestamp <= ?"
            params.append(end.isoformat())
        query += " ORDER BY timestamp"

        rows = self._conn.execute(query, params).fetchall()
        return [
            FundingRate(
                coin=r["coin"],
                rate=r["rate"],
                premium=r["premium"],
                timestamp=datetime.fromisoformat(r["timestamp"]),
            )
            for r in rows
        ]

    def get_latest_funding_rate(self, coin: str) -> FundingRate | None:
        row = self._conn.execute(
            "SELECT coin, rate, premium, timestamp FROM funding_rates WHERE coin = ? ORDER BY timestamp DESC LIMIT 1",
            (coin,),
        ).fetchone()
        if not row:
            return None
        return FundingRate(
            coin=row["coin"],
            rate=row["rate"],
            premium=row["premium"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
        )

    def save_trade(
        self,
        coin: str,
        side: str,
        size: float,
        price: float,
        order_id: str = "",
        is_spot: bool = False,
        fee: float = 0.0,
    ) -> None:
        sql = (
            "INSERT INTO trades (coin, side, size, price, order_id, is_spot, fee, timestamp)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
        )
        self._conn.execute(
            sql,
            (
                coin,
                side,
                size,
                price,
                order_id,
                int(is_spot),
                fee,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._conn.commit()

    def save_pnl_snapshot(
        self,
        equity: float,
        available_balance: float,
        unrealized_pnl: float,
        realized_pnl: float,
        funding_earned: float = 0.0,
    ) -> None:
        sql = (
            "INSERT INTO pnl_snapshots (equity, available_balance, unrealized_pnl,"
            " realized_pnl, funding_earned, timestamp) VALUES (?, ?, ?, ?, ?, ?)"
        )
        self._conn.execute(
            sql,
            (
                equity,
                available_balance,
                unrealized_pnl,
                realized_pnl,
                funding_earned,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._conn.commit()

    def save_event(self, event_type: str, data: dict) -> None:
        self._conn.execute(
            "INSERT INTO events (event_type, data, timestamp) VALUES (?, ?, ?)",
            (event_type, json.dumps(data), datetime.now(timezone.utc).isoformat()),
        )
        self._conn.commit()

    def get_pnl_history(self, limit: int = 100) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM pnl_snapshots ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
