"""Paper trading executor for Polymarket — simulates order matching without real funds."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from src.logger import get_logger
from src.polymarket.types import (
    Market,
    Order,
    OrderStatus,
    Outcome,
    Position,
    Side,
    TradeResult,
)

log = get_logger(__name__)


@dataclass
class PaperPosition:
    market_condition_id: str
    question: str
    outcome: Outcome
    token_id: str
    size: float
    avg_price: float
    realized_pnl: float = 0.0


@dataclass
class PaperAccount:
    initial_balance: float = 10000.0
    balance: float = 10000.0
    positions: dict[str, PaperPosition] = field(default_factory=dict)  # token_id -> pos
    orders: dict[str, Order] = field(default_factory=dict)  # order_id -> order
    trade_log: list[dict] = field(default_factory=list)
    _order_counter: int = 0

    @property
    def equity(self) -> float:
        pos_value = sum(p.size * p.avg_price for p in self.positions.values())
        return self.balance + pos_value

    @property
    def total_pnl(self) -> float:
        return self.equity - self.initial_balance


class PaperExecutor:
    """Simulates Polymarket trading for strategy development and testing."""

    def __init__(self, cfg: dict) -> None:
        pm_cfg = cfg.get("polymarket", {})
        initial = pm_cfg.get("paper_balance", 10000.0)
        self._account = PaperAccount(initial_balance=initial, balance=initial)
        self._fill_probability = pm_cfg.get("paper_fill_probability", 0.8)

    @property
    def account(self) -> PaperAccount:
        return self._account

    def place_order(
        self,
        token_id: str,
        side: Side,
        price: float,
        size: float,
        market: Optional[Market] = None,
    ) -> TradeResult:
        cost = price * size

        if side == Side.BUY and cost > self._account.balance:
            return TradeResult(success=False, error=f"Insufficient balance: need ${cost:.2f}, have ${self._account.balance:.2f}")

        # simulate fill
        self._account._order_counter += 1
        oid = f"paper-{self._account._order_counter}"

        import random
        if random.random() > self._fill_probability:
            log.info(f"Paper order {oid} NOT filled (simulated slippage)")
            return TradeResult(success=True, order_id=oid, filled_size=0)

        # fill immediately
        if side == Side.BUY:
            self._account.balance -= cost
            key = token_id
            if key in self._account.positions:
                pos = self._account.positions[key]
                new_size = pos.size + size
                new_avg = (pos.avg_price * pos.size + price * size) / new_size
                self._account.positions[key] = PaperPosition(
                    market_condition_id=pos.market_condition_id,
                    question=pos.question,
                    outcome=pos.outcome,
                    token_id=token_id,
                    size=new_size,
                    avg_price=new_avg,
                    realized_pnl=pos.realized_pnl,
                )
            else:
                question = market.question if market else ""
                cid = market.condition_id if market else ""
                self._account.positions[key] = PaperPosition(
                    market_condition_id=cid,
                    question=question,
                    outcome=Outcome.YES,
                    token_id=token_id,
                    size=size,
                    avg_price=price,
                )
        else:  # SELL
            key = token_id
            if key not in self._account.positions or self._account.positions[key].size < size:
                return TradeResult(success=False, error="Insufficient position to sell")
            pos = self._account.positions[key]
            pnl = (price - pos.avg_price) * size
            self._account.balance += price * size
            remaining = pos.size - size
            if remaining < 0.001:
                del self._account.positions[key]
            else:
                self._account.positions[key] = PaperPosition(
                    market_condition_id=pos.market_condition_id,
                    question=pos.question,
                    outcome=pos.outcome,
                    token_id=token_id,
                    size=remaining,
                    avg_price=pos.avg_price,
                    realized_pnl=pos.realized_pnl + pnl,
                )

        self._account.trade_log.append({
            "order_id": oid,
            "token_id": token_id,
            "side": side.value,
            "price": price,
            "size": size,
            "ts": time.time(),
        })

        log.info(f"Paper fill: {side.value} {size}@{price:.4f} token={token_id[:12]}... bal=${self._account.balance:.2f}")
        return TradeResult(success=True, order_id=oid, filled_size=size, avg_fill_price=price)

    def cancel_order(self, order_id: str) -> bool:
        if order_id in self._account.orders:
            del self._account.orders[order_id]
            return True
        return False

    def cancel_all(self) -> int:
        count = len(self._account.orders)
        self._account.orders.clear()
        return count

    def get_balance(self) -> float:
        return self._account.balance

    def add_balance(self, amount: float) -> None:
        self._account.balance += amount

    def get_equity(self) -> float:
        return self._account.equity

    def get_positions_summary(self) -> list[dict]:
        return [
            {
                "question": p.question[:60],
                "outcome": p.outcome.value,
                "size": round(p.size, 2),
                "avg_price": round(p.avg_price, 4),
                "cost_basis": round(p.size * p.avg_price, 2),
            }
            for p in self._account.positions.values()
        ]

    def summary(self) -> str:
        lines = [
            f"Paper Account: ${self._account.equity:.2f} equity",
            f"  Cash: ${self._account.balance:.2f}",
            f"  Positions: {len(self._account.positions)}",
            f"  PnL: ${self._account.total_pnl:+.2f}",
            f"  Trades: {len(self._account.trade_log)}",
        ]
        return "\n".join(lines)
