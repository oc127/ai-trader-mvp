from __future__ import annotations

from datetime import date, datetime

import akshare as ak
import pandas as pd

from src.data.validator import filter_valid_bars, filter_valid_snapshots
from src.logger import get_logger
from src.models import BondInfo, BondSnapshot, DailyBar

logger = get_logger(__name__)


class DataFetcher:
    """Wraps akshare APIs for convertible bond data."""

    def fetch_bond_list_jsl(self) -> list[BondSnapshot]:
        """Fetch current convertible bond list with premiums from Jisilu."""
        logger.info("Fetching bond list from Jisilu via akshare...")
        df = ak.bond_cb_jsl()
        if df.empty:
            logger.warning("Empty result from bond_cb_jsl")
            return []

        snapshots = []
        now = datetime.now()
        for _, row in df.iterrows():
            try:
                snapshot = BondSnapshot(
                    code=str(row.get("债券代码", "")),
                    name=str(row.get("债券简称", "")),
                    price=float(row.get("现价", 0)),
                    stock_code=str(row.get("正股代码", "")),
                    stock_price=float(row.get("正股价", 0)),
                    conversion_price=float(row.get("转股价", 0)),
                    conversion_value=float(row.get("转股价值", 0)),
                    premium_rate=float(row.get("溢价率", 0)) / 100.0,
                    volume_cny=float(row.get("成交额(万元)", 0)) * 10000,
                    ytm=float(row.get("到期收益率", 0)) / 100.0,
                    remaining_years=float(row.get("剩余年限", 0)),
                    timestamp=now,
                )
                if snapshot.code and snapshot.price > 0:
                    snapshots.append(snapshot)
            except (ValueError, TypeError) as e:
                logger.debug("Skipping row: %s", e)
                continue

        snapshots = filter_valid_snapshots(snapshots)
        logger.info("Fetched %d valid bond snapshots", len(snapshots))
        return snapshots

    def fetch_daily_bars(self, code: str, start: date | None = None, end: date | None = None) -> list[DailyBar]:
        """Fetch historical daily OHLCV for a convertible bond."""
        logger.debug("Fetching daily bars for %s", code)
        try:
            df = ak.bond_zh_hs_cov_daily(symbol=code)
        except Exception as e:
            logger.warning("Failed to fetch daily bars for %s: %s", code, e)
            return []

        if df.empty:
            return []

        if start:
            df = df[df["date"] >= pd.Timestamp(start)]
        if end:
            df = df[df["date"] <= pd.Timestamp(end)]

        bars = []
        for _, row in df.iterrows():
            try:
                bar = DailyBar(
                    code=code,
                    date=(
                        row["date"].date()
                        if isinstance(row["date"], pd.Timestamp)
                        else date.fromisoformat(str(row["date"])[:10])
                    ),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("volume", 0)),
                    amount=float(row.get("amount", 0)) if "amount" in row.index else 0.0,
                )
                bars.append(bar)
            except (ValueError, TypeError, KeyError) as e:
                logger.debug("Skipping bar row for %s: %s", code, e)
                continue

        bars = filter_valid_bars(bars)
        logger.debug("Fetched %d valid bars for %s", len(bars), code)
        return bars

    def fetch_stock_daily(self, stock_code: str, start: str | None = None, end: str | None = None) -> list[DailyBar]:
        """Fetch historical daily OHLCV for an A-share stock."""
        logger.debug("Fetching stock daily for %s", stock_code)
        try:
            df = ak.stock_zh_a_hist(
                symbol=stock_code,
                period="daily",
                start_date=start or "20230101",
                end_date=end or datetime.now().strftime("%Y%m%d"),
                adjust="qfq",
            )
        except Exception as e:
            logger.warning("Failed to fetch stock daily for %s: %s", stock_code, e)
            return []

        if df.empty:
            return []

        bars = []
        for _, row in df.iterrows():
            try:
                bar_date = row["日期"]
                if isinstance(bar_date, str):
                    bar_date = date.fromisoformat(bar_date)
                elif isinstance(bar_date, pd.Timestamp):
                    bar_date = bar_date.date()

                bar = DailyBar(
                    code=stock_code,
                    date=bar_date,
                    open=float(row["开盘"]),
                    high=float(row["最高"]),
                    low=float(row["最低"]),
                    close=float(row["收盘"]),
                    volume=float(row["成交量"]),
                    amount=float(row["成交额"]),
                )
                bars.append(bar)
            except (ValueError, TypeError, KeyError) as e:
                logger.debug("Skipping stock bar for %s: %s", stock_code, e)
                continue

        logger.debug("Fetched %d stock bars for %s", len(bars), stock_code)
        return bars

    def snapshots_to_bond_info(self, snapshots: list[BondSnapshot]) -> list[BondInfo]:
        """Extract BondInfo from snapshots (best-effort, maturity/issue dates not available from Jisilu)."""
        bonds = []
        today = date.today()
        for s in snapshots:
            if not s.code or not s.stock_code:
                continue
            remaining_days = int(s.remaining_years * 365)
            estimated_maturity = date.fromordinal(today.toordinal() + remaining_days)
            estimated_issue = date.fromordinal(today.toordinal() + remaining_days - 365 * 6)

            bonds.append(
                BondInfo(
                    code=s.code,
                    name=s.name,
                    stock_code=s.stock_code,
                    stock_name="",
                    conversion_price=s.conversion_price,
                    maturity_date=estimated_maturity,
                    issue_date=estimated_issue,
                )
            )
        return bonds
