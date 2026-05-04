from __future__ import annotations

from datetime import datetime

from src.models import BondSnapshot
from src.strategy.universe import UniverseFilter


def _make_snapshot(
    code: str = "123001",
    price: float = 120.0,
    premium_rate: float = 0.10,
    volume_cny: float = 80_000_000,
    remaining_years: float = 3.0,
) -> BondSnapshot:
    return BondSnapshot(
        code=code,
        name=f"转债{code}",
        price=price,
        stock_code="600001",
        stock_price=12.0,
        conversion_price=10.0,
        conversion_value=120.0,
        premium_rate=premium_rate,
        volume_cny=volume_cny,
        ytm=0.02,
        remaining_years=remaining_years,
        timestamp=datetime(2024, 6, 1),
    )


class TestUniverseFilter:
    def setup_method(self) -> None:
        self.config = {
            "universe": {
                "max_premium_rate": 0.20,
                "min_volume_cny": 50_000_000,
                "min_price": 100,
                "max_price": 200,
                "min_remaining_years": 0.5,
                "min_days_since_ipo": 20,
                "max_count": 5,
            }
        }
        self.uf = UniverseFilter(self.config)

    def test_passes_good_bond(self) -> None:
        result = self.uf.filter([_make_snapshot()])
        assert len(result) == 1

    def test_rejects_high_premium(self) -> None:
        result = self.uf.filter([_make_snapshot(premium_rate=0.35)])
        assert len(result) == 0

    def test_rejects_low_volume(self) -> None:
        result = self.uf.filter([_make_snapshot(volume_cny=10_000_000)])
        assert len(result) == 0

    def test_rejects_price_too_low(self) -> None:
        result = self.uf.filter([_make_snapshot(price=90)])
        assert len(result) == 0

    def test_rejects_price_too_high(self) -> None:
        result = self.uf.filter([_make_snapshot(price=250)])
        assert len(result) == 0

    def test_rejects_short_remaining(self) -> None:
        result = self.uf.filter([_make_snapshot(remaining_years=0.2)])
        assert len(result) == 0

    def test_sorts_by_volume_descending(self) -> None:
        snapshots = [
            _make_snapshot(code="001", volume_cny=60_000_000),
            _make_snapshot(code="002", volume_cny=200_000_000),
            _make_snapshot(code="003", volume_cny=100_000_000),
        ]
        result = self.uf.filter(snapshots)
        assert [s.code for s in result] == ["002", "003", "001"]

    def test_respects_max_count(self) -> None:
        snapshots = [_make_snapshot(code=f"10{i}", volume_cny=float(100_000_000 - i * 1_000_000)) for i in range(10)]
        result = self.uf.filter(snapshots)
        assert len(result) == 5

    def test_empty_input(self) -> None:
        result = self.uf.filter([])
        assert result == []

    def test_all_rejected(self) -> None:
        snapshots = [
            _make_snapshot(premium_rate=0.50),
            _make_snapshot(volume_cny=1_000_000),
            _make_snapshot(price=50),
        ]
        result = self.uf.filter(snapshots)
        assert len(result) == 0

    def test_edge_case_exact_threshold(self) -> None:
        result = self.uf.filter([_make_snapshot(premium_rate=0.20)])
        assert len(result) == 1

        result = self.uf.filter([_make_snapshot(premium_rate=0.20001)])
        assert len(result) == 0
