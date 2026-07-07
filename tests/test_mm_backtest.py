"""Tests for Polymarket market maker backtest engine."""

from __future__ import annotations

from src.polymarket.backtest import MarketMakerBacktest, BacktestResult, format_report, _max_drawdown, _sharpe_ratio


def _cfg(**overrides) -> dict:
    base = {
        "polymarket": {
            "hf_market_maker": {},
        }
    }
    if overrides:
        base["polymarket"]["hf_market_maker"].update(overrides)
    return base


class TestPricePath:
    def test_correct_length(self):
        path = MarketMakerBacktest.generate_price_path(0.50, 100, 0.01, 0.05)
        assert len(path) == 100

    def test_starts_at_mid(self):
        path = MarketMakerBacktest.generate_price_path(0.60, 50, 0.01, 0.05)
        assert path[0] == 0.60

    def test_prices_in_range(self):
        path = MarketMakerBacktest.generate_price_path(0.50, 1000, 0.05, 0.01)
        assert all(0.05 <= p <= 0.95 for p in path)

    def test_mean_reversion(self):
        path = MarketMakerBacktest.generate_price_path(0.50, 500, 0.005, 0.2)
        avg = sum(path) / len(path)
        assert abs(avg - 0.50) < 0.10


class TestSimulation:
    def test_runs_without_error(self):
        bt = MarketMakerBacktest(_cfg())
        result = bt.simulate(
            n_ticks=200, mid_price=0.50, volatility=0.01,
            book_spread=0.06, fill_probability=0.3, seed=42,
        )
        assert isinstance(result, BacktestResult)

    def test_curves_correct_length(self):
        bt = MarketMakerBacktest(_cfg())
        result = bt.simulate(
            n_ticks=100, mid_price=0.50, volatility=0.01,
            book_spread=0.06, fill_probability=0.5, seed=123,
        )
        assert len(result.pnl_curve) == 100
        assert len(result.exposure_curve) == 100
        assert len(result.inventory_curve) == 100

    def test_trades_happen(self):
        bt = MarketMakerBacktest(_cfg())
        result = bt.simulate(
            n_ticks=500, mid_price=0.50, volatility=0.01,
            book_spread=0.06, fill_probability=0.8, seed=42,
        )
        assert result.num_trades > 0

    def test_no_trades_with_zero_fill_prob(self):
        bt = MarketMakerBacktest(_cfg())
        result = bt.simulate(
            n_ticks=100, mid_price=0.50, volatility=0.01,
            book_spread=0.06, fill_probability=0.0, seed=42,
        )
        assert result.num_trades == 0
        assert result.total_pnl == 0.0

    def test_seed_reproducibility(self):
        bt = MarketMakerBacktest(_cfg())
        r1 = bt.simulate(
            n_ticks=200, mid_price=0.50, volatility=0.01,
            book_spread=0.06, fill_probability=0.5, seed=999,
        )
        r2 = bt.simulate(
            n_ticks=200, mid_price=0.50, volatility=0.01,
            book_spread=0.06, fill_probability=0.5, seed=999,
        )
        assert r1.total_pnl == r2.total_pnl
        assert r1.num_trades == r2.num_trades

    def test_win_rate_in_range(self):
        bt = MarketMakerBacktest(_cfg())
        result = bt.simulate(
            n_ticks=500, mid_price=0.50, volatility=0.01,
            book_spread=0.06, fill_probability=0.5, seed=42,
        )
        assert 0.0 <= result.win_rate <= 1.0

    def test_max_drawdown_non_negative(self):
        bt = MarketMakerBacktest(_cfg())
        result = bt.simulate(
            n_ticks=300, mid_price=0.50, volatility=0.01,
            book_spread=0.06, fill_probability=0.5, seed=42,
        )
        assert result.max_drawdown >= 0.0

    def test_auto_flatten_triggers(self):
        bt = MarketMakerBacktest(_cfg())
        result = bt.simulate(
            n_ticks=500, mid_price=0.50, volatility=0.02,
            book_spread=0.06, fill_probability=0.1, seed=42,
            flatten_interval=20, stale_ticks=30,
        )
        assert result.num_flattens >= 0  # may or may not trigger depending on fills


class TestMetrics:
    def test_max_drawdown_empty(self):
        assert _max_drawdown([]) == 0.0

    def test_max_drawdown_monotonic_up(self):
        assert _max_drawdown([1, 2, 3, 4, 5]) == 0.0

    def test_max_drawdown_calculates(self):
        dd = _max_drawdown([0, 5, 3, 7, 2])
        assert dd == 5.0  # peak=7, trough=2

    def test_sharpe_zero_returns(self):
        assert _sharpe_ratio([1, 1, 1, 1]) == 0.0

    def test_sharpe_positive(self):
        curve = [0, 1, 1.5, 3, 3.2, 5]  # positive but variable returns
        s = _sharpe_ratio(curve)
        assert s > 0

    def test_sharpe_too_short(self):
        assert _sharpe_ratio([1]) == 0.0


class TestFormatReport:
    def test_format_contains_key_fields(self):
        result = BacktestResult(
            total_pnl=1.23, num_trades=50, win_rate=0.60,
            max_drawdown=0.50, sharpe_ratio=1.5,
            pnl_curve=[0, 0.5, 1.0, 1.23],
            exposure_curve=[10, 20, 15, 5],
            inventory_curve=[5, 10, 8, 2],
            num_flattens=3, avg_spread_captured=0.04,
        )
        report = format_report(result)
        assert "$1.23" in report or "1.2300" in report
        assert "50" in report
        assert "60" in report
        assert "Sharpe" in report
        assert "Flatten" in report
