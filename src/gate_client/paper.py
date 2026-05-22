"""Paper trading wrapper — intercepts all write calls, forwards reads to the real client."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.logger import get_logger

if TYPE_CHECKING:
    from src.gate_client.rest import GateClient

log = get_logger(__name__)

TAG = "[PAPER] "


class PaperGateClient:
    def __init__(self, real_client: GateClient) -> None:
        self._client = real_client
        self._next_id = 1000
        self._open_orders: dict[str, dict] = {}

    # -- Read-through (safe, no money at risk) --------------------------------

    def get_all_contracts(self) -> list[dict]:
        return self._client.get_all_contracts()

    def get_contract_info(self, contract: str) -> dict:
        return self._client.get_contract_info(contract)

    def get_spot_tickers(self) -> dict[str, float]:
        return self._client.get_spot_tickers()

    def get_spot_balances(self) -> dict[str, float]:
        return self._client.get_spot_balances()

    def get_futures_account(self) -> dict:
        return self._client.get_futures_account()

    def get_futures_positions(self) -> dict[str, dict]:
        return self._client.get_futures_positions()

    def get_order_book(self, pair: str, limit: int = 20) -> dict:
        return self._client.get_order_book(pair, limit)

    def get_spot_trades(self, pair: str, limit: int = 50) -> list:
        return self._client.get_spot_trades(pair, limit)

    def get_my_trades(self, pair: str, limit: int = 50) -> list:
        return []

    def list_open_orders(self, pair: str) -> list:
        return [v for v in self._open_orders.values() if v.get("pair") == pair]

    # -- Spot writes (intercepted) --------------------------------------------

    def spot_market_buy(self, pair: str, spend_usdt: float) -> dict:
        oid = self._next_oid()
        log.info("%sSpot market BUY %s $%.2f", TAG, pair, spend_usdt)
        return {"id": oid}

    def spot_market_sell(self, pair: str, amount: float) -> dict:
        oid = self._next_oid()
        log.info("%sSpot market SELL %s %.6f", TAG, pair, amount)
        return {"id": oid}

    def spot_limit_buy(self, pair: str, price: float, amount: float) -> dict:
        oid = self._next_oid()
        self._open_orders[oid] = {"id": oid, "side": "buy", "pair": pair, "price": price, "amount": amount}
        log.info("%sLimit BUY %s %.4f @ %.6f", TAG, pair, amount, price)
        return {"id": oid}

    def spot_limit_sell(self, pair: str, price: float, amount: float) -> dict:
        oid = self._next_oid()
        self._open_orders[oid] = {"id": oid, "side": "sell", "pair": pair, "price": price, "amount": amount}
        log.info("%sLimit SELL %s %.4f @ %.6f", TAG, pair, amount, price)
        return {"id": oid}

    def cancel_order(self, pair: str, order_id: str) -> dict:
        self._open_orders.pop(order_id, None)
        return {}

    def cancel_all_orders(self, pair: str) -> list:
        to_remove = [k for k, v in self._open_orders.items() if v.get("pair") == pair]
        for k in to_remove:
            del self._open_orders[k]
        log.info("%sCancelled %d orders for %s", TAG, len(to_remove), pair)
        return []

    # -- Futures writes (intercepted) -----------------------------------------

    def futures_open_short(self, contract: str, size: int) -> dict:
        oid = self._next_oid()
        log.info("%sFutures OPEN SHORT %s size=%d", TAG, contract, size)
        return {"id": oid}

    def futures_close_short(self, contract: str, size: int) -> dict:
        oid = self._next_oid()
        log.info("%sFutures CLOSE SHORT %s size=%d", TAG, contract, size)
        return {"id": oid}

    def futures_open_long(self, contract: str, size: int) -> dict:
        oid = self._next_oid()
        log.info("%sFutures OPEN LONG %s size=%d", TAG, contract, size)
        return {"id": oid}

    def futures_close_long(self, contract: str, size: int) -> dict:
        oid = self._next_oid()
        log.info("%sFutures CLOSE LONG %s size=%d", TAG, contract, size)
        return {"id": oid}

    def futures_set_leverage(self, contract: str, leverage: int) -> dict:
        log.info("%sSet leverage %s x%d", TAG, contract, leverage)
        return {}

    def futures_get_contract(self, contract: str) -> dict:
        return self._client.futures_get_contract(contract)

    # -- Helpers --------------------------------------------------------------

    def _next_oid(self) -> str:
        oid = str(self._next_id)
        self._next_id += 1
        return oid
