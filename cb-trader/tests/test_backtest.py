from __future__ import annotations

from datetime import date, datetime

from src.backtest.cost_model import CostModel
from src.backtest.metrics import calc_metrics
from src.data.store import DataStore
from src.execution.paper import PaperExecutor
from src.models import BacktestResult, DailyBar, Side, Trade


class TestCostModel:
    def setup_method(self) -> None:
        self.cm = CostModel(
            {
                "cost": {
                    "commission_rate": 0.0001,
                    "slippage_bps": 2,
                    "min_commission": 0.1,
                }
            }
        )

    def test_commission(self) -> None:
        assert self.cm.calc_commission(100_000) == 10.0

    def test_min_commission(self) -> None:
        assert self.cm.calc_commission(100) == 0.1

    def test_slippage(self) -> None:
        slip = self.cm.calc_slippage(120.0)
        assert abs(slip - 0.024) < 1e-10

    def test_total_cost(self) -> None:
        cost = self.cm.calc_total_cost(120.0, 100)
        commission = max(120.0 * 100 * 0.0001, 0.1)
        slippage = 0.024 * 100
        assert abs(cost - (commission + slippage)) < 1e-10

    def test_execution_price_buy(self) -> None:
        price = self.cm.execution_price(120.0, is_buy=True)
        assert price > 120.0

    def test_execution_price_sell(self) -> None:
        price = self.cm.execution_price(120.0, is_buy=False)
        assert price < 120.0


class TestPaperExecutor:
    def setup_method(self) -> None:
        self.executor = PaperExecutor({"cost": {"commission_rate": 0.0001, "slippage_bps": 2, "min_commission": 0.1}})

    def test_execute_buy(self) -> None:
        trade = self.executor.execute("123001", Side.BUY, 100, 120.0, timestamp=datetime(2024, 1, 2, 10, 0))
        assert trade is not None
        assert trade.side == Side.BUY
        assert trade.shares == 100
        assert trade.price > 120.0

    def test_execute_sell(self) -> None:
        trade = self.executor.execute("123001", Side.SELL, 100, 120.0, timestamp=datetime(2024, 1, 2, 14, 30))
        assert trade is not None
        assert trade.price < 120.0

    def test_rejects_zero_shares(self) -> None:
        assert self.executor.execute("123001", Side.BUY, 0, 120.0) is None

    def test_rejects_zero_price(self) -> None:
        assert self.executor.execute("123001", Side.BUY, 100, 0) is None

    def test_tracks_trades(self) -> None:
        self.executor.execute("123001", Side.BUY, 100, 120.0, timestamp=datetime(2024, 1, 2, 10, 0))
        self.executor.execute("123001", Side.SELL, 100, 125.0, timestamp=datetime(2024, 1, 2, 14, 30))
        assert len(self.executor.get_trades()) == 2

    def test_reset(self) -> None:
        self.executor.execute("123001", Side.BUY, 100, 120.0, timestamp=datetime(2024, 1, 2, 10, 0))
        self.executor.reset()
        assert len(self.executor.get_trades()) == 0


