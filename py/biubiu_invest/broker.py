"""Broker abstraction for order execution."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Order:
    symbol: str
    side: str  # "buy" | "sell"
    qty: float
    order_type: str = "market"  # "market" | "limit"
    limit_price: Optional[float] = None
    time_in_force: str = "day"  # "day" | "gtc" | "ioc"


@dataclass(frozen=True)
class OrderResult:
    order_id: str
    symbol: str
    side: str
    qty: float
    status: str  # "accepted" | "filled" | "rejected" | ...
    filled_price: Optional[float] = None


@dataclass(frozen=True)
class Position:
    symbol: str
    qty: float
    avg_entry_price: float
    current_price: float
    unrealized_pl: float
    unrealized_plpc: float
    market_value: float


@dataclass(frozen=True)
class AccountInfo:
    equity: float
    cash: float
    buying_power: float
    day_trade_count: int
    pattern_day_trader: bool
    account_blocked: bool


class Broker:
    """Abstract broker interface."""

    name: str

    def get_account(self) -> AccountInfo:
        raise NotImplementedError

    def get_positions(self) -> list[Position]:
        raise NotImplementedError

    def submit_order(self, order: Order) -> OrderResult:
        raise NotImplementedError

    def cancel_order(self, order_id: str) -> None:
        raise NotImplementedError

    def close_position(self, symbol: str) -> OrderResult:
        raise NotImplementedError


class AlpacaBroker(Broker):
    """
    Alpaca Markets broker for US stocks.

    Requires: pip install alpaca-py
    Environment variables: ALPACA_API_KEY, ALPACA_SECRET_KEY
    Set ALPACA_PAPER=false for live trading (default: paper).
    """

    name = "alpaca"

    def __init__(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        paper: bool = True,
    ):
        self.api_key = api_key or os.environ.get("ALPACA_API_KEY", "")
        self.secret_key = secret_key or os.environ.get("ALPACA_SECRET_KEY", "")
        if os.environ.get("ALPACA_PAPER", "").lower() == "false":
            paper = False
        self._paper = paper

        if not self.api_key or not self.secret_key:
            raise RuntimeError(
                "Alpaca credentials required. Set ALPACA_API_KEY and ALPACA_SECRET_KEY."
            )
        try:
            from alpaca.trading.client import TradingClient  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "alpaca-py is not installed. Install it first: pip install alpaca-py"
            ) from e
        self._client = TradingClient(
            self.api_key, self.secret_key, paper=self._paper
        )

    def get_account(self) -> AccountInfo:
        acct = self._client.get_account()
        return AccountInfo(
            equity=float(acct.equity),
            cash=float(acct.cash),
            buying_power=float(acct.buying_power),
            day_trade_count=int(acct.daytrade_count),
            pattern_day_trader=bool(acct.pattern_day_trader),
            account_blocked=bool(acct.account_blocked),
        )

    def get_positions(self) -> list[Position]:
        positions = self._client.get_all_positions()
        return [
            Position(
                symbol=str(p.symbol),
                qty=float(p.qty),
                avg_entry_price=float(p.avg_entry_price),
                current_price=float(p.current_price),
                unrealized_pl=float(p.unrealized_pl),
                unrealized_plpc=float(p.unrealized_plpc),
                market_value=float(p.market_value),
            )
            for p in positions
        ]

    def submit_order(self, order: Order) -> OrderResult:
        from alpaca.trading.enums import OrderSide, TimeInForce  # type: ignore
        from alpaca.trading.requests import (  # type: ignore
            LimitOrderRequest,
            MarketOrderRequest,
        )

        side = OrderSide.BUY if order.side == "buy" else OrderSide.SELL
        tif_map = {
            "day": TimeInForce.DAY,
            "gtc": TimeInForce.GTC,
            "ioc": TimeInForce.IOC,
        }
        tif = tif_map.get(order.time_in_force, TimeInForce.DAY)

        if order.order_type == "limit" and order.limit_price is not None:
            req = LimitOrderRequest(
                symbol=order.symbol,
                qty=order.qty,
                side=side,
                time_in_force=tif,
                limit_price=order.limit_price,
            )
        else:
            req = MarketOrderRequest(
                symbol=order.symbol,
                qty=order.qty,
                side=side,
                time_in_force=tif,
            )

        result = self._client.submit_order(req)
        return OrderResult(
            order_id=str(result.id),
            symbol=str(result.symbol),
            side=order.side,
            qty=float(result.qty or order.qty),
            status=str(result.status.value if hasattr(result.status, "value") else result.status),
            filled_price=float(result.filled_avg_price) if result.filled_avg_price else None,
        )

    def cancel_order(self, order_id: str) -> None:
        self._client.cancel_order_by_id(order_id)

    def close_position(self, symbol: str) -> OrderResult:
        result = self._client.close_position(symbol)
        return OrderResult(
            order_id=str(getattr(result, "id", "")),
            symbol=symbol,
            side="sell",
            qty=float(getattr(result, "qty", 0) or 0),
            status=str(
                result.status.value
                if hasattr(result.status, "value")
                else str(getattr(result, "status", "submitted"))
            ),
        )
