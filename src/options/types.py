"""Data types for Deribit options trading."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class OptionType(Enum):
    CALL = "call"
    PUT = "put"


class OptionDirection(Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass
class Greeks:
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    rho: float = 0.0


@dataclass
class OptionInstrument:
    """A single options contract on Deribit."""
    instrument_name: str       # e.g. "BTC-28JUN26-80000-P"
    underlying: str            # "BTC" or "ETH"
    option_type: OptionType
    strike: float
    expiry_ts: int             # unix timestamp
    settlement: str            # "delivery" or "cash"
    min_trade_amount: float    # minimum contract size
    tick_size: float
    contract_size: float       # 1 for BTC, 1 for ETH

    # live market data (filled by scanner)
    bid: float = 0.0           # in underlying (BTC/ETH)
    ask: float = 0.0
    mark_price: float = 0.0
    iv: float = 0.0            # implied volatility
    greeks: Greeks = field(default_factory=Greeks)
    open_interest: float = 0.0
    volume_24h: float = 0.0
    underlying_price: float = 0.0

    @property
    def mid(self) -> float:
        if self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / 2
        return self.mark_price

    @property
    def premium_usd(self) -> float:
        return self.mid * self.underlying_price

    @property
    def bid_usd(self) -> float:
        return self.bid * self.underlying_price

    @property
    def ask_usd(self) -> float:
        return self.ask * self.underlying_price

    @property
    def otm_pct(self) -> float:
        if self.underlying_price <= 0:
            return 0.0
        if self.option_type == OptionType.PUT:
            return (self.underlying_price - self.strike) / self.underlying_price
        return (self.strike - self.underlying_price) / self.underlying_price

    @property
    def days_to_expiry(self) -> float:
        import time
        return max(0, (self.expiry_ts - time.time()) / 86400)


@dataclass
class Position:
    """An open options position."""
    instrument_name: str
    direction: OptionDirection
    size: float                # positive = long, negative = short
    avg_price: float           # entry price in underlying
    mark_price: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    delta: float = 0.0
    theta: float = 0.0
    underlying: str = ""
    option_type: OptionType = OptionType.PUT
    strike: float = 0.0
    expiry_ts: int = 0


@dataclass
class TradeResult:
    success: bool
    order_id: str = ""
    instrument: str = ""
    direction: str = ""
    price: float = 0.0
    amount: float = 0.0
    premium_usd: float = 0.0
    error: str = ""


@dataclass
class SellerState:
    """Running state for the premium seller bot."""
    cycle_count: int = 0
    trades_today: int = 0
    premium_collected_today: float = 0.0
    total_premium_collected: float = 0.0
    positions_opened: int = 0
    positions_closed: int = 0
    errors_today: int = 0
    halted: bool = False
    halt_reason: str = ""