class TestMetrics:
    def test_basic_metrics(self) -> None:
        trades = [
            Trade(
                code="123001", side=Side.BUY, shares=100, price=100.0, cost=1.0, timestamp=datetime(2024, 1, 2, 10, 0)
            ),
            Trade(
                code="123001", side=Side.SELL, shares=100, price=105.0, cost=1.0, timestamp=datetime(2024, 1, 2, 14, 30)
            ),
            Trade(
                code="123001", side=Side.BUY, shares=100, price=103.0, cost=1.0, timestamp=datetime(2024, 1, 3, 10, 0)
            ),
            Trade(
                code="123001", side=Side.SELL, shares=100, price=101.0, cost=1.0, timestamp=datetime(2024, 1, 3, 14, 30)
            ),
        ]
        daily_pnl = [498.0, -202.0]
        result = calc_metrics(daily_pnl, trades, initial_capital=2_000_000)

        assert result.total_return > 0
        assert result.total_trades == 4
        assert result.win_rate == 0.5

    def test_empty_pnl(self) -> None:
        result = calc_metrics([], [], initial_capital=2_000_000)
        assert result.total_return == 0.0

    def test_drawdown_calculation(self) -> None:
        daily_pnl = [1000, 1000, -3000, -2000, 500]
        result = calc_metrics(daily_pnl, [], initial_capital=100_000)
        assert result.max_drawdown > 0

    def test_sharpe_varied_pnl(self) -> None:
        daily_pnl = [100.0, -50.0, 200.0, -100.0, 150.0] * 10
        result = calc_metrics(daily_pnl, [], initial_capital=2_000_000)
        assert result.sharpe_ratio > 0

    def test_all_winning_trades(self) -> None:
        trades = [
            Trade(
                code="123001", side=Side.BUY, shares=100, price=100.0, cost=0.5, timestamp=datetime(2024, 1, 2, 10, 0)
            ),
            Trade(
                code="123001", side=Side.SELL, shares=100, price=110.0, cost=0.5, timestamp=datetime(2024, 1, 2, 14, 30)
            ),
        ]
        result = calc_metrics([999.0], trades, initial_capital=2_000_000)
        assert result.win_rate == 1.0


class TestBacktestEngine:
    def test_runs_with_minimal_data(self, store: DataStore) -> None:
        from src.backtest.engine import BacktestEngine

        bars = [
            DailyBar(
                code="123001",
                date=date(2024, 1, d),
                open=100.0 + d,
                high=105.0 + d,
                low=99.0 + d,
                close=103.0 + d,
                volume=10000,
                amount=50_000_000,
            )
            for d in range(1, 31)
        ]
        stock_bars = [
            DailyBar(
                code="600001",
                date=date(2024, 1, d),
                open=10.0,
                high=10.5,
                low=9.9,
                close=10.0 + d * 0.1,
                volume=500000,
                amount=5_000_000,
            )
            for d in range(1, 31)
        ]
        store.insert_daily_bars(bars)
        store.insert_daily_bars(stock_bars)

        config = {
            "strategy": {
                "weights": {
                    "stock_lead": 0.30,
                    "premium_revert": 0.25,
                    "intraday_regime": 0.20,
                    "volume_anomaly": 0.15,
                    "redemption": 0.10,
                },
                "thresholds": {"strong_buy": 1.5, "buy": 0.8, "sell": -0.8, "strong_sell": -1.5},
            },
            "risk": {
                "max_daily_loss": 10000,
                "max_daily_loss_pct": 0.005,
                "max_daily_trades": 200,
                "halt_after_consecutive_losses": 3,
                "max_capital_usage": 0.80,
                "max_per_bond": 200000,
                "max_single_loss_pct": 0.01,
            },
            "position": {"total_capital": 2_000_000, "max_positions": 10, "liquidity_limit_pct": 0.01},
            "cost": {"commission_rate": 0.0001, "slippage_bps": 2, "min_commission": 0.1},
        }

        engine = BacktestEngine(config, store)
        result = engine.run(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 30),
            bond_codes=["123001"],
            stock_codes={"123001": "600001"},
        )
        assert isinstance(result, BacktestResult)
        assert result.start_date == date(2024, 1, 1)
        assert result.end_date == date(2024, 1, 30)

    def test_handles_no_data(self, store: DataStore) -> None:
        from src.backtest.engine import BacktestEngine

        config = {
            "strategy": {"weights": {}, "thresholds": {}},
            "risk": {
                "max_daily_loss": 10000,
                "max_daily_loss_pct": 0.005,
                "max_daily_trades": 200,
                "halt_after_consecutive_losses": 3,
                "max_capital_usage": 0.80,
                "max_per_bond": 200000,
                "max_single_loss_pct": 0.01,
            },
            "position": {"total_capital": 2_000_000, "max_positions": 10, "liquidity_limit_pct": 0.01},
            "cost": {"commission_rate": 0.0001, "slippage_bps": 2, "min_commission": 0.1},
        }
        engine = BacktestEngine(config, store)
        result = engine.run(date(2024, 1, 1), date(2024, 12, 31), [], {})
        assert result.total_trades == 0
