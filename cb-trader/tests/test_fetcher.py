from __future__ import annotations

from datetime import date, datetime
from unittest.mock import patch

import pandas as pd

from src.data.fetcher import DataFetcher
from src.models import BondSnapshot


class TestFetcher:
    def setup_method(self) -> None:
        self.fetcher = DataFetcher()

    def test_fetch_bond_list_jsl_parses_correctly(self) -> None:
        mock_df = pd.DataFrame(
            [
                {
                    "债券代码": "123001",
                    "债券简称": "蓝晓转债",
                    "现价": 130.5,
                    "正股代码": "300487",
                    "正股价": 42.0,
                    "转股价": 35.0,
                    "转股价值": 120.0,
                    "溢价率": 8.75,
                    "成交额(万元)": 5000.0,
                    "到期收益率": 1.5,
                    "剩余年限": 3.2,
                }
            ]
        )

        with patch("src.data.fetcher.ak.bond_cb_jsl", return_value=mock_df):
            snapshots = self.fetcher.fetch_bond_list_jsl()

        assert len(snapshots) == 1
        s = snapshots[0]
        assert s.code == "123001"
        assert s.name == "蓝晓转债"
        assert s.price == 130.5
        assert s.premium_rate == 0.0875
        assert s.volume_cny == 50_000_000

    def test_fetch_bond_list_jsl_handles_empty(self) -> None:
        with patch("src.data.fetcher.ak.bond_cb_jsl", return_value=pd.DataFrame()):
            snapshots = self.fetcher.fetch_bond_list_jsl()
        assert snapshots == []

    def test_fetch_daily_bars_parses_correctly(self) -> None:
        mock_df = pd.DataFrame(
            [
                {
                    "date": pd.Timestamp("2024-01-02"),
                    "open": 100.0,
                    "high": 105.0,
                    "low": 99.0,
                    "close": 103.0,
                    "volume": 10000,
                },
                {
                    "date": pd.Timestamp("2024-01-03"),
                    "open": 103.0,
                    "high": 108.0,
                    "low": 102.0,
                    "close": 107.0,
                    "volume": 12000,
                },
            ]
        )

        with patch("src.data.fetcher.ak.bond_zh_hs_cov_daily", return_value=mock_df):
            bars = self.fetcher.fetch_daily_bars("123001")

        assert len(bars) == 2
        assert bars[0].code == "123001"
        assert bars[0].date == date(2024, 1, 2)
        assert bars[1].close == 107.0

    def test_fetch_daily_bars_handles_error(self) -> None:
        with patch("src.data.fetcher.ak.bond_zh_hs_cov_daily", side_effect=Exception("API error")):
            bars = self.fetcher.fetch_daily_bars("999999")
        assert bars == []

    def test_fetch_daily_bars_date_filter(self) -> None:
        mock_df = pd.DataFrame(
            [
                {
                    "date": pd.Timestamp("2024-01-01"),
                    "open": 100.0,
                    "high": 105.0,
                    "low": 99.0,
                    "close": 103.0,
                    "volume": 10000,
                },
                {
                    "date": pd.Timestamp("2024-06-01"),
                    "open": 110.0,
                    "high": 115.0,
                    "low": 109.0,
                    "close": 113.0,
                    "volume": 15000,
                },
                {
                    "date": pd.Timestamp("2024-12-01"),
                    "open": 120.0,
                    "high": 125.0,
                    "low": 119.0,
                    "close": 123.0,
                    "volume": 20000,
                },
            ]
        )

        with patch("src.data.fetcher.ak.bond_zh_hs_cov_daily", return_value=mock_df):
            bars = self.fetcher.fetch_daily_bars("123001", start=date(2024, 3, 1), end=date(2024, 9, 1))

        assert len(bars) == 1
        assert bars[0].date == date(2024, 6, 1)

    def test_fetch_stock_daily_parses_correctly(self) -> None:
        mock_df = pd.DataFrame(
            [
                {
                    "日期": "2024-01-02",
                    "开盘": 10.0,
                    "最高": 10.5,
                    "最低": 9.9,
                    "收盘": 10.3,
                    "成交量": 500000,
                    "成交额": 5100000,
                },
            ]
        )

        with patch("src.data.fetcher.ak.stock_zh_a_hist", return_value=mock_df):
            bars = self.fetcher.fetch_stock_daily("600001", start="20240101", end="20240110")

        assert len(bars) == 1
        assert bars[0].code == "600001"
        assert bars[0].close == 10.3

    def test_fetch_stock_daily_handles_error(self) -> None:
        with patch("src.data.fetcher.ak.stock_zh_a_hist", side_effect=Exception("timeout")):
            bars = self.fetcher.fetch_stock_daily("600001")
        assert bars == []

    def test_snapshots_to_bond_info(self) -> None:
        snapshots = [
            BondSnapshot(
                code="123001",
                name="测试转债",
                price=130.0,
                stock_code="300487",
                stock_price=42.0,
                conversion_price=35.0,
                conversion_value=120.0,
                premium_rate=0.0875,
                volume_cny=50_000_000,
                ytm=0.015,
                remaining_years=3.2,
                timestamp=datetime(2024, 6, 1),
            ),
        ]
        bonds = self.fetcher.snapshots_to_bond_info(snapshots)
        assert len(bonds) == 1
        assert bonds[0].code == "123001"
        assert bonds[0].conversion_price == 35.0

    def test_snapshots_to_bond_info_skips_invalid(self) -> None:
        snapshots = [
            BondSnapshot(
                code="",
                name="",
                price=0,
                stock_code="",
                stock_price=0,
                conversion_price=0,
                conversion_value=0,
                premium_rate=0,
                volume_cny=0,
                ytm=0,
                remaining_years=0,
                timestamp=datetime(2024, 6, 1),
            ),
        ]
        bonds = self.fetcher.snapshots_to_bond_info(snapshots)
        assert len(bonds) == 0
