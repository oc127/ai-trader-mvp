from __future__ import annotations

import time

from src.data.store import DataStore
from src.execution.engine import Executor
from src.hl_client.rest import HLRestClient
from src.hl_client.types import OrderResult, OrderStatus
from src.logger import get_logger
from src.strategy.base import Signal

log = get_logger(__name__)


class LiveExecutor(Executor):
    def __init__(self, client: HLRestClient, store: DataStore) -> None:
        self._client = client
        self._store = store

    def execute_signal(self, signal: Signal) -> list[OrderResult]:
        results = []
        for order_req in signal.orders:
            try:
                result = self._client.place_order(order_req)
                results.append(result)

                self._store.save_trade(
                    coin=result.coin,
                    side=result.side.value,
                    size=result.filled_size,
                    price=result.price,
                    order_id=result.order_id,
                    is_spot=order_req.is_spot,
                )
                self._store.save_event(
                    "order_placed",
                    {
                        "coin": result.coin,
                        "side": result.side.value,
                        "size": result.size,
                        "filled": result.filled_size,
                        "price": result.price,
                        "status": result.status.value,
                        "signal_action": signal.action,
                        "signal_reason": signal.reason,
                    },
                )

                log.info(
                    "Order executed",
                    extra={
                        "coin": result.coin,
                        "side": result.side.value,
                        "size": result.filled_size,
                        "price": result.price,
                        "status": result.status.value,
                    },
                )

                if result.status == OrderStatus.REJECTED:
                    log.error("Order rejected", extra={"coin": result.coin, "order_id": result.order_id})
                    break

                time.sleep(0.1)

            except Exception:
                log.exception("Order execution failed", extra={"coin": order_req.coin})
                break

        return results

    def flatten_all(self) -> list[OrderResult]:
        results = []
        account = self._client.get_account_state()
        for pos in account.positions:
            if abs(pos.size) < 1e-8:
                continue
            try:
                self._client.close_position(pos.coin)
                log.info("Position flattened", extra={"coin": pos.coin, "size": pos.size})
                self._store.save_event("kill_switch", {"coin": pos.coin, "size": pos.size})
            except Exception:
                log.exception("Failed to flatten position", extra={"coin": pos.coin})

        open_orders = self._client.get_open_orders()
        for order in open_orders:
            try:
                self._client.cancel_order(order["coin"], order["oid"])
            except Exception:
                log.exception("Failed to cancel order", extra={"oid": order.get("oid")})

        return results
