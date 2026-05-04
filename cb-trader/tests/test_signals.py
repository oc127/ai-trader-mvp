from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from src.models import Action
from src.strategy.composite import CompositeStrategy
from src.strategy.signals import (
    signal_intraday_regime,
    signal_premium_reversion,
    signal_redemption_proximity,
    signal_stock_lead,
    signal_volume_anomaly,
)


class TestStockLeadSignal:
    def test_returns_zero_with_insufficient_data(self) -> None:
        short = pd.Series([0.001] * 10)
        assert signal_stock_lead(short, short, delta=0.5) == 0.0

    def test_positive_when_stock_up_bond_flat(self) -> None:
        np.random.seed(42)
        n = 600
        stock_returns = pd.Series(np.random.normal(0.001, 0.01, n))
        bond_returns = pd.Series(np.random.normal(0.0, 0.005, n))

        stock_returns.iloc[-5:] = 0.02
        bond_returns.iloc[-5:] = 0.001

        result = signal_stock_lead(stock_returns, bond_returns, delta=0.8)
        assert result > 0

    def test_negative_when_stock_down_bond_flat(self) -> None:
        np.random.seed(42)
        n = 600
        stock_returns = pd.Series(np.random.normal(0.0, 0.01, n))
        bond_returns = pd.Series(np.random.normal(0.0, 0.005, n))

        stock_returns.iloc[-5:] = -0.02
        bond_returns.iloc[-5:] = -0.001

        result = signal_stock_lead(stock_returns, bond_returns, delta=0.8)
        assert result < 0

    def test_clipped_to_range(self) -> None:
        np.random.seed(42)
        n = 600
        stock_returns = pd.Series(np.random.normal(0.0, 0.01, n))
        bond_returns = pd.Series(np.random.normal(0.0, 0.005, n))
        result = signal_stock_lead(stock_returns, bond_returns, delta=0.5)
        assert -5.0 <= result <= 5.0


class TestPremiumReversionSignal:
    def test_returns_zero_with_insufficient_data(self) -> None:
        assert signal_premium_reversion(pd.Series([0.1] * 5)) == 0.0

    def test_positive_when_premium_below_mean(self) -> None:
        premiums = pd.Series([0.15] * 25 + [0.05])
        result = signal_premium_reversion(premiums, lookback=20)
        assert result > 0

    def test_negative_when_premium_above_mean(self) -> None:
        premiums = pd.Series([0.10] * 25 + [0.25])
        result = signal_premium_reversion(premiums, lookback=20)
        assert result < 0


class TestIntradayRegimeSignal:
    def test_momentum_period_follows_trend(self) -> None:
        prices = pd.Series([100.0, 101.0, 102.0, 103.0, 104.0])
        volumes = pd.Series([1000, 1200, 1100, 1300, 1500])

        result = signal_intraday_regime(prices, volumes, hour=9, minute=35)
        assert result > 0

    def test_reversion_period_fades_vwap(self) -> None:
        prices = pd.Series([100.0, 101.0, 102.0, 103.0, 104.0])
        volumes = pd.Series([5000, 1000, 1000, 1000, 1000])

        result = signal_intraday_regime(prices, volumes, hour=10, minute=30)
        assert result < 0

    def test_no_signal_near_close(self) -> None:
        prices = pd.Series([100.0, 105.0])
        volumes = pd.Series([1000, 2000])
        result = signal_intraday_regime(prices, volumes, hour=14, minute=55)
        assert result == 0.0

    def test_empty_data(self) -> None:
        assert signal_intraday_regime(pd.Series(dtype=float), pd.Series(dtype=float), hour=10, minute=0) == 0.0


class TestVolumeAnomalySignal:
    def test_no_signal_normal_volume(self) -> None:
        volumes = pd.Series([1000.0] * 35)
        prices = pd.Series([100.0] * 35)
        result = signal_volume_anomaly(volumes, prices)
        assert result == 0.0

    def test_sell_on_volume_spike_up(self) -> None:
        volumes = pd.Series([1000.0] * 34 + [5000.0])
        prices = pd.Series([100.0] * 29 + [100.0, 100.2, 100.5, 100.8, 101.0, 101.5])
        result = signal_volume_anomaly(volumes, prices)
        assert result == -1.0

    def test_buy_on_volume_spike_down(self) -> None:
        volumes = pd.Series([1000.0] * 34 + [5000.0])
        prices = pd.Series([100.0] * 29 + [100.0, 99.8, 99.5, 99.2, 99.0, 98.5])
        result = signal_volume_anomaly(volumes, prices)
        assert result == 1.0

    def test_insufficient_data(self) -> None:
        assert signal_volume_anomaly(pd.Series([1000.0] * 5), pd.Series([100.0] * 5)) == 0.0


