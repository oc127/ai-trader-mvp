from __future__ import annotations

import numpy as np
import pandas as pd


def signal_stock_lead(
    stock_returns: pd.Series,
    bond_returns: pd.Series,
    delta: float,
    lookback: int = 5,
    z_window: int = 500,
) -> float:
    """
    Stock-bond lead-lag signal.
    When stock moves but bond hasn't followed (scaled by delta), there's a mispricing.
    Returns z-score: positive = bond is cheap, negative = bond is expensive.
    """
    if len(stock_returns) < lookback + z_window or len(bond_returns) < lookback + z_window:
        return 0.0

    stock_move = stock_returns.rolling(lookback).sum()
    expected_bond_move = stock_move * delta
    actual_bond_move = bond_returns.rolling(lookback).sum()
    mispricing = expected_bond_move - actual_bond_move

    mean = mispricing.rolling(z_window).mean()
    std = mispricing.rolling(z_window).std()

    latest_z = (mispricing.iloc[-1] - mean.iloc[-1]) / (std.iloc[-1] + 1e-10)
    return float(np.clip(latest_z, -5.0, 5.0))


def signal_premium_reversion(
    premium_series: pd.Series,
    lookback: int = 20,
) -> float:
    """
    Premium mean reversion signal.
    When premium deviates from its rolling mean, it tends to revert.
    Returns z-score: positive = premium is low (bond is cheap), negative = premium is high.
    """
    if len(premium_series) < lookback + 1:
        return 0.0

    mean = premium_series.rolling(lookback).mean()
    std = premium_series.rolling(lookback).std()

    z = (mean.iloc[-1] - premium_series.iloc[-1]) / (std.iloc[-1] + 1e-10)
    return float(np.clip(z, -5.0, 5.0))


def signal_intraday_regime(
    prices: pd.Series,
    volumes: pd.Series,
    hour: int,
    minute: int,
) -> float:
    """
    Intraday momentum/reversion signal based on time of day.
    Morning open (9:30-10:00): momentum (follow trend).
    Rest of day: mean reversion (fade VWAP deviation).
    Last 10 minutes (14:50-15:00): no signal.
    """
    if len(prices) < 2 or len(volumes) < 2:
        return 0.0

    open_price = prices.iloc[0]
    current_price = prices.iloc[-1]
    intraday_return = (current_price - open_price) / open_price

    total_volume = volumes.sum()
    if total_volume <= 0:
        return 0.0
    vwap = (prices * volumes).sum() / total_volume
    price_vs_vwap = (current_price - vwap) / vwap

    if hour == 14 and minute >= 50:
        return 0.0

    is_momentum_period = (hour == 9 and minute >= 30) or (hour == 13 and minute < 30)

    if is_momentum_period:
        return float(np.clip(intraday_return * 10, -3.0, 3.0))
    else:
        return float(np.clip(-price_vs_vwap * 10, -3.0, 3.0))


def signal_volume_anomaly(
    volume_series: pd.Series,
    price_series: pd.Series,
    lookback: int = 30,
    vol_threshold: float = 3.0,
    price_threshold: float = 0.005,
) -> float:
    """
    Volume anomaly signal.
    When volume spikes with price move, it's likely retail chasing — fade the move.
    """
    if len(volume_series) < lookback + 1 or len(price_series) < 6:
        return 0.0

    vol_ma = volume_series.iloc[-lookback - 1 : -1].mean()
    if vol_ma <= 0:
        return 0.0

    vol_ratio = volume_series.iloc[-1] / vol_ma
    price_change = (price_series.iloc[-1] - price_series.iloc[-6]) / price_series.iloc[-6]

    if vol_ratio < vol_threshold:
        return 0.0

    if price_change > price_threshold:
        return -1.0
    elif price_change < -price_threshold:
        return 1.0

    return 0.0


def signal_redemption_proximity(
    stock_price: float,
    conversion_price: float,
    days_above_130pct: int,
    trigger_days: int = 15,
) -> float:
    """
    Redemption proximity signal.
    When stock is near the forced redemption trigger, premium compresses.
    """
    if conversion_price <= 0:
        return 0.0

    ratio = stock_price / conversion_price
    proximity = days_above_130pct / trigger_days

    if ratio > 1.3 and proximity > 0.7:
        return -1.0
    elif ratio < 1.3 and proximity > 0.5:
        return 1.0

    return 0.0
