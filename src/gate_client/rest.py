from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Any

import requests

from src.logger import get_logger

log = get_logger(__name__)

BASE_URL = "https://api.gateio.ws"


class GateClient:
    def __init__(self, api_key: str = "", api_secret: str = "") -> None:
        self._api_key = api_key or os.getenv("GATE_API_KEY", "")
        self._api_secret = api_secret or os.getenv("GATE_API_SECRET", "")
        self._session = requests.Session()
        self._session.headers.update({"Accept": "application/json"})
        self._contracts_cache: list[dict] | None = None

        if not self._api_key or not self._api_secret:
            log.warning("GATE_API_KEY or GATE_API_SECRET not set — read-only mode")
        else:
            log.info("Gate.io client initialized")

    def _sign(self, method: str, path: str, query_string: str, body: str) -> dict[str, str]:
        timestamp = str(int(time.time()))
        body_hash = hashlib.sha512(body.encode()).hexdigest()
        sign_string = f"{method}\n{path}\n{query_string}\n{body_hash}\n{timestamp}"
        signature = hmac.new(
            self._api_secret.encode(), sign_string.encode(), hashlib.sha512
        ).hexdigest()
        return {
            "KEY": self._api_key,
            "SIGN": signature,
            "Timestamp": timestamp,
        }

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        auth: bool = False,
    ) -> Any:
        url = BASE_URL + path
        query_string = "&".join(f"{k}={v}" for k, v in sorted(params.items())) if params else ""
        body_str = ""
        headers: dict[str, str] = {}

        if body is not None:
            import json
            body_str = json.dumps(body)
            headers["Content-Type"] = "application/json"

        if auth:
            if not self._api_key or not self._api_secret:
                raise RuntimeError("No API credentials configured — cannot make authenticated request")
            headers.update(self._sign(method, path, query_string, body_str))

        log.debug("Gate.io request", extra={"method": method, "path": path})

        try:
            resp = self._session.request(
                method=method,
                url=url,
                params=params,
                data=body_str if body_str else None,
                headers=headers,
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError as e:
            log.error(
                "Gate.io HTTP error",
                extra={
                    "status": e.response.status_code if e.response is not None else None,
                    "body": e.response.text if e.response is not None else "",
                    "path": path,
                },
            )
            raise
        except requests.exceptions.RequestException as e:
            log.error("Gate.io request failed", extra={"path": path, "error": str(e)})
            raise

    # -- Public ----------------------------------------------------------------

    def get_all_contracts(self) -> list[dict]:
        contracts = self._request("GET", "/api/v4/futures/usdt/contracts")
        self._contracts_cache = contracts
        return contracts

    def get_spot_tickers(self) -> dict[str, float]:
        raw = self._request("GET", "/api/v4/spot/tickers")
        result: dict[str, float] = {}
        for ticker in raw:
            pair = ticker.get("currency_pair", "")
            last = ticker.get("last")
            if pair and last:
                coin = pair.split("_")[0]
                result[coin] = float(last)
        return result

    def get_contract_info(self, contract: str) -> dict:
        if self._contracts_cache is None:
            self.get_all_contracts()
        assert self._contracts_cache is not None
        for c in self._contracts_cache:
            if c.get("name") == contract:
                return c
        raise ValueError(f"Contract {contract} not found")

    # -- Private Read ----------------------------------------------------------

    def get_spot_balances(self) -> dict[str, float]:
        raw = self._request("GET", "/api/v4/spot/accounts", auth=True)
        result: dict[str, float] = {}
        for account in raw:
            currency = account.get("currency", "")
            available = float(account.get("available", 0))
            if currency and available > 0:
                result[currency] = available
        return result

    def get_futures_account(self) -> dict:
        return self._request("GET", "/api/v4/futures/usdt/accounts", auth=True)

    def get_futures_positions(self) -> dict[str, dict]:
        raw = self._request("GET", "/api/v4/futures/usdt/positions", auth=True)
        result: dict[str, dict] = {}
        for pos in raw:
            contract = pos.get("contract", "")
            size = int(pos.get("size", 0))
            if contract and size != 0:
                result[contract] = {
                    "size": size,
                    "entry_price": float(pos.get("entry_price", 0)),
                    "unrealised_pnl": float(pos.get("unrealised_pnl", 0)),
                    "realised_pnl": float(pos.get("realised_pnl", 0)),
                    "leverage": int(pos.get("leverage", 0)),
                    "margin": float(pos.get("margin", 0)),
                    "liq_price": float(pos.get("liq_price", 0)),
                }
        return result

    # -- Private Trade ---------------------------------------------------------

    def spot_market_buy(self, pair: str, spend_usdt: float) -> dict:
        body = {
            "currency_pair": pair,
            "type": "market",
            "side": "buy",
            "amount": str(spend_usdt),
        }
        log.info("Spot market buy", extra={"pair": pair, "spend_usdt": spend_usdt})
        return self._request("POST", "/api/v4/spot/orders", body=body, auth=True)

    def spot_market_sell(self, pair: str, amount: float) -> dict:
        body = {
            "currency_pair": pair,
            "type": "market",
            "side": "sell",
            "amount": str(amount),
        }
        log.info("Spot market sell", extra={"pair": pair, "amount": amount})
        return self._request("POST", "/api/v4/spot/orders", body=body, auth=True)

    def futures_open_short(self, contract: str, size: int) -> dict:
        body = {
            "contract": contract,
            "size": -abs(size),
            "price": "0",
            "tif": "ioc",
        }
        log.info("Futures open short", extra={"contract": contract, "size": body["size"]})
        return self._request("POST", "/api/v4/futures/usdt/orders", body=body, auth=True)

    def futures_close_short(self, contract: str, size: int) -> dict:
        body = {
            "contract": contract,
            "size": abs(size),
            "price": "0",
            "tif": "ioc",
            "close": True,
        }
        log.info("Futures close short", extra={"contract": contract, "size": body["size"]})
        return self._request("POST", "/api/v4/futures/usdt/orders", body=body, auth=True)

    # -- Market Making -----------------------------------------------------------

    def get_order_book(self, pair: str, limit: int = 20) -> dict:
        return self._request(
            "GET", "/api/v4/spot/order_book",
            params={"currency_pair": pair, "limit": limit},
        )

    def spot_limit_buy(self, pair: str, price: float, amount: float) -> dict:
        body = {
            "currency_pair": pair,
            "type": "limit",
            "side": "buy",
            "price": str(price),
            "amount": str(amount),
        }
        return self._request("POST", "/api/v4/spot/orders", body=body, auth=True)

    def spot_limit_sell(self, pair: str, price: float, amount: float) -> dict:
        body = {
            "currency_pair": pair,
            "type": "limit",
            "side": "sell",
            "price": str(price),
            "amount": str(amount),
        }
        return self._request("POST", "/api/v4/spot/orders", body=body, auth=True)

    def cancel_order(self, pair: str, order_id: str) -> dict:
        return self._request(
            "DELETE", f"/api/v4/spot/orders/{order_id}",
            params={"currency_pair": pair}, auth=True,
        )

    def cancel_all_orders(self, pair: str) -> list:
        return self._request(
            "DELETE", "/api/v4/spot/orders",
            params={"currency_pair": pair}, auth=True,
        )

    def list_open_orders(self, pair: str) -> list:
        return self._request(
            "GET", "/api/v4/spot/orders",
            params={"currency_pair": pair, "status": "open"}, auth=True,
        )

    def get_spot_trades(self, pair: str, limit: int = 50) -> list:
        return self._request(
            "GET", "/api/v4/spot/trades",
            params={"currency_pair": pair, "limit": limit},
        )

    def get_my_trades(self, pair: str, limit: int = 50) -> list:
        return self._request(
            "GET", "/api/v4/spot/my_trades",
            params={"currency_pair": pair, "limit": limit}, auth=True,
        )
