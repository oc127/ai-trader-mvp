from __future__ import annotations

import random
import uuid

from src.data.store import DataStore
from src.execution.engine import Executor
from src.hl_client.rest import HLRestClient
from src.hl_client.types import OrderResult, OrderStatus, Side
from src.logger import get_logger
from src.strategy.base import Signal
from src.strategy.market_maker import QuoteLevel

log = get_logger(__name__)

TAKER_FEE = 0.00035
MAKER_FEE = 0.0001

# Fill probability model for limit orders
AGGRESSIVE_FILL_PROB = 0.70  # at or better than BBO
PASSIVE_FILL_PROB = 0.20  # behind BBO


class LimitOrder:
    """Represents a resting limit order in the paper simulator."""

    def __init__(
        self,
        order_id: str,
        coin: str,
        side: str,
        price: float,
        size: float,
        strategy_tag: str = "funding_arb",
    ) -> None:
        self.order_id = order_id
        self.coin = coin
        self.side = side
        self.price = price
        self.size = size
        self.strategy_tag = strategy_tag


class PaperExecutor(Executor):
    def __init__(self, client: HLRestClient, store: DataStore, initial_balance: float = 10000.0) -> None:
        self._client = client
        self._store = store
        self._balance = initial_balance
        self._positions: dict[str, dict] = {}
        self._trade_log: list[dict] = []

        # MM limit order book
        self._limit_orders: list[LimitOrder] = []

    @property
    def balance(self) -> float:
        return self._balance

    @property
    def positions(self) -> dict[str, dict]:
        return self._positions.copy()

    def execute_signal(self, signal: Signal) -> list[OrderResult]:
        results = []
        mids = self._client.get_all_mids()

        for order_req in signal.orders:
            mid_price = mids.get(order_req.coin)
            if mid_price is None:
                log.warning("No price for paper trade", extra={"coin": order_req.coin})
                continue

            slippage = 0.001
            if order_req.side == Side.BUY:
                fill_price = mid_price * (1 + slippage)
            else:
                fill_price = mid_price * (1 - slippage)

            notional = order_req.size * fill_price
            fee = notional * TAKER_FEE

            if order_req.side == Side.BUY and order_req.is_spot:
                if notional + fee > self._balance:
                    log.warning(
                        "Insufficient paper balance",
                        extra={"need": notional, "have": self._balance},
                    )
                    continue
                self._balance -= notional + fee
            elif order_req.side == Side.BUY and not order_req.is_spot:
                margin_required = notional * 0.1 + fee
                if margin_required > self._balance:
                    log.warning(
                        "Insufficient margin for perp",
                        extra={"need": margin_required, "have": self._balance},
                    )
                    continue
                self._balance -= fee
            elif order_req.side == Side.SELL and not order_req.is_spot:
                margin_required = notional * 0.1 + fee
                if not order_req.reduce_only and margin_required > self._balance:
                    log.warning(
                        "Insufficient margin for perp",
                        extra={"need": margin_required, "have": self._balance},
                    )
                    continue
                self._balance -= fee
            elif order_req.side == Side.SELL and order_req.is_spot:
                spot_held = self._positions.get(order_req.coin, {}).get("spot", 0)
                if order_req.size > spot_held + 1e-8:
                    log.warning(
                        "Cannot sell spot not owned",
                        extra={"want": order_req.size, "have": spot_held},
                    )
                    continue
                self._balance += notional - fee

            self._update_paper_position(order_req.coin, order_req.side, order_req.size, fill_price, order_req.is_spot)

            result = OrderResult(
                order_id=f"paper-{uuid.uuid4().hex[:8]}",
                coin=order_req.coin,
                side=order_req.side,
                size=order_req.size,
                filled_size=order_req.size,
                price=fill_price,
                status=OrderStatus.FILLED,
                is_spot=order_req.is_spot,
            )
            results.append(result)

            self._store.save_trade(
                coin=result.coin,
                side=result.side.value,
                size=result.filled_size,
                price=result.price,
                order_id=result.order_id,
                is_spot=order_req.is_spot,
                fee=fee,
            )

            log.info(
                "Paper trade filled",
                extra={
                    "coin": result.coin,
                    "side": result.side.value,
                    "size": result.filled_size,
                    "price": fill_price,
                    "fee": fee,
                    "balance": self._balance,
                },
            )

        return results

    def flatten_all(self) -> list[OrderResult]:
        results = []
        mids = self._client.get_all_mids()

        for coin, pos in list(self._positions.items()):
            mid = mids.get(coin, 0)
            if pos.get("spot", 0) > 0:
                notional = pos["spot"] * mid
                self._balance += notional * (1 - TAKER_FEE)
            self._positions.pop(coin, None)
            log.info("Paper position flattened", extra={"coin": coin})

        # Cancel all limit orders
        self._limit_orders.clear()

        return results

    def apply_funding(self, coin: str, rate: float) -> float:
        pos = self._positions.get(coin, {})
        perp_size = pos.get("perp", 0)
        if perp_size == 0:
            return 0.0

        mids = self._client.get_all_mids()
        mid = mids.get(coin, 0)
        funding_payment = -perp_size * mid * rate
        self._balance += funding_payment

        log.debug("Paper funding applied", extra={"coin": coin, "rate": rate, "payment": funding_payment})
        return funding_payment

    # --- Market Making limit order support ---

    def place_limit_orders(self, orders: list[QuoteLevel], coin: str, strategy_tag: str = "market_maker") -> list[str]:
        """Place a batch of limit orders for market making.

        Args:
            orders: List of QuoteLevel objects with side, price, size, tier.
            coin: The coin these orders are for.
            strategy_tag: Tag to identify which strategy owns these orders.

        Returns:
            List of order IDs.
        """
        order_ids = []
        for quote in orders:
            order_id = f"paper-mm-{uuid.uuid4().hex[:8]}"
            limit_order = LimitOrder(
                order_id=order_id,
                coin=coin,
                side=quote.side,
                price=quote.price,
                size=quote.size,
                strategy_tag=strategy_tag,
            )
            self._limit_orders.append(limit_order)
            order_ids.append(order_id)

        log.info(
            "Paper MM limit orders placed",
            extra={"coin": coin, "count": len(orders), "tag": strategy_tag},
        )
        return order_ids

    def cancel_orders(self, coin: str, strategy_tag: str = "market_maker") -> int:
        """Cancel all resting limit orders for a coin and strategy.

        Args:
            coin: The coin to cancel orders for.
            strategy_tag: Only cancel orders with this tag.

        Returns:
            Number of orders cancelled.
        """
        before = len(self._limit_orders)
        self._limit_orders = [
            o for o in self._limit_orders if not (o.coin == coin and o.strategy_tag == strategy_tag)
        ]
        cancelled = before - len(self._limit_orders)
        if cancelled > 0:
            log.info(
                "Paper MM orders cancelled",
                extra={"coin": coin, "cancelled": cancelled, "tag": strategy_tag},
            )
        return cancelled

    def check_limit_fills(self, current_prices: dict[str, float]) -> list[dict]:
        """Check which limit orders would be filled given current prices.

        Fill logic:
        - Buy fills when mark <= order price
        - Sell fills when mark >= order price
        - Fill probability: 70% at BBO (aggressive), 20% behind BBO (passive)

        Args:
            current_prices: dict mapping coin -> current mid/mark price.

        Returns:
            List of fill dicts with coin, side, size, price, order_id, strategy_tag.
        """
        fills: list[dict] = []
        remaining: list[LimitOrder] = []

        for order in self._limit_orders:
            current_price = current_prices.get(order.coin)
            if current_price is None:
                remaining.append(order)
                continue

            would_fill = False
            if order.side == "buy" and current_price <= order.price:
                would_fill = True
            elif order.side == "sell" and current_price >= order.price:
                would_fill = True

            if would_fill:
                # Determine fill probability based on how aggressive the order is
                if order.side == "buy":
                    # Closer to current price = more aggressive
                    distance_pct = (order.price - current_price) / current_price if current_price > 0 else 0
                else:
                    distance_pct = (current_price - order.price) / current_price if current_price > 0 else 0

                # Use aggressive prob if price is very close (within 1 bps), passive otherwise
                if distance_pct < 0.0001:
                    fill_prob = AGGRESSIVE_FILL_PROB
                else:
                    fill_prob = PASSIVE_FILL_PROB

                if random.random() < fill_prob:
                    fee = order.size * order.price * MAKER_FEE
                    self._balance -= fee

                    # Update perp position for MM fills
                    side_enum = Side.BUY if order.side == "buy" else Side.SELL
                    self._update_paper_position(order.coin, side_enum, order.size, order.price, is_spot=False)

                    fill = {
                        "coin": order.coin,
                        "side": order.side,
                        "size": order.size,
                        "price": order.price,
                        "order_id": order.order_id,
                        "strategy_tag": order.strategy_tag,
                        "fee": fee,
                    }
                    fills.append(fill)

                    self._store.save_trade(
                        coin=order.coin,
                        side=order.side,
                        size=order.size,
                        price=order.price,
                        order_id=order.order_id,
                        is_spot=False,
                        fee=fee,
                    )

                    log.info(
                        "Paper MM limit order filled",
                        extra={
                            "coin": order.coin,
                            "side": order.side,
                            "size": order.size,
                            "price": order.price,
                            "fee": fee,
                            "tag": order.strategy_tag,
                        },
                    )
                else:
                    remaining.append(order)
            else:
                remaining.append(order)

        self._limit_orders = remaining
        return fills

    def get_limit_orders(self, coin: str | None = None, strategy_tag: str | None = None) -> list[LimitOrder]:
        """Get resting limit orders, optionally filtered by coin and/or strategy tag."""
        orders = self._limit_orders
        if coin is not None:
            orders = [o for o in orders if o.coin == coin]
        if strategy_tag is not None:
            orders = [o for o in orders if o.strategy_tag == strategy_tag]
        return list(orders)

    def _update_paper_position(self, coin: str, side: Side, size: float, price: float, is_spot: bool) -> None:
        if coin not in self._positions:
            self._positions[coin] = {"spot": 0.0, "perp": 0.0}

        key = "spot" if is_spot else "perp"
        if side == Side.BUY:
            self._positions[coin][key] += size
        else:
            self._positions[coin][key] -= size

    def get_equity(self) -> float:
        equity = self._balance
        mids = self._client.get_all_mids()
        for coin, pos in self._positions.items():
            mid = mids.get(coin, 0)
            equity += pos.get("spot", 0) * mid
            equity += pos.get("perp", 0) * mid
        return equity
