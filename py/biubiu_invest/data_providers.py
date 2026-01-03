from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .storage import DailyBar


@dataclass(frozen=True)
class FetchRequest:
    ts_codes: list[str]
    start_date: Optional[str] = None  # YYYY-MM-DD
    end_date: Optional[str] = None  # YYYY-MM-DD


class Provider:
    name: str

    def fetch_daily_bars(self, req: FetchRequest) -> list[DailyBar]:
        raise NotImplementedError


class SampleCsvProvider(Provider):
    name = "sample"

    def __init__(self, csv_path: Path):
        self.csv_path = csv_path

    def fetch_daily_bars(self, req: FetchRequest) -> list[DailyBar]:
        rows: list[DailyBar] = []
        with self.csv_path.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                ts_code = str(r["ts_code"]).strip()
                if ts_code not in set(req.ts_codes):
                    continue
                trade_date = str(r["trade_date"]).strip()
                if req.start_date and trade_date < req.start_date:
                    continue
                if req.end_date and trade_date > req.end_date:
                    continue
                rows.append(
                    DailyBar(
                        ts_code=ts_code,
                        trade_date=trade_date,
                        open=float(r["open"]),
                        high=float(r["high"]),
                        low=float(r["low"]),
                        close=float(r["close"]),
                        volume=float(r.get("volume") or 0),
                    )
                )
        return rows


class AkShareProvider(Provider):
    """
    Free A-share daily bars via AkShare.

    Notes:
    - AkShare's API surfaces can change; this provider is intentionally narrow.
    - We fetch per-symbol for a watchlist (good enough for MVP daily report).
    """

    name = "akshare"

    def __init__(self):
        try:
            import akshare as ak  # type: ignore
        except Exception as e:  # pragma: no cover
            raise RuntimeError(
                "AkShare is not installed. Install it first: pip install akshare"
            ) from e
        self.ak = ak

    @staticmethod
    def _to_ak_symbol(ts_code: str) -> str:
        # 000001.SZ -> 000001 ; 600519.SH -> 600519
        return ts_code.split(".")[0]

    def fetch_daily_bars(self, req: FetchRequest) -> list[DailyBar]:
        out: list[DailyBar] = []

        # AkShare returns pandas.DataFrame. Import pandas lazily so sample mode has zero deps.
        try:
            import pandas as pd  # type: ignore
        except Exception as e:  # pragma: no cover
            raise RuntimeError(
                "pandas is required when using --provider akshare. Install it first (or just pip install akshare)."
            ) from e

        for ts_code in req.ts_codes:
            symbol = self._to_ak_symbol(ts_code)
            # period='daily' adjust='' ; start_date/end_date need YYYYMMDD for this endpoint.
            start = req.start_date.replace("-", "") if req.start_date else "19900101"
            end = req.end_date.replace("-", "") if req.end_date else "20991231"

            df = self.ak.stock_zh_a_hist(  # type: ignore[attr-defined]
                symbol=symbol, period="daily", start_date=start, end_date=end, adjust=""
            )
            if df is None or df.empty:
                continue

            # Normalize columns (AkShare uses Chinese column names; map common ones).
            # Typical columns: 日期, 开盘, 收盘, 最高, 最低, 成交量
            # Some variants: "日期" vs "date"
            cols = {c: str(c).strip() for c in df.columns}
            df = df.rename(columns=cols)

            def pick(*candidates: str) -> str:
                for c in candidates:
                    if c in df.columns:
                        return c
                raise KeyError(f"Missing columns in AkShare result: {candidates}")

            c_date = pick("日期", "date")
            c_open = pick("开盘", "open")
            c_close = pick("收盘", "close")
            c_high = pick("最高", "high")
            c_low = pick("最低", "low")
            c_vol = pick("成交量", "volume")

            # Use pandas operations; ensure string date format YYYY-MM-DD
            s = df[[c_date, c_open, c_high, c_low, c_close, c_vol]].copy()
            s[c_date] = pd.to_datetime(s[c_date]).dt.strftime("%Y-%m-%d")

            for _, r in s.iterrows():
                out.append(
                    DailyBar(
                        ts_code=ts_code,
                        trade_date=str(r[c_date]),
                        open=float(r[c_open]),
                        high=float(r[c_high]),
                        low=float(r[c_low]),
                        close=float(r[c_close]),
                        volume=float(r[c_vol]),
                    )
                )

        return out


def load_watchlist(path: Path) -> list[str]:
    ts_codes: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        x = line.strip()
        if not x or x.startswith("#"):
            continue
        ts_codes.append(x)
    # de-dup keep order
    seen = set()
    out: list[str] = []
    for x in ts_codes:
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


