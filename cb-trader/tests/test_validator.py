from __future__ import annotations

from datetime import date, datetime

from src.data.validator import (
    filter_valid_bars,
    filter_valid_snapshots,
    validate_bar,
    validate_snapshot,
)
from src.models import BondSnapshot, DailyBar


def _bar(**kwargs) -> DailyBar:
    defaults = {
        "code": "123001",
        "date": date(2024, 1, 2),
        "open": 100.0,
        "high": 105.0,
        "low": 99.0,
        "close": 103.0,
        "volume": 10000,
        "amount": 1030000,
    }
    defaults.update(kwargs)
    return DailyBar(**defaults)


def _snapshot(**kwargs) -> BondSnapshot:
    defaults = {
        "code": "123001",
        "name": "测试转债",
        "price": 105.0,
        "stock_code": "600001",
        "stock_price": 11.0,
        "conversion_price": 10.0,
        "conversion_value": 110.0,
        "premium_rate": 0.05,
        "volume_cny": 80_000_000,
        "ytm": 0.02,
        "remaining_years": 3.5,
        "timestamp": datetime(2024, 6, 1),
    }
    defaults.update(kwargs)
    return BondSnapshot(**defaults)


class TestValidateBar:
    def test_valid_bar(self) -> None:
        assert validate_bar(_bar()) is True

    def test_negative_close(self) -> None:
        assert validate_bar(_bar(close=-1)) is False

    def test_zero_open(self) -> None:
        assert validate_bar(_bar(open=0)) is False

    def test_high_below_low(self) -> None:
        assert validate_bar(_bar(high=98, low=100)) is False

    def test_high_below_close(self) -> None:
        assert validate_bar(_bar(high=102, close=103)) is False

    def test_low_above_open(self) -> None:
        assert validate_bar(_bar(low=101, open=100)) is False

    def test_negative_volume(self) -> None:
        assert validate_bar(_bar(volume=-1)) is False

    def test_price_too_high(self) -> None:
        assert validate_bar(_bar(close=1500, high=1500)) is False

    def test_price_too_low(self) -> None:
        assert validate_bar(_bar(close=5, low=5, open=5, high=5)) is False


class TestValidateSnapshot:
    def test_valid_snapshot(self) -> None:
        assert validate_snapshot(_snapshot()) is True

    def test_empty_code(self) -> None:
        assert validate_snapshot(_snapshot(code="")) is False

    def test_short_code(self) -> None:
        assert validate_snapshot(_snapshot(code="123")) is False

    def test_zero_price(self) -> None:
        assert validate_snapshot(_snapshot(price=0)) is False

    def test_extreme_price(self) -> None:
        assert validate_snapshot(_snapshot(price=2000)) is False

    def test_zero_conversion_price(self) -> None:
        assert validate_snapshot(_snapshot(conversion_price=0)) is False

    def test_extreme_premium(self) -> None:
        assert validate_snapshot(_snapshot(premium_rate=15.0)) is False

    def test_negative_volume(self) -> None:
        assert validate_snapshot(_snapshot(volume_cny=-1)) is False

    def test_negative_remaining_years(self) -> None:
        assert validate_snapshot(_snapshot(remaining_years=-0.5)) is False


class TestFilterFunctions:
    def test_filter_valid_bars(self) -> None:
        bars = [_bar(), _bar(close=-1), _bar(high=50, low=100)]
        result = filter_valid_bars(bars)
        assert len(result) == 1

    def test_filter_valid_snapshots(self) -> None:
        snapshots = [_snapshot(), _snapshot(code=""), _snapshot(price=0)]
        result = filter_valid_snapshots(snapshots)
        assert len(result) == 1

    def test_filter_empty_input(self) -> None:
        assert filter_valid_bars([]) == []
        assert filter_valid_snapshots([]) == []
