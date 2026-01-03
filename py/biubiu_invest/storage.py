from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional


@dataclass(frozen=True)
class DailyBar:
    ts_code: str  # e.g. 000001.SZ
    trade_date: str  # YYYY-MM-DD
    open: float
    high: float
    low: float
    close: float
    volume: float


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_bars (
          ts_code TEXT NOT NULL,
          trade_date TEXT NOT NULL,
          open REAL NOT NULL,
          high REAL NOT NULL,
          low REAL NOT NULL,
          close REAL NOT NULL,
          volume REAL NOT NULL,
          PRIMARY KEY (ts_code, trade_date)
        );
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_daily_bars_date ON daily_bars(trade_date);")
    conn.commit()


def upsert_daily_bars(conn: sqlite3.Connection, bars: Iterable[DailyBar]) -> int:
    rows = [
        (b.ts_code, b.trade_date, b.open, b.high, b.low, b.close, b.volume)
        for b in bars
    ]
    if not rows:
        return 0
    conn.executemany(
        """
        INSERT INTO daily_bars (ts_code, trade_date, open, high, low, close, volume)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(ts_code, trade_date) DO UPDATE SET
          open=excluded.open,
          high=excluded.high,
          low=excluded.low,
          close=excluded.close,
          volume=excluded.volume;
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def latest_trade_date(conn: sqlite3.Connection, ts_code: str) -> Optional[str]:
    cur = conn.execute(
        "SELECT MAX(trade_date) FROM daily_bars WHERE ts_code=?;",
        (ts_code,),
    )
    row = cur.fetchone()
    return row[0] if row and row[0] else None


def load_daily_bars(
    conn: sqlite3.Connection,
    ts_codes: list[str],
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> list[DailyBar]:
    if not ts_codes:
        return []

    where = ["ts_code IN (%s)" % ",".join(["?"] * len(ts_codes))]
    params: list[str] = list(ts_codes)
    if start_date:
        where.append("trade_date >= ?")
        params.append(start_date)
    if end_date:
        where.append("trade_date <= ?")
        params.append(end_date)

    q = f"""
      SELECT ts_code, trade_date, open, high, low, close, volume
      FROM daily_bars
      WHERE {' AND '.join(where)}
      ORDER BY ts_code ASC, trade_date ASC;
    """
    cur = conn.execute(q, params)
    out: list[DailyBar] = []
    for row in cur.fetchall():
        out.append(
            DailyBar(
                ts_code=row[0],
                trade_date=row[1],
                open=float(row[2]),
                high=float(row[3]),
                low=float(row[4]),
                close=float(row[5]),
                volume=float(row[6]),
            )
        )
    return out


