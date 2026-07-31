"""Backtest engine for the Polymarket HFT market maker."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from src.logger import get_logger
from src.polymarket.market_maker import HighFreqMarketMaker
from src.polymarket.types import Market

log = get_logger(__name__)


@dataclass
class BacktestResult:
    total_pnl: float
    num_trades: int
    win_rate: float
    max_drawdown: float
    sharpe_ratio: float
    pnl_curve: list[float]
    exposure_curve: list[float]
    inventory_curve: list[float]
    num_flattens: int
    avg_spread_captured: float


class MarketMakerBacktest:

    def __init__(self, cfg: dict) -> None:
        self._cfg = cfg

    @staticmethod
    def generate_price_path(
        mid: float,
        n_ticks: int,
        volatility: float,
        mean_reversion: float,
    ) -> list[float]:
        """Generate a mean-reverting price path (Ornstein-Uhlenbeck discretization).

        Each tick: dp = mean_reversion * (mid - p) + volatility * N(0,1)
        Prices clamped to [0.05, 0.95].
        """
        prices = [mid]
        p = mid
        for _ in range(n_ticks - 1):
            drift = mean_reversion * (mid - p)
            noise = volatility * random.gauss(0, 1)
            p += drift + noise
            p = max(0.05, min(0.95, p))
            prices.append(round(p, 6))
        return prices

    def simulate(
        self,
        n_ticks: int,
        mid_price: float,
        volatility: float,
        book_spread: float,
        fill_probability: float,
        seed: int | None = None,
        mean_reversion: float = 0.05,
        flatten_interval: int = 50,
        stale_ticks: int = 100,
        flatten_slippage: float = 0.01,
    ) -> BacktestResult:
        """Run a full market-making simulation.

        Args:
            n_ticks: Number of price ticks to simulate.
            mid_price: Starting mid price (and OU mean).
            volatility: Per-tick price volatility.
            book_spread: Observable book spread passed to generate_quotes.
            fill_probability: Probability of a market order arriving each tick.
            seed: RNG seed for reproducibility.
            mean_reversion: OU mean-reversion speed.
            flatten_interval: Check for stale inventory every N ticks.
            stale_ticks: Ticks without a fill before auto-flatten triggers.
            flatten_slippage: Price slippage on flatten orders.
        """
        if seed is not None:
            random.seed(seed)

        maker = HighFreqMarketMaker(self._cfg)
        prices = self.generate_price_path(mid_price, n_ticks, volatility, mean_reversion)

        cid = "bt-cond-001"
        yes_tid = "bt-yes-001"
        no_tid = "bt-no-001"
        question = "Backtest Simulation Market"

        pnl_curve: list[float] = []
        exposure_curve: list[float] = []
        inventory_curve: list[float] = []
        spreads_captured: list[float] = []
        num_trades = 0
        num_flattens = 0
        last_fill_tick = -stale_ticks

        for tick in range(n_ticks):
            price = prices[tick]

            market = Market(
                condition_id=cid,
                question=question,
                slug="bt-sim",
                yes_token_id=yes_tid,
                no_token_id=no_tid,
                yes_price=price,
                no_price=round(1.0 - price, 6),
                volume=1_000_000,
                volume_24h=50_000,
                liquidity=100_000,
            )

            # Reset time-based pause each tick (simulation has no real clock)
            maker._paused_until = 0

            quote = maker.generate_quotes(market, book_spread)

            if quote is not None and random.random() < fill_probability:
                inv = maker._inventory.get(cid)
                has_inventory = inv is not None and inv.yes_shares > 0.5
                is_market_buy = random.random() < 0.5

                if is_market_buy and has_inventory:
                    sell_size = min(quote.ask_size, inv.yes_shares)
                    maker.on_fill(
                        cid, question, yes_tid, no_tid,
                        "SELL", yes_tid, quote.ask_price, sell_size,
                    )
                    num_trades += 1
                    last_fill_tick = tick
                    spreads_captured.append(quote.spread)
                elif not is_market_buy:
                    maker.on_fill(
                        cid, question, yes_tid, no_tid,
                        "BUY", yes_tid, quote.bid_price, quote.bid_size,
                    )
                    num_trades += 1
                    last_fill_tick = tick
                    spreads_captured.append(quote.spread)

            # Auto-flatten stale inventory
            if tick > 0 and tick % flatten_interval == 0:
                inv = maker._inventory.get(cid)
                if inv and not inv.is_flat and (tick - last_fill_tick) >= stale_ticks:
                    if inv.yes_shares > 0.5:
                        fp = max(0.01, price - flatten_slippage)
                        maker.on_fill(
                            cid, question, yes_tid, no_tid,
                            "SELL", yes_tid, fp, inv.yes_shares,
                        )
                        num_flattens += 1
                        last_fill_tick = tick
                        log.debug(
                            "Tick %d: auto-flatten %.1f YES shares at %.4f",
                            tick, inv.yes_shares, fp,
                        )
                    elif inv.no_shares > 0.5:
                        fp = max(0.01, (1.0 - price) - flatten_slippage)
                        maker.on_fill(
                            cid, question, yes_tid, no_tid,
                            "SELL", no_tid, fp, inv.no_shares,
                        )
                        num_flattens += 1
                        last_fill_tick = tick
                        log.debug(
                            "Tick %d: auto-flatten %.1f NO shares at %.4f",
                            tick, inv.no_shares, fp,
                        )

            # Record curves
            pnl_curve.append(maker.daily_pnl)
            inv = maker._inventory.get(cid)
            if inv:
                exposure_curve.append(inv.abs_exposure)
                inventory_curve.append(inv.yes_shares - inv.no_shares)
            else:
                exposure_curve.append(0.0)
                inventory_curve.append(0.0)

        status = maker.status()
        total_pnl = maker.daily_pnl
        win_rate = status["win_rate"]
        max_dd = _max_drawdown(pnl_curve)
        sharpe = _sharpe_ratio(pnl_curve)
        avg_spread = (
            sum(spreads_captured) / len(spreads_captured)
            if spreads_captured
            else 0.0
        )

        result = BacktestResult(
            total_pnl=round(total_pnl, 6),
            num_trades=num_trades,
            win_rate=round(win_rate, 4),
            max_drawdown=round(max_dd, 6),
            sharpe_ratio=round(sharpe, 4),
            pnl_curve=pnl_curve,
            exposure_curve=exposure_curve,
            inventory_curve=inventory_curve,
            num_flattens=num_flattens,
            avg_spread_captured=round(avg_spread, 6),
        )

        log.info(
            "Backtest complete: %d trades, PnL=%.4f, Sharpe=%.4f, MaxDD=%.4f",
            num_trades, total_pnl, sharpe, max_dd,
        )
        return result


def _max_drawdown(pnl_curve: list[float]) -> float:
    if not pnl_curve:
        return 0.0
    peak = pnl_curve[0]
    max_dd = 0.0
    for pnl in pnl_curve:
        if pnl > peak:
            peak = pnl
        dd = peak - pnl
        if dd > max_dd:
            max_dd = dd
    return max_dd


def _sharpe_ratio(pnl_curve: list[float]) -> float:
    if len(pnl_curve) < 2:
        return 0.0
    returns = [pnl_curve[i] - pnl_curve[i - 1] for i in range(1, len(pnl_curve))]
    n = len(returns)
    mean_ret = sum(returns) / n
    variance = sum((r - mean_ret) ** 2 for r in returns) / (n - 1)
    std = math.sqrt(variance) if variance > 0 else 0.0
    if std < 1e-12:
        return 0.0
    return mean_ret / std


def format_report(result: BacktestResult) -> str:
    """Format a backtest result as a readable text report."""
    lines = [
        "=" * 52,
        "  MARKET MAKER BACKTEST REPORT",
        "=" * 52,
        "",
        f"  Total PnL:            ${result.total_pnl:+.4f}",
        f"  Number of Trades:     {result.num_trades}",
        f"  Win Rate:             {result.win_rate:.1%}",
        f"  Max Drawdown:         ${result.max_drawdown:.4f}",
        f"  Sharpe Ratio:         {result.sharpe_ratio:.4f}",
        f"  Avg Spread Captured:  ${result.avg_spread_captured:.4f}",
        f"  Auto-Flattens:        {result.num_flattens}",
    ]

    if result.pnl_curve:
        peak_pnl = max(result.pnl_curve)
        min_pnl = min(result.pnl_curve)
        lines.extend([
            "",
            f"  Peak PnL:             ${peak_pnl:+.4f}",
            f"  Trough PnL:           ${min_pnl:+.4f}",
        ])

    if result.exposure_curve:
        max_exp = max(result.exposure_curve)
        avg_exp = sum(result.exposure_curve) / len(result.exposure_curve)
        lines.extend([
            "",
            f"  Max Exposure:         ${max_exp:.2f}",
            f"  Avg Exposure:         ${avg_exp:.2f}",
        ])

    if result.inventory_curve:
        max_abs_inv = max(abs(v) for v in result.inventory_curve)
        lines.append(f"  Max Abs Inventory:    {max_abs_inv:.2f} shares")

    lines.extend(["", "=" * 52])
    return "\n".join(lines)
