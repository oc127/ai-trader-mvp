from __future__ import annotations

from datetime import date, datetime

from src.data.store import DataStore
from src.models import BondInfo, BondSnapshot, DailyBar, Side, Trade


class TestBondInfo:
    def test_upsert_and_get(self, store: DataStore) -> None:
        bond = BondInfo(
            code="123001",
            name="测试转债",
            stock_code="600001",
            stock_name="测试股票",
            conversion_price=10.0,
            maturity_date=date(2028, 1, 1),
            issue_date=date(2022, 1, 1),
        )
        store.upsert_bond_info(bond)
        result = store.get_bond_info("123001")

        assert result is not None
        assert result.code == "123001"
        assert result.name == "测试转债"
        assert result.conversion_price == 10.0

    def test_upsert_updates_existing(self, store: DataStore) -> None:
        bond = BondInfo(
            code="123001",
            name="测试转债",
            stock_code="600001",
            stock_name="测试股票",
            conversion_price=10.0,
            maturity_date=date(2028, 1, 1),
            issue_date=date(2022, 1, 1),
        )
        store.upsert_bond_info(bond)

        updated = BondInfo(
            code="123001",
            name="测试转债",
            stock_code="600001",
            stock_name="测试股票",
            conversion_price=8.5,
            maturity_date=date(2028, 1, 1),
            issue_date=date(2022, 1, 1),
        )
        store.upsert_bond_info(updated)

        result = store.get_bond_info("123001")
        assert result is not None
        assert result.conversion_price == 8.5

    def test_get_nonexistent(self, store: DataStore) -> None:
        assert store.get_bond_info("999999") is None

    def test_batch_upsert(self, store: DataStore) -> None:
        bonds = [
            BondInfo(
                code=f"12300{i}",
                name=f"转债{i}",
                stock_code=f"60000{i}",
                stock_name=f"股票{i}",
                conversion_price=10.0 + i,
                maturity_date=date(2028, 1, 1),
                issue_date=date(2022, 1, 1),
            )
            for i in range(5)
        ]
        store.upsert_bond_info_batch(bonds)
        all_bonds = store.get_all_bond_info()
        assert len(all_bonds) == 5

    def test_get_all(self, store: DataStore) -> None:
        assert store.get_all_bond_info() == []


class TestDailyBars:
    def test_insert_and_get(self, store: DataStore) -> None:
        bars = [
            DailyBar(
                code="123001",
                date=date(2024, 1, 2),
                open=100.0,
                high=105.0,
                low=99.0,
                close=103.0,
                volume=10000,
                amount=1030000,
            ),
            DailyBar(
                code="123001",
                date=date(2024, 1, 3),
                open=103.0,
                high=108.0,
                low=102.0,
                close=107.0,
                volume=12000,
                amount=1284000,
            ),
        ]
        inserted = store.insert_daily_bars(bars)
        assert inserted == 2

        result = store.get_daily_bars("123001")
        assert len(result) == 2
        assert result[0].date == date(2024, 1, 2)
        assert result[1].close == 107.0

    def test_date_range_filter(self, store: DataStore) -> None:
        bars = [
            DailyBar(
                code="123001",
                date=date(2024, 1, d),
                open=100.0,
                high=105.0,
                low=99.0,
                close=103.0,
                volume=10000,
                amount=1030000,
            )
            for d in range(1, 11)
        ]
        store.insert_daily_bars(bars)

        result = store.get_daily_bars("123001", start=date(2024, 1, 3), end=date(2024, 1, 7))
        assert len(result) == 5

    def test_ignore_duplicates(self, store: DataStore) -> None:
        bar = DailyBar(
            code="123001",
            date=date(2024, 1, 2),
            open=100.0,
            high=105.0,
            low=99.0,
            close=103.0,
            volume=10000,
            amount=1030000,
        )
        store.insert_daily_bars([bar])
        store.insert_daily_bars([bar])
        assert store.get_bar_count("123001") == 1

    def test_bar_count(self, store: DataStore) -> None:
        assert store.get_bar_count("123001") == 0


class TestSnapshots:
    def test_insert_and_get_latest(self, store: DataStore) -> None:
        snapshots = [
            BondSnapshot(
                code="123001",
                name="测试转债",
                price=105.0,
                stock_code="600001",
                stock_price=11.0,
                conversion_price=10.0,
                conversion_value=110.0,
                premium_rate=-0.045,
                volume_cny=80000000,
                ytm=0.02,
                remaining_years=3.5,
                timestamp=datetime(2024, 1, 2, 10, 0),
            ),
            BondSnapshot(
                code="123001",
                name="测试转债",
                price=106.0,
                stock_code="600001",
                stock_price=11.5,
                conversion_price=10.0,
                conversion_value=115.0,
                premium_rate=-0.078,
                volume_cny=90000000,
                ytm=0.015,
                remaining_years=3.5,
                timestamp=datetime(2024, 1, 3, 10, 0),
            ),
        ]
        inserted = store.insert_snapshots(snapshots)
        assert inserted == 2

        latest = store.get_latest_snapshots()
        assert len(latest) == 1
        assert latest[0].price == 106.0


class TestTrades:
    def test_insert_and_get(self, store: DataStore) -> None:
        run_id = store.create_backtest_run(date(2024, 1, 1), date(2024, 12, 31), {"test": True})
        trades = [
            Trade(
                code="123001",
                side=Side.BUY,
                shares=100,
                price=105.0,
                cost=1.05,
                timestamp=datetime(2024, 1, 2, 10, 0),
                signal_composite=1.8,
            ),
            Trade(
                code="123001",
                side=Side.SELL,
                shares=100,
                price=107.0,
                cost=1.07,
                timestamp=datetime(2024, 1, 2, 14, 30),
                signal_composite=-0.9,
            ),
        ]
        store.insert_trades(trades, backtest_run_id=run_id)

        result = store.get_trades(run_id)
        assert len(result) == 2
        assert result[0].side == Side.BUY
        assert result[1].price == 107.0


class TestBacktestRuns:
    def test_create_and_update(self, store: DataStore) -> None:
        from src.models import BacktestResult

        run_id = store.create_backtest_run(date(2024, 1, 1), date(2024, 12, 31), {"strategy": "cb_t0"})
        assert run_id > 0

        result = BacktestResult(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
            total_return=0.18,
            annualized_return=0.18,
            max_drawdown=0.025,
            sharpe_ratio=3.2,
            win_rate=0.62,
            total_trades=1500,
        )
        store.update_backtest_run(run_id, result)


class TestDailyPnl:
    def test_insert(self, store: DataStore) -> None:
        run_id = store.create_backtest_run(date(2024, 1, 1), date(2024, 12, 31), {})
        store.insert_daily_pnl(
            run_id, date(2024, 1, 2), pnl=500.0, cumulative_pnl=500.0, capital=2000500.0, trade_count=12
        )
