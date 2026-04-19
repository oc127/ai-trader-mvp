from __future__ import annotations

import uuid

from src.data.store import DataStore
from src.execution.engine import Executor
from src.hl_client.rest import HLRestClient
from src.hl_client.types import OrderResult, OrderStatus, Side
from src.logger import get_logger
from src.strategy.base import Signal

log = get_logger(__name__)

TAKER_FEE = 0.00035
MAKER_FEE = 0.0001


class PaperExecutor(Executor):
    def __init__(self, client: HLRestClient, store: DataStore, initial_balance: float = 10000.0) -> None:
        self._client = client
        self._store = store
        self._balance = initial_balance
        self._positions: dict[str, dict] = {}
        self._trade_log: list[dict] = []

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
