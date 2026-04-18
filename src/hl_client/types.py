from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    LIMIT = "limit"
    MARKET = "market"


class OrderStatus(str, Enum):
    PENDING = "pending"
    OPEN = "open"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass
class FundingRate:
    coin: str
    rate: float  # decimal, e.g. 0.0001 = 0.01%
    timestamp: datetime
    premium: float = 0.0

    @property
    def annualized(self) -> float:
        return self.rate * 24 * 365


@dataclass
class OrderRequest:
    coin: str
    side: Side
    size: float
    order_type: OrderType = OrderType.MARKET
    price: float | None = None
    reduce_only: bool = False
    is_spot: bool = False


@dataclass
class OrderResult:
    order_id: str
    coin: str
    side: Side
    size: float
    filled_size: float
    price: float
    status: OrderStatus
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    is_spot: bool = False


@dataclass
class Position:
    coin: str
    size: float  # positive = long, negative = short
    entry_price: float
    mark_price: float
    unrealized_pnl: float
    margin_used: float
    leverage: float
    is_spot: bool = False

    @property
    def side(self) -> Side:
        return Side.BUY if self.size > 0 else Side.SELL

    @property
    def notional(self) -> float:
        return abs(self.size) * self.mark_price


@dataclass
class AccountState:
    equity: float
    available_balance: float
    margin_used: float
    positions: list[Position] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def margin_utilization(self) -> float:
        if self.equity == 0:
            return 0.0
        return self.margin_used / self.equity


@dataclass
class SpotBalance:
    coin: str
    total: float
    available: float
