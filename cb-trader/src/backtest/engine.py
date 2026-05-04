from __future__ import annotations

from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd

from src.backtest.cost_model import CostModel
from src.backtest.metrics import calc_metrics
from src.data.store import DataStore
from src.execution.paper import PaperExecutor
from src.logger import get_logger
from src.models import Action, BacktestResult, DailyBar, Position, Side
from src.risk.manager import RiskManager
from src.strategy.composite import CompositeStrategy
from src.strategy.signals import signal_premium_reversion, signal_stock_lead

logger = get_logger(__name__)


class BacktestEngine:
    def __init__(self, config: dict[str, Any], store: DataStore) -> None:
        self.config = config
        self.store = store
        self.strategy = CompositeStrategy(config)
        self.risk_mgr = RiskManager(config)
        self.executor = PaperExecutor(config)
        self.cost_model = CostModel(config)
        self.initial_capital: float = config.get("position", {}).get("total_capital", 2_000_000)

    def run(
        self,
        start_date: date,
        end_date: date,
        bond_codes: list[str],
        stock_codes: dict[str, str],
    ) -> BacktestResult:
        logger.info("Backtest: %s to %s, %d bonds", start_date, end_date, len(bond_codes))

        bond_bars = self._load_bars(bond_codes, start_date, end_date)
        stock_bars = self._load_bars(list(stock_codes.values()), start_date, end_date)

        all_dates = sorted({bar.date for bars in bond_bars.values() for bar in bars})
        all_dates = [d for d in all_dates if start_date <= d <= end_date]

        if not all_dates:
            logger.warning("No trading dates found")
            return BacktestResult(start_date=start_date, end_date=end_date)

        positions: dict[str, Position] = {}
        daily_pnl_list: list[float] = []

        for day in all_dates:
            self.risk_mgr.reset_daily()
            day_pnl = 0.0

            for code in bond_codes:
                bars = bond_bars.get(code, [])
                today_bars = [b for b in bars if b.date == day]
                if not today_bars:
                    continue

                bar = today_bars[0]
                stock_code = stock_codes.get(code, "")
                s_bars = stock_bars.get(stock_code, [])

                signal = self._generate_daily_signal(code, bar, bars, s_bars, day)
                if signal is None:
                    continue

                used_capital = sum(p.shares * p.avg_price for p in positions.values())

                if code in positions:
                    pos = positions[code]
                    pos.current_price = bar.close
                    pos.unrealized_pnl = (bar.close - pos.avg_price) * pos.shares

                    if signal.action in (Action.SELL, Action.STRONG_SELL):
                        trade = self.executor.execute(
                            code,
                            Side.SELL,
                            pos.shares,
                            bar.close,
                            timestamp=datetime(day.year, day.month, day.day, 14, 30),
                            signal_composite=signal.composite,
                        )
                        if trade:
                            pnl = (trade.price - pos.avg_price) * pos.shares - trade.cost
                            day_pnl += pnl
                            self.risk_mgr.on_trade_close(pnl)
                            del positions[code]

                elif signal.action in (Action.BUY, Action.STRONG_BUY):
                    check = self.risk_mgr.check_pre_trade(len(positions), used_capital)
                    if not check.allowed:
                        continue

                    shares = self.risk_mgr.calc_position_size(
                        signal.composite,
                        bar.close,
                        bar.amount,
                        used_capital,
                    )
                    if shares <= 0:
                        continue

                    trade = self.executor.execute(
                        code,
                        Side.BUY,
                        shares,
                        bar.close,
                        timestamp=datetime(day.year, day.month, day.day, 10, 0),
                        signal_composite=signal.composite,
                    )
                    if trade:
                        positions[code] = Position(
                            code=code,
                            shares=shares,
                            avg_price=trade.price,
                            current_price=bar.close,
                            entry_time=trade.timestamp,
                        )

            for code in list(positions.keys()):
                pos = positions[code]
                bars_for_code = bond_bars.get(code, [])
                today_bar = next((b for b in bars_for_code if b.date == day), None)
                if today_bar:
                    pos.current_price = today_bar.close
                    pos.unrealized_pnl = (today_bar.close - pos.avg_price) * pos.shares

            daily_pnl_list.append(day_pnl)

        for code, pos in list(positions.items()):
            last_bar = bond_bars.get(code, [])
            if last_bar:
                last_price = last_bar[-1].close
                trade = self.executor.execute(
                    code,
                    Side.SELL,
                    pos.shares,
                    last_price,
                    timestamp=datetime(end_date.year, end_date.month, end_date.day, 15, 0),
                )
                if trade:
                    pnl = (trade.price - pos.avg_price) * pos.shares - trade.cost
                    if daily_pnl_list:
                        daily_pnl_list[-1] += pnl

        result = calc_metrics(daily_pnl_list, self.executor.get_trades(), self.initial_capital)
        result.start_date = start_date
        result.end_date = end_date

        logger.info(
            "Backtest complete: return=%.2f%%, sharpe=%.2f, drawdown=%.2f%%, trades=%d, win_rate=%.1f%%",
            result.total_return * 100,
            result.sharpe_ratio,
            result.max_drawdown * 100,
            result.total_trades,
            result.win_rate * 100,
        )
        return result

    def _load_bars(self, codes: list[str], start: date, end: date) -> dict[str, list[DailyBar]]:
        result = {}
        for code in codes:
            bars = self.store.get_daily_bars(code, start, end)
            if bars:
                result[code] = bars
        return result

    def _generate_daily_signal(
        self,
        code: str,
        today_bar: DailyBar,
        all_bars: list[DailyBar],
        stock_bars: list[DailyBar],
        current_date: date,
    ):
        idx = next((i for i, b in enumerate(all_bars) if b.date == current_date), None)
        if idx is None or idx < 20:
            return None

        recent_bars = all_bars[max(0, idx - 60) : idx + 1]
        bond_closes = pd.Series([b.close for b in recent_bars])
        bond_returns = bond_closes.pct_change().dropna()

        stock_returns = pd.Series(dtype=float)
        delta = 0.5
        if stock_bars:
            recent_stock = [b for b in stock_bars if b.date <= current_date][-61:]
            if len(recent_stock) > 1:
                stock_closes = pd.Series([b.close for b in recent_stock])
                stock_returns = stock_closes.pct_change().dropna()

                if len(bond_returns) > 1 and len(stock_returns) > 1:
                    min_len = min(len(bond_returns), len(stock_returns))
                    corr = bond_returns.iloc[-min_len:].corr(stock_returns.iloc[-min_len:])
                    delta = max(0.1, min(1.0, corr)) if not np.isnan(corr) else 0.5

        sl = 0.0
        if len(stock_returns) > 10 and len(bond_returns) > 10:
            min_len = min(len(stock_returns), len(bond_returns))
            sl = signal_stock_lead(
                stock_returns.iloc[-min_len:].reset_index(drop=True),
                bond_returns.iloc[-min_len:].reset_index(drop=True),
                delta=delta,
                lookback=3,
                z_window=min(min_len - 3, 50),
            )

        premiums = pd.Series([b.close / today_bar.close - 1 for b in recent_bars])
        pr = signal_premium_reversion(premiums, lookback=min(20, len(premiums) - 1))

        ir = 0.0
        va = 0.0
        rd = 0.0

        return self.strategy.combine(
            code=code,
            timestamp=datetime(current_date.year, current_date.month, current_date.day),
            stock_lead=sl,
            premium_revert=pr,
            intraday_regime=ir,
            volume_anomaly=va,
            redemption=rd,
        )
