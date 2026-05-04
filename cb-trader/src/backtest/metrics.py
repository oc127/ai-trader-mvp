from __future__ import annotations

import math

import numpy as np

from src.models import BacktestResult, Side, Trade


def calc_metrics(
    daily_pnl: list[float],
    trades: list[Trade],
    initial_capital: float,
    trading_days_per_year: int = 242,
) -> BacktestResult:
    if not daily_pnl:
        return BacktestResult(
            start_date=trades[0].timestamp.date() if trades else None,
            end_date=trades[-1].timestamp.date() if trades else None,
        )

    pnl_array = np.array(daily_pnl)
    cumulative = np.cumsum(pnl_array)

    total_return = cumulative[-1] / initial_capital
    n_days = len(daily_pnl)
    years = n_days / trading_days_per_year
    annualized_return = (1 + total_return) ** (1 / years) - 1 if years > 0 else 0.0

    max_drawdown = _calc_max_drawdown(cumulative, initial_capital)
    sharpe_ratio = _calc_sharpe(pnl_array, initial_capital, trading_days_per_year)

    winning_trades = sum(1 for t in _pair_trades(trades) if t > 0)
    total_pairs = len(list(_pair_trades(trades)))
    win_rate = winning_trades / total_pairs if total_pairs > 0 else 0.0

    avg_trade_pnl = float(np.mean(list(_pair_trades(trades)))) if total_pairs > 0 else 0.0

    return BacktestResult(
        start_date=trades[0].timestamp.date() if trades else None,
        end_date=trades[-1].timestamp.date() if trades else None,
        total_return=total_return,
        annualized_return=annualized_return,
        max_drawdown=max_drawdown,
        sharpe_ratio=sharpe_ratio,
        win_rate=win_rate,
        total_trades=len(trades),
        avg_trade_pnl=avg_trade_pnl,
        daily_pnl=daily_pnl,
        trades=trades,
    )


def _calc_max_drawdown(cumulative_pnl: np.ndarray, initial_capital: float) -> float:
    equity = initial_capital + cumulative_pnl
    peak = np.maximum.accumulate(equity)
    drawdown = (peak - equity) / peak
    return float(np.max(drawdown)) if len(drawdown) > 0 else 0.0


def _calc_sharpe(
    daily_pnl: np.ndarray,
    initial_capital: float,
    trading_days_per_year: int,
) -> float:
    if len(daily_pnl) < 2:
        return 0.0
    daily_returns = daily_pnl / initial_capital
    mean_ret = np.mean(daily_returns)
    std_ret = np.std(daily_returns, ddof=1)
    if std_ret == 0:
        return 0.0
    return float(mean_ret / std_ret * math.sqrt(trading_days_per_year))


def _pair_trades(trades: list[Trade]):
    positions: dict[str, list[Trade]] = {}
    for t in trades:
        if t.side == Side.BUY:
            positions.setdefault(t.code, []).append(t)
        elif t.side == Side.SELL and t.code in positions and positions[t.code]:
            buy = positions[t.code].pop(0)
            pnl = (t.price - buy.price) * min(t.shares, buy.shares) - t.cost - buy.cost
            yield pnl
