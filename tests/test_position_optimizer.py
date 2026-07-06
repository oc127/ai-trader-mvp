"""Tests for position optimizer."""

from __future__ import annotations

from src.quant.expected_value import ev_from_prices
from src.quant.position_optimizer import PositionConstraints, optimize_positions, format_portfolio_report


def test_no_positive_ev():
    results = [ev_from_prices("BAD", entry=100, stop_loss=80, target=105, win_prob=0.2)]
    p = optimize_positions(results, PositionConstraints(total_capital=100000))
    assert len(p.allocations) == 0
    assert p.cash_reserve == 100000
    assert "All cash" in p.verdict


def test_single_position():
    results = [ev_from_prices("BTC", entry=50000, stop_loss=48000, target=56000, win_prob=0.55)]
    p = optimize_positions(results, PositionConstraints(total_capital=100000))
    assert len(p.allocations) == 1
    a = p.allocations[0]
    assert a.symbol == "BTC"
    assert a.units >= 0
    assert p.cash_reserve > 0


def test_multi_position_cap():
    results = [
        ev_from_prices("BTC", entry=50000, stop_loss=45000, target=65000, win_prob=0.6),
        ev_from_prices("ETH", entry=3000, stop_loss=2700, target=4000, win_prob=0.5),
        ev_from_prices("SOL", entry=150, stop_loss=130, target=200, win_prob=0.55),
    ]
    p = optimize_positions(results, PositionConstraints(total_capital=500000, max_single_position_pct=0.30))
    assert len(p.allocations) <= 3
    for a in p.allocations:
        assert a.weight <= 0.31


def test_lot_size():
    results = [ev_from_prices("600718.SH", entry=15, stop_loss=13.5, target=20, win_prob=0.55)]
    p = optimize_positions(results, PositionConstraints(total_capital=100000, lot_size=100))
    if p.allocations:
        assert p.allocations[0].units % 100 == 0


def test_format_report():
    results = [
        ev_from_prices("BTC", entry=50000, stop_loss=45000, target=65000, win_prob=0.6),
        ev_from_prices("ETH", entry=3000, stop_loss=2700, target=4000, win_prob=0.5),
    ]
    p = optimize_positions(results, PositionConstraints(total_capital=200000))
    report = format_portfolio_report(p, capital=200000)
    assert "Optimization" in report
    assert "BTC" in report