class TestRedemptionProximitySignal:
    def test_near_trigger_bearish(self) -> None:
        result = signal_redemption_proximity(
            stock_price=14.0, conversion_price=10.0, days_above_130pct=12, trigger_days=15
        )
        assert result == -1.0

    def test_failed_trigger_bullish(self) -> None:
        result = signal_redemption_proximity(
            stock_price=12.0, conversion_price=10.0, days_above_130pct=9, trigger_days=15
        )
        assert result == 1.0

    def test_no_signal_far_from_trigger(self) -> None:
        result = signal_redemption_proximity(
            stock_price=9.0, conversion_price=10.0, days_above_130pct=2, trigger_days=15
        )
        assert result == 0.0

    def test_zero_conversion_price(self) -> None:
        result = signal_redemption_proximity(
            stock_price=10.0, conversion_price=0.0, days_above_130pct=10, trigger_days=15
        )
        assert result == 0.0


class TestCompositeStrategy:
    def setup_method(self) -> None:
        self.config = {
            "strategy": {
                "weights": {
                    "stock_lead": 0.30,
                    "premium_revert": 0.25,
                    "intraday_regime": 0.20,
                    "volume_anomaly": 0.15,
                    "redemption": 0.10,
                },
                "thresholds": {
                    "strong_buy": 1.5,
                    "buy": 0.8,
                    "sell": -0.8,
                    "strong_sell": -1.5,
                },
            }
        }
        self.cs = CompositeStrategy(self.config)

    def test_strong_buy(self) -> None:
        sig = self.cs.combine(
            "123001",
            datetime(2024, 1, 1),
            stock_lead=4.0,
            premium_revert=3.0,
            intraday_regime=2.0,
            volume_anomaly=1.0,
            redemption=1.0,
        )
        assert sig.action == Action.STRONG_BUY
        assert sig.composite > 1.5

    def test_strong_sell(self) -> None:
        sig = self.cs.combine(
            "123001",
            datetime(2024, 1, 1),
            stock_lead=-4.0,
            premium_revert=-3.0,
            intraday_regime=-2.0,
            volume_anomaly=-1.0,
            redemption=-1.0,
        )
        assert sig.action == Action.STRONG_SELL
        assert sig.composite < -1.5

    def test_hold_near_zero(self) -> None:
        sig = self.cs.combine(
            "123001",
            datetime(2024, 1, 1),
            stock_lead=0.1,
            premium_revert=-0.1,
            intraday_regime=0.0,
            volume_anomaly=0.0,
            redemption=0.0,
        )
        assert sig.action == Action.HOLD

    def test_buy(self) -> None:
        sig = self.cs.combine(
            "123001",
            datetime(2024, 1, 1),
            stock_lead=2.5,
            premium_revert=2.0,
            intraday_regime=1.0,
            volume_anomaly=0.0,
            redemption=0.0,
        )
        assert sig.action in (Action.BUY, Action.STRONG_BUY)
        assert sig.composite >= 0.8

    def test_weights_applied(self) -> None:
        sig = self.cs.combine(
            "123001",
            datetime(2024, 1, 1),
            stock_lead=1.0,
            premium_revert=0.0,
            intraday_regime=0.0,
            volume_anomaly=0.0,
            redemption=0.0,
        )
        assert abs(sig.composite - 0.30) < 1e-10

    def test_signal_fields_populated(self) -> None:
        sig = self.cs.combine(
            "123001",
            datetime(2024, 1, 1, 10, 30),
            stock_lead=1.0,
            premium_revert=2.0,
            intraday_regime=0.5,
            volume_anomaly=-1.0,
            redemption=0.0,
        )
        assert sig.code == "123001"
        assert sig.stock_lead == 1.0
        assert sig.premium_revert == 2.0
        assert sig.intraday_regime == 0.5
        assert sig.volume_anomaly == -1.0
        assert sig.redemption == 0.0
