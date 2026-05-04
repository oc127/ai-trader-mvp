from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum


class Action(Enum):
    STRONG_BUY = "STRONG_BUY"
    BUY = "BUY"
    HOLD = "HOLD"
    SELL = "SELL"
    STRONG_SELL = "STRONG_SELL"


class Side(Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class BondInfo:
    code: str
    name: str
    stock_code: str
    stock_name: str
    conversion_price: float
    maturity_date: date
    issue_date: date
    par_value: float = 100.0


@dataclass(frozen=True)
class DailyBar:
    code: str
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float


@dataclass(frozen=True)
class BondSnapshot:
    code: str
    name: str
    price: float
    stock_code: str
    stock_price: float
    conversion_price: float
    conversion_value: float
    premium_rate: float
    volume_cny: float
    ytm: float
    remaining_years: float
    timestamp: datetime


@dataclass
class Signal:
    code: str
    timestamp: datetime
    stock_lead: float = 0.0
    premium_revert: float = 0.0
    intraday_regime: float = 0.0
    volume_anomaly: float = 0.0
    redemption: float = 0.0
    composite: float = 0.0
    action: Action = Action.HOLD


@dataclass
class Position:
    code: str
    shares: int
    avg_price: float
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    entry_time: datetime = field(default_factory=datetime.now)


@dataclass(frozen=True)
class Trade:
    code: str
    side: Side
    shares: int
    price: float
    cost: float
    timestamp: datetime
    signal_composite: float = 0.0


@dataclass
class BacktestResult:
    start_date: date
    end_date: date
    total_return: float = 0.0
    annualized_return: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    win_rate: float = 0.0
    total_trades: int = 0
    avg_trade_pnl: float = 0.0
    daily_pnl: list[float] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
