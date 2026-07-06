"""Tests for strategy noise detector."""

from __future__ import annotations

from src.quant.noise_detector import BacktestTrade, analyze_trades, backtest_momentum, format_stats_report


def _make_trade(pnl: float) -> BacktestTrade:
    return BacktestTrade(
        symbol="BTC", entry_date="2025-01-01", exit_date="2025-01-05",
        entry_price=50000, exit_price=50000 * (1 + pnl),
        pnl_pct=pnl, is_win=pnl > 0,
    )


def test_empty_trades():
    s = analyze_trades([])
    assert s.total_trades == 0
    assert s.verdict == "No trades"


def test_winning_strategy():
    trades = [_make_trade(0.05)] * 20 + [_make_trade(-0.02)] * 10
    s = analyze_trades(trades)
    assert s.total_trades == 30
    assert s.wins == 20
    assert s.losses == 10
    assert abs(s.win_rate - 2 / 3) < 0.01
    assert s.expectancy_pct > 0
    assert s.profit_factor > 1


def test_small_sample_warning():
    trades = [_make_trade(0.10)] * 5
    s = analyze_trades(trades)
    assert "Insufficient" in s.verdict


def test_significant_strategy():
    trades = [_make_trade(0.03)] * 40 + [_make_trade(-0.01)] * 10
    s = analyze_trades(trades)
    assert s.total_trades == 50
    assert s.t_stat > 0


def test_random_not_significant():
    trades = [_make_trade(0.01 if i % 2 == 0 else -0.01) for i in range(50)]
    s = analyze_trades(trades)
    assert abs(s.expectancy_pct) < 0.01


def test_max_consecutive_losses():
    trades = [
        _make_trade(0.05), _make_trade(-0.02), _make_trade(-0.03),
        _make_trade(-0.01), _make_trade(0.04), _make_trade(-0.02),
    ]
    s = analyze_trades(trades)
    assert s.max_consecutive_losses == 3


def test_backtest_momentum():
    prices = {"BTC": [(f"2025-01-{d:02d}", 50000 + d * 100) for d in range(1, 31)]}
    trades = backtest_momentum(prices, lookback=5, hold_days=3)
    assert len(trades) > 0
    assert all(t.symbol == "BTC" for t in trades)


def test_format_report():
    trades = [_make_trade(0.05)] * 30 + [_make_trade(-0.03)] * 15
    s = analyze_trades(trades)
    report = format_stats_report(s)
    assert "Noise Detection" in report
    assert "Hypothesis" in report
