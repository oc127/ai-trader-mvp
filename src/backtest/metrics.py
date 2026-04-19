from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class BacktestMetrics:
    total_return: float
    annualized_return: float
    sharpe_ratio: float
    max_drawdown: float
    win_rate: float
    total_trades: int
    total_funding_earned: float
    avg_holding_hours: float
    days: int

    def summary(self) -> str:
        return (
            f"--- Backtest Results ({self.days} days) ---\n"
            f"Total Return:      {self.total_return:+.2%}\n"
            f"Annualized Return: {self.annualized_return:+.2%}\n"
            f"Sharpe Ratio:      {self.sharpe_ratio:.2f}\n"
            f"Max Drawdown:      {self.max_drawdown:.2%}\n"
            f"Win Rate:          {self.win_rate:.2%}\n"
            f"Total Trades:      {self.total_trades}\n"
            f"Funding Earned:    ${self.total_funding_earned:.2f}\n"
            f"Avg Hold (hours):  {self.avg_holding_hours:.1f}\n"
        )


def compute_metrics(
    equity_curve: list[float], funding_earned: float, num_trades: int, holding_hours: list[float]
) -> BacktestMetrics:
    if len(equity_curve) < 2:
        return BacktestMetrics(0, 0, 0, 0, 0, 0, 0, 0, 0)

    initial = equity_curve[0]
    final = equity_curve[-1]
    total_return = (final - initial) / initial if initial else 0
    days = len(equity_curve)
    annualized = (1 + total_return) ** (365 / max(days, 1)) - 1

    returns = []
    for i in range(1, len(equity_curve)):
        r = (equity_curve[i] - equity_curve[i - 1]) / equity_curve[i - 1] if equity_curve[i - 1] else 0
        returns.append(r)

    if returns:
        mean_r = sum(returns) / len(returns)
        var_r = sum((r - mean_r) ** 2 for r in returns) / len(returns)
        std_r = math.sqrt(var_r) if var_r > 0 else 1e-10
        sharpe = (mean_r / std_r) * math.sqrt(365)
    else:
        sharpe = 0

    peak = equity_curve[0]
    max_dd = 0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak if peak else 0
        max_dd = max(max_dd, dd)

    wins = sum(1 for r in returns if r > 0)
    win_rate = wins / len(returns) if returns else 0

    avg_hold = sum(holding_hours) / len(holding_hours) if holding_hours else 0

    return BacktestMetrics(
        total_return=total_return,
        annualized_return=annualized,
        sharpe_ratio=sharpe,
        max_drawdown=max_dd,
        win_rate=win_rate,
        total_trades=num_trades,
        total_funding_earned=funding_earned,
        avg_holding_hours=avg_hold,
        days=days,
    )
