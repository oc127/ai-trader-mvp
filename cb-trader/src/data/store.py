from __future__ import annotations

import json
import sqlite3
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any

from src.logger import get_logger
from src.models import BacktestResult, BondInfo, BondSnapshot, DailyBar, Side, Trade

logger = get_logger(__name__)

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


class DataStore:
    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        schema = _SCHEMA_PATH.read_text()
        with self._lock:
            conn = self._get_conn()
            try:
                conn.executescript(schema)
                conn.commit()
            finally:
                conn.close()
        logger.info("Database initialized at %s", self._db_path)

    # ── bond_info ────────────────────────────────────────────

    def upsert_bond_info(self, bond: BondInfo) -> None:
        sql = """
            INSERT INTO bond_info
            (code, name, stock_code, stock_name, conversion_price,
             maturity_date, issue_date, par_value, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(code) DO UPDATE SET
                name=excluded.name,
                stock_code=excluded.stock_code,
                stock_name=excluded.stock_name,
                conversion_price=excluded.conversion_price,
                maturity_date=excluded.maturity_date,
                issue_date=excluded.issue_date,
                par_value=excluded.par_value,
                updated_at=datetime('now')
        """
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    sql,
                    (
                        bond.code,
                        bond.name,
                        bond.stock_code,
                        bond.stock_name,
                        bond.conversion_price,
                        bond.maturity_date.isoformat(),
                        bond.issue_date.isoformat(),
                        bond.par_value,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def upsert_bond_info_batch(self, bonds: list[BondInfo]) -> None:
        sql = """
            INSERT INTO bond_info
            (code, name, stock_code, stock_name, conversion_price,
             maturity_date, issue_date, par_value, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(code) DO UPDATE SET
                name=excluded.name,
                stock_code=excluded.stock_code,
                stock_name=excluded.stock_name,
                conversion_price=excluded.conversion_price,
                maturity_date=excluded.maturity_date,
                issue_date=excluded.issue_date,
                par_value=excluded.par_value,
                updated_at=datetime('now')
        """
        with self._lock:
            conn = self._get_conn()
            try:
                conn.executemany(
                    sql,
                    [
                        (
                            b.code,
                            b.name,
                            b.stock_code,
                            b.stock_name,
                            b.conversion_price,
                            b.maturity_date.isoformat(),
                            b.issue_date.isoformat(),
                            b.par_value,
                        )
                        for b in bonds
                    ],
                )
                conn.commit()
            finally:
                conn.close()
        logger.info("Upserted %d bond_info records", len(bonds))

    def get_bond_info(self, code: str) -> BondInfo | None:
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute("SELECT * FROM bond_info WHERE code = ?", (code,)).fetchone()
            finally:
                conn.close()
        if row is None:
            return None
        return BondInfo(
            code=row["code"],
            name=row["name"],
            stock_code=row["stock_code"],
            stock_name=row["stock_name"],
            conversion_price=row["conversion_price"],
            maturity_date=date.fromisoformat(row["maturity_date"]),
            issue_date=date.fromisoformat(row["issue_date"]),
            par_value=row["par_value"],
        )

    def get_all_bond_info(self) -> list[BondInfo]:
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute("SELECT * FROM bond_info ORDER BY code").fetchall()
            finally:
                conn.close()
        return [
            BondInfo(
                code=r["code"],
                name=r["name"],
                stock_code=r["stock_code"],
                stock_name=r["stock_name"],
                conversion_price=r["conversion_price"],
                maturity_date=date.fromisoformat(r["maturity_date"]),
                issue_date=date.fromisoformat(r["issue_date"]),
                par_value=r["par_value"],
            )
            for r in rows
        ]

    # ── daily_bars ───────────────────────────────────────────

    def insert_daily_bars(self, bars: list[DailyBar]) -> int:
        sql = """
            INSERT OR IGNORE INTO daily_bars (code, date, open, high, low, close, volume, amount)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        with self._lock:
            conn = self._get_conn()
            try:
                cursor = conn.executemany(
                    sql,
                    [(b.code, b.date.isoformat(), b.open, b.high, b.low, b.close, b.volume, b.amount) for b in bars],
                )
                conn.commit()
                inserted = cursor.rowcount
            finally:
                conn.close()
        return inserted

    def get_daily_bars(self, code: str, start: date | None = None, end: date | None = None) -> list[DailyBar]:
        conditions = ["code = ?"]
        params: list[Any] = [code]
        if start:
            conditions.append("date >= ?")
            params.append(start.isoformat())
        if end:
            conditions.append("date <= ?")
            params.append(end.isoformat())

        sql = f"SELECT * FROM daily_bars WHERE {' AND '.join(conditions)} ORDER BY date"
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(sql, params).fetchall()
            finally:
                conn.close()
        return [
            DailyBar(
                code=r["code"],
                date=date.fromisoformat(r["date"]),
                open=r["open"],
                high=r["high"],
                low=r["low"],
                close=r["close"],
                volume=r["volume"],
                amount=r["amount"],
            )
            for r in rows
        ]

    def get_bar_count(self, code: str) -> int:
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute("SELECT COUNT(*) as cnt FROM daily_bars WHERE code = ?", (code,)).fetchone()
            finally:
                conn.close()
        return row["cnt"]

    # ── bond_snapshots ───────────────────────────────────────

    def insert_snapshots(self, snapshots: list[BondSnapshot]) -> int:
        sql = """
            INSERT OR IGNORE INTO bond_snapshots
            (code, name, price, stock_code, stock_price, conversion_price, conversion_value,
             premium_rate, volume_cny, ytm, remaining_years, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        with self._lock:
            conn = self._get_conn()
            try:
                cursor = conn.executemany(
                    sql,
                    [
                        (
                            s.code,
                            s.name,
                            s.price,
                            s.stock_code,
                            s.stock_price,
                            s.conversion_price,
                            s.conversion_value,
                            s.premium_rate,
                            s.volume_cny,
                            s.ytm,
                            s.remaining_years,
                            s.timestamp.isoformat(),
                        )
                        for s in snapshots
                    ],
                )
                conn.commit()
                inserted = cursor.rowcount
            finally:
                conn.close()
        return inserted

    def get_latest_snapshots(self) -> list[BondSnapshot]:
        sql = """
            SELECT bs.* FROM bond_snapshots bs
            INNER JOIN (
                SELECT code, MAX(timestamp) as max_ts FROM bond_snapshots GROUP BY code
            ) latest ON bs.code = latest.code AND bs.timestamp = latest.max_ts
            ORDER BY bs.volume_cny DESC
        """
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(sql).fetchall()
            finally:
                conn.close()
        return [
            BondSnapshot(
                code=r["code"],
                name=r["name"],
                price=r["price"],
                stock_code=r["stock_code"],
                stock_price=r["stock_price"],
                conversion_price=r["conversion_price"],
                conversion_value=r["conversion_value"],
                premium_rate=r["premium_rate"],
                volume_cny=r["volume_cny"],
                ytm=r["ytm"],
                remaining_years=r["remaining_years"],
                timestamp=datetime.fromisoformat(r["timestamp"]),
            )
            for r in rows
        ]

    # ── trades ───────────────────────────────────────────────

    def insert_trades(self, trades: list[Trade], backtest_run_id: int | None = None) -> int:
        sql = """
            INSERT INTO trades (backtest_run_id, code, side, shares, price, cost, timestamp, signal_composite)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        with self._lock:
            conn = self._get_conn()
            try:
                cursor = conn.executemany(
                    sql,
                    [
                        (
                            backtest_run_id,
                            t.code,
                            t.side.value,
                            t.shares,
                            t.price,
                            t.cost,
                            t.timestamp.isoformat(),
                            t.signal_composite,
                        )
                        for t in trades
                    ],
                )
                conn.commit()
                inserted = cursor.rowcount
            finally:
                conn.close()
        return inserted

    def get_trades(self, backtest_run_id: int) -> list[Trade]:
        sql = "SELECT * FROM trades WHERE backtest_run_id = ? ORDER BY timestamp"
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(sql, (backtest_run_id,)).fetchall()
            finally:
                conn.close()
        return [
            Trade(
                code=r["code"],
                side=Side(r["side"]),
                shares=r["shares"],
                price=r["price"],
                cost=r["cost"],
                timestamp=datetime.fromisoformat(r["timestamp"]),
                signal_composite=r["signal_composite"],
            )
            for r in rows
        ]

    # ── backtest_runs ────────────────────────────────────────

    def create_backtest_run(self, start_date: date, end_date: date, config: dict) -> int:
        sql = """
            INSERT INTO backtest_runs (start_date, end_date, config_json)
            VALUES (?, ?, ?)
        """
        with self._lock:
            conn = self._get_conn()
            try:
                cursor = conn.execute(
                    sql,
                    (
                        start_date.isoformat(),
                        end_date.isoformat(),
                        json.dumps(config),
                    ),
                )
                conn.commit()
                run_id = cursor.lastrowid
            finally:
                conn.close()
        return run_id

    def update_backtest_run(self, run_id: int, result: BacktestResult) -> None:
        sql = """
            UPDATE backtest_runs SET
                total_return = ?, annualized_return = ?, max_drawdown = ?,
                sharpe_ratio = ?, win_rate = ?, total_trades = ?
            WHERE id = ?
        """
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    sql,
                    (
                        result.total_return,
                        result.annualized_return,
                        result.max_drawdown,
                        result.sharpe_ratio,
                        result.win_rate,
                        result.total_trades,
                        run_id,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    # ── daily_pnl ────────────────────────────────────────────

    def insert_daily_pnl(
        self,
        backtest_run_id: int,
        pnl_date: date,
        pnl: float,
        cumulative_pnl: float,
        capital: float,
        trade_count: int,
    ) -> None:
        sql = """
            INSERT INTO daily_pnl (backtest_run_id, date, pnl, cumulative_pnl, capital, trade_count)
            VALUES (?, ?, ?, ?, ?, ?)
        """
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(sql, (backtest_run_id, pnl_date.isoformat(), pnl, cumulative_pnl, capital, trade_count))
                conn.commit()
            finally:
                conn.close()
