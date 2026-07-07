"""Polymarket data types."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class Outcome(str, Enum):
    YES = "Yes"
    NO = "No"


class OrderStatus(str, Enum):
    LIVE = "LIVE"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    PARTIAL = "PARTIAL"


@dataclass(frozen=True)
class Market:
    condition_id: str
    question: str
    slug: str
    yes_token_id: str
    no_token_id: str
    yes_price: float  # 0–1
    no_price: float  # 0–1
    volume: float  # total traded volume in USDC
    volume_24h: float
    liquidity: float
    end_date: Optional[str] = None
    category: str = ""
    active: bool = True


@dataclass(frozen=True)
class OrderBook:
    market: Market
    bids: list[tuple[float, float]]  # [(price, size), ...]
    asks: list[tuple[float, float]]
    spread: float
    mid_price: float


@dataclass(frozen=True)
class Order:
    order_id: str
    market_condition_id: str
    token_id: str
    side: Side
    price: float
    size: float
    outcome: Outcome
    status: OrderStatus = OrderStatus.LIVE
    filled_size: float = 0.0
    timestamp: str = ""


@dataclass(frozen=True)
class Position:
    market_condition_id: str
    question: str
    outcome: Outcome
    token_id: str
    size: float
    avg_price: float
    current_price: float
    unrealized_pnl: float
    realized_pnl: float = 0.0


@dataclass(frozen=True)
class TradeResult:
    success: bool
    order_id: str = ""
    filled_size: float = 0.0
    avg_fill_price: float = 0.0
    error: str = ""


@dataclass
class BotState:
    """Mutable state for the bot loop."""
    total_pnl: float = 0.0
    trades_today: int = 0
    open_orders: int = 0
    active_positions: int = 0
    last_scan_ts: str = ""
    errors_today: int = 0
    halted: bool = False
    halt_reason: str = ""
    cycle_count: int = 0


@dataclass(frozen=True)
class Opportunity:
    """A detected trading opportunity."""
    market: Market
    outcome: Outcome
    side: Side
    model_prob: float  # our estimated probability
    market_prob: float  # current market price
    edge: float  # model_prob - market_prob (for YES buy)
    ev: float  # expected value per dollar
    kelly_fraction: float
    confidence: str  # "low" / "medium" / "high"
    reason: str = ""
