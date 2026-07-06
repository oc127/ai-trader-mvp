"""Strategy noise detection via hypothesis testing."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class BacktestTrade:
    symbol: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    pnl_pct: float
    is_win: bool


@dataclass(frozen=True)
class StrategyStats:
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    avg_win_pct: float
    avg_loss_pct: float
    profit_factor: float
    expectancy_pct: float
    max_consecutive_losses: int
    sharpe_approx: float
    t_stat: float
    p_value_approx: float
    is_significant: bool
    verdict: str


def _t_to_p_approx(t: float, df: int) -> float:
    """Two-tailed p-value approximation via normal CDF (no scipy)."""
    if df <= 0:
        return 1.0
    x = abs(t)
    a1, a2, a3, a4, a5 = 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429
    p_const = 0.3275911
    tt = 1.0 / (1.0 + p_const * x)
    y = 1.0 - (((((a5 * tt + a4) * tt) + a3) * tt + a2) * tt + a1) * tt * math.exp(-x * x / 2.0)
    phi = 0.5 * (1.0 + y)
    return 2.0 * (1.0 - phi)


def analyze_trades(trades: list[BacktestTrade], confidence: float = 0.05) -> StrategyStats:
    n = len(trades)
    if n == 0:
        return StrategyStats(
            total_trades=0, wins=0, losses=0, win_rate=0, avg_win_pct=0,
            avg_loss_pct=0, profit_factor=0, expectancy_pct=0,
            max_consecutive_losses=0, sharpe_approx=0, t_stat=0,
            p_value_approx=1.0, is_significant=False, verdict="No trades",
        )

    wins = [t for t in trades if t.is_win]
    losses = [t for t in trades if not t.is_win]
    n_w, n_l = len(wins), len(losses)
    win_rate = n_w / n

    avg_win = sum(t.pnl_pct for t in wins) / n_w if n_w else 0.0
    avg_loss = sum(t.pnl_pct for t in losses) / n_l if n_l else 0.0

    gross_profit = sum(t.pnl_pct for t in wins)
    gross_loss = abs(sum(t.pnl_pct for t in losses))
    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    else:
        profit_factor = 9999.0 if gross_profit > 0 else 0.0

    returns = [t.pnl_pct for t in trades]
    mean_r = sum(returns) / n

    max_cl = cur_cl = 0
    for t in trades:
        if not t.is_win:
            cur_cl += 1
            max_cl = max(max_cl, cur_cl)
        else:
            cur_cl = 0

    if n > 1:
        var = sum((r - mean_r) ** 2 for r in returns) / (n - 1)
        std = math.sqrt(var) if var > 0 else 0.0
    else:
        std = 0.0

    sharpe = (mean_r / std) * math.sqrt(252) if std > 0 else 0.0
    t_stat = (mean_r / (std / math.sqrt(n))) if std > 0 and n > 1 else 0.0
    p_val = _t_to_p_approx(t_stat, n - 1)
    is_sig = p_val < confidence

    if n < 30:
        verdict = f"Insufficient sample ({n} trades), need >= 30 for statistical inference"
    elif not is_sig and mean_r > 0:
        verdict = f"Positive returns but not significant (p={p_val:.3f}) — may be noise/luck"
    elif not is_sig:
        verdict = f"No statistical significance (p={p_val:.3f}) — indistinguishable from random"
    elif is_sig and mean_r > 0:
        verdict = f"Significantly positive (p={p_val:.3f}) — real alpha detected"
    else:
        verdict = f"Significantly negative (p={p_val:.3f}) — strategy is destructive"

    return StrategyStats(
        total_trades=n, wins=n_w, losses=n_l,
        win_rate=round(win_rate, 4),
        avg_win_pct=round(avg_win, 4), avg_loss_pct=round(avg_loss, 4),
        profit_factor=round(profit_factor, 4),
        expectancy_pct=round(mean_r, 4),
        max_consecutive_losses=max_cl,
        sharpe_approx=round(sharpe, 4),
        t_stat=round(t_stat, 4),
        p_value_approx=round(p_val, 6),
        is_significant=is_sig,
        verdict=verdict,
    )


def backtest_momentum(
    prices: dict[str, list[tuple[str, float]]],
    lookback: int = 20,
    hold_days: int = 5,
) -> list[BacktestTrade]:
    """Naive momentum backtest: buy when lookback return > 0, hold for hold_days.

    prices: {symbol: [(date, close), ...]} sorted by date.
    """
    trades: list[BacktestTrade] = []
    for symbol, series in prices.items():
        i = lookback
        while i + hold_days < len(series):
            _, close_now = series[i]
            _, close_lb = series[i - lookback]
            if close_lb <= 0:
                i += 1
                continue
            mom = (close_now / close_lb) - 1.0
            if mom > 0:
                entry_date, entry_price = series[i]
                exit_date, exit_price = series[i + hold_days]
                pnl = (exit_price / entry_price) - 1.0 if entry_price > 0 else 0.0
                trades.append(BacktestTrade(
                    symbol=symbol, entry_date=entry_date, exit_date=exit_date,
                    entry_price=entry_price, exit_price=exit_price,
                    pnl_pct=round(pnl, 6), is_win=pnl > 0,
                ))
                i += hold_days
            else:
                i += 1
    return trades


def format_stats_report(s: StrategyStats) -> str:
    lines = [
        "# Strategy Noise Detection Report",
        "",
        "## Basic Stats",
        f"- Total trades: {s.total_trades}",
        f"- W/L: {s.wins}/{s.losses}",
        f"- Win rate: {s.win_rate:.1%}",
        f"- Avg win: {s.avg_win_pct:+.2%}",
        f"- Avg loss: {s.avg_loss_pct:+.2%}",
        f"- Profit factor: {s.profit_factor:.2f}",
        f"- Expectancy: {s.expectancy_pct:+.2%}",
        f"- Max consec losses: {s.max_consecutive_losses}",
        "",
        "## Risk-Adjusted",
        f"- Sharpe (annualized approx): {s.sharpe_approx:.2f}",
        "",
        "## Hypothesis Test (H0: strategy return = 0)",
        f"- t-statistic: {s.t_stat:.3f}",
        f"- p-value: {s.p_value_approx:.4f}",
        f"- Significant (alpha=0.05): {'YES' if s.is_significant else 'NO'}",
        "",
        f"## Verdict: {s.verdict}",
        "",
    ]
    return "\n".join(lines)
