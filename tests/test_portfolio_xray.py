"""Tests for portfolio X-ray."""

from __future__ import annotations

import math

from src.quant.portfolio_xray import xray, format_xray_report


def _synth_returns(symbol: str, n: int = 60, drift: float = 0.001, vol: float = 0.02) -> list[tuple[str, float]]:
    rets = []
    for i in range(n):
        noise = vol * math.sin(i * 1.618 + hash(symbol) % 100)
        rets.append((f"2025-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}", drift + noise))
    return rets


def test_single_asset():
    returns = {"BTC": _synth_returns("BTC")}
    px = xray(returns)
    assert "Need >= 2" in px.verdict


def test_two_assets():
    returns = {
        "BTC": _synth_returns("BTC", drift=0.002),
        "ETH": _synth_returns("ETH", drift=-0.001),
    }
    px = xray(returns)
    assert len(px.symbols) == 2
    assert len(px.asset_stats) == 2
    assert len(px.correlations) == 1
    assert px.pca_effective_bets > 0


def test_three_assets():
    returns = {
        "BTC": _synth_returns("BTC", drift=0.003, vol=0.01),
        "ETH": _synth_returns("ETH", drift=0.001, vol=0.03),
        "SOL": _synth_returns("SOL", drift=-0.001, vol=0.02),
    }
    px = xray(returns)
    assert len(px.symbols) == 3
    assert len(px.correlations) == 3
    assert px.concentration_risk in ("low", "moderate", "high")
    assert len(px.pca_components) <= 3


def test_format_report():
    returns = {"X": _synth_returns("X"), "Y": _synth_returns("Y")}
    px = xray(returns)
    report = format_xray_report(px)
    assert "X-Ray" in report
    assert "PCA" in report
