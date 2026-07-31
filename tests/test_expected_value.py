"""Tests for expected value calculator and Kelly criterion."""

from __future__ import annotations

import pytest

from src.quant.expected_value import EVResult, TradeSetup, compute_ev, ev_from_prices, format_ev_report


def test_basic_long_positive_ev():
    r = ev_from_prices("BTC", entry=50000, stop_loss=48000, target=56000, win_prob=0.55)
    assert r.verdict == "+EV trade"
    assert r.risk_per_unit == 2000
    assert r.reward_per_unit == 6000
    assert abs(r.risk_reward_ratio - 3.0) < 0.01
    # EV = 0.55*6000 - 0.45*2000 = 3300 - 900 = 2400
    assert abs(r.expected_value_per_unit - 2400) < 1
    assert r.kelly_fraction > 0
    assert r.kelly_half > 0
    assert r.kelly_half < r.kelly_fraction


def test_negative_ev():
    r = ev_from_prices("ETH", entry=3000, stop_loss=2500, target=3200, win_prob=0.3)
    # EV = 0.3*200 - 0.7*500 = 60 - 350 = -290
    assert r.verdict == "negative EV — skip"
    assert r.expected_value_per_unit < 0
    assert r.kelly_fraction == 0


def test_kelly_position_sizing():
    r = ev_from_prices("BTC", entry=50000, stop_loss=48000, target=56000, win_prob=0.55, capital=100000)
    assert r.optimal_units is not None
    assert r.optimal_units > 0
    assert r.optimal_position_value is not None
    at_risk = r.optimal_units * r.risk_per_unit
    assert at_risk <= 100000 * (r.kelly_half + 0.01)


def test_short_trade():
    r = ev_from_prices("SOL", entry=150, stop_loss=165, target=120, win_prob=0.5)
    assert r.risk_per_unit == 15
    assert r.reward_per_unit == 30
    assert r.risk_reward_ratio == 2.0
    assert r.verdict == "+EV trade"


def test_breakeven():
    r = ev_from_prices("X", entry=100, stop_loss=90, target=110, win_prob=0.5)
    assert r.verdict == "breakeven"


def test_format_report():
    r = ev_from_prices("BTC", entry=50000, stop_loss=48000, target=56000, win_prob=0.55, capital=100000)
    report = format_ev_report(r, capital=100000)
    assert "BTC" in report
    assert "Kelly" in report
    assert "+EV" in report


def test_invalid_inputs():
    with pytest.raises(ValueError):
        ev_from_prices("X", entry=0, stop_loss=-1, target=5, win_prob=0.5)
    with pytest.raises(ValueError):
        ev_from_prices("X", entry=10, stop_loss=5, target=15, win_prob=1.5)
    with pytest.raises(ValueError):
        ev_from_prices("X", entry=10, stop_loss=12, target=15, win_prob=0.5)
