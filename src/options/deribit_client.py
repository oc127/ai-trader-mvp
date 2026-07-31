"""Deribit API v2 REST client for options trading.

Auth: client_credentials grant → Bearer token.
Endpoints:
  - Production: https://www.deribit.com/api/v2
  - Testnet:    https://test.deribit.com/api/v2
"""

from __future__ import annotations

import os
import time
from typing import Any, Optional

from src.logger import get_logger
from src.options.types import (
    Greeks,
    OptionDirection,
    OptionInstrument,
    OptionType,
    Position,
    TradeResult,
)

log = get_logger(__name__)

PROD_URL = "https://www.deribit.com/api/v2"
TEST_URL = "https://test.deribit.com/api/v2"


class DeribitClient:
    """REST client for Deribit API v2."""

    def __init__(self, cfg: dict) -> None:
        opts = cfg.get("options", {})
        self._testnet = opts.get("testnet", True)
        self._base = TEST_URL if self._testnet else PROD_URL

        self._client_id = opts.get("client_id") or os.getenv("DERIBIT_CLIENT_ID", "")
        self._client_secret = opts.get("client_secret") or os.getenv("DERIBIT_CLIENT_SECRET", "")

        self._access_token: str = ""
        self._refresh_token: str = ""
        self._token_expiry: float = 0.0

        self._rate_limit_delay = opts.get("rate_limit_delay", 0.2)
        self._last_request_ts = 0.0
        self._timeout = 15

        import requests as _req
        self._session = _req.Session()

    # ── Auth ──

    def authenticate(self) -> bool:
        if not self._client_id or not self._client_secret:
            log.error("Deribit credentials not set (DERIBIT_CLIENT_ID / DERIBIT_CLIENT_SECRET)")
            return False

        resp = self._public_request("public/auth", {
            "grant_type": "client_credentials",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        })
        if not resp:
            return False

        self._access_token = resp.get("access_token", "")
        self._refresh_token = resp.get("refresh_token", "")
        self._token_expiry = time.time() + resp.get("expires_in", 900) - 60
        log.info(f"Authenticated to Deribit ({'testnet' if self._testnet else 'PRODUCTION'})")
        return True

    def _ensure_auth(self) -> None:
        if time.time() >= self._token_expiry:
            if self._refresh_token:
                resp = self._public_request("public/auth", {
                    "grant_type": "refresh_token",
                    "refresh_token": self._refresh_token,
                })
                if resp:
                    self._access_token = resp.get("access_token", "")
                    self._refresh_token = resp.get("refresh_token", "")
                    self._token_expiry = time.time() + resp.get("expires_in", 900) - 60
                    return
            self.authenticate()

    # ── HTTP helpers ──

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < self._rate_limit_delay:
            time.sleep(self._rate_limit_delay - elapsed)
        self._last_request_ts = time.monotonic()

    def _public_request(self, method: str, params: dict | None = None) -> dict | None:
        self._throttle()
        try:
            resp = self._session.get(
                f"{self._base}/{method}",
                params=params or {},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            if "result" in data:
                return data["result"]
            if "error" in data:
                log.error(f"Deribit error: {data['error']}")
                return None
            return data
        except Exception:
            log.exception(f"Request failed: {method}")
            return None

    def _private_request(self, method: str, params: dict | None = None) -> dict | None:
        self._ensure_auth()
        self._throttle()
        try:
            headers = {"Authorization": f"Bearer {self._access_token}"}
            resp = self._session.get(
                f"{self._base}/{method}",
                params=params or {},
                headers=headers,
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            if "result" in data:
                return data["result"]
            if "error" in data:
                log.error(f"Deribit error: {data['error']}")
                return None
            return data
        except Exception:
            log.exception(f"Private request failed: {method}")
            return None

    # ── Market Data (public) ──

    def get_instruments(
        self, currency: str = "BTC", kind: str = "option", expired: bool = False,
    ) -> list[OptionInstrument]:
        resp = self._public_request("public/get_instruments", {
            "currency": currency,
            "kind": kind,
            "expired": str(expired).lower(),
        })
        if not resp:
            return []

        instruments: list[OptionInstrument] = []
        for item in resp:
            otype = OptionType.CALL if item.get("option_type") == "call" else OptionType.PUT
            instruments.append(OptionInstrument(
                instrument_name=item["instrument_name"],
                underlying=currency,
                option_type=otype,
                strike=float(item.get("strike", 0)),
                expiry_ts=int(item.get("expiration_timestamp", 0)) // 1000,
                settlement=item.get("settlement_period", ""),
                min_trade_amount=float(item.get("min_trade_amount", 0.1)),
                tick_size=float(item.get("tick_size", 0.0001)),
                contract_size=float(item.get("contract_size", 1)),
            ))
        return instruments

    def get_ticker(self, instrument_name: str) -> dict | None:
        return self._public_request("public/ticker", {"instrument_name": instrument_name})

    def get_order_book(self, instrument_name: str, depth: int = 5) -> dict | None:
        return self._public_request("public/get_order_book", {
            "instrument_name": instrument_name,
            "depth": depth,
        })

    def get_index_price(self, currency: str = "BTC") -> float:
        resp = self._public_request("public/get_index_price", {"index_name": f"{currency.lower()}_usd"})
        if resp:
            return float(resp.get("index_price", 0))
        return 0.0

    def enrich_instrument(self, inst: OptionInstrument) -> OptionInstrument:
        """Fill live market data into an instrument."""
        ticker = self.get_ticker(inst.instrument_name)
        if not ticker:
            return inst

        inst.bid = float(ticker.get("best_bid_price", 0) or 0)
        inst.ask = float(ticker.get("best_ask_price", 0) or 0)
        inst.mark_price = float(ticker.get("mark_price", 0) or 0)
        inst.iv = float(ticker.get("mark_iv", 0) or 0)
        inst.open_interest = float(ticker.get("open_interest", 0) or 0)
        inst.volume_24h = float(ticker.get("stats", {}).get("volume", 0) or 0)
        inst.underlying_price = float(ticker.get("underlying_price", 0) or 0)

        greeks = ticker.get("greeks", {})
        if greeks:
            inst.greeks = Greeks(
                delta=float(greeks.get("delta", 0) or 0),
                gamma=float(greeks.get("gamma", 0) or 0),
                theta=float(greeks.get("theta", 0) or 0),
                vega=float(greeks.get("vega", 0) or 0),
                rho=float(greeks.get("rho", 0) or 0),
            )
        return inst

    # ── Trading (private) ──

    def sell_option(
        self,
        instrument_name: str,
        amount: float,
        price: Optional[float] = None,
        post_only: bool = True,
    ) -> TradeResult:
        params: dict[str, Any] = {
            "instrument_name": instrument_name,
            "amount": amount,
            "type": "limit" if price is not None else "market",
        }
        if price is not None:
            params["price"] = price
        if post_only and price is not None:
            params["post_only"] = "true"

        resp = self._private_request("private/sell", params)
        if not resp:
            return TradeResult(success=False, error="API request failed")

        order = resp.get("order", {})
        trades = resp.get("trades", [])

        avg_price = 0.0
        filled = 0.0
        for t in trades:
            avg_price += float(t.get("price", 0)) * float(t.get("amount", 0))
            filled += float(t.get("amount", 0))
        if filled > 0:
            avg_price /= filled

        underlying_price = float(trades[0].get("underlying_price", 0)) if trades else 0
        premium = avg_price * underlying_price * filled if filled > 0 else 0

        return TradeResult(
            success=order.get("order_state") in ("filled", "open", "untriggered"),
            order_id=order.get("order_id", ""),
            instrument=instrument_name,
            direction="sell",
            price=avg_price,
            amount=filled,
            premium_usd=premium,
        )

    def buy_option(
        self,
        instrument_name: str,
        amount: float,
        price: Optional[float] = None,
    ) -> TradeResult:
        params: dict[str, Any] = {
            "instrument_name": instrument_name,
            "amount": amount,
            "type": "limit" if price is not None else "market",
        }
        if price is not None:
            params["price"] = price

        resp = self._private_request("private/buy", params)
        if not resp:
            return TradeResult(success=False, error="API request failed")

        order = resp.get("order", {})
        trades = resp.get("trades", [])

        avg_price = 0.0
        filled = 0.0
        for t in trades:
            avg_price += float(t.get("price", 0)) * float(t.get("amount", 0))
            filled += float(t.get("amount", 0))
        if filled > 0:
            avg_price /= filled

        return TradeResult(
            success=order.get("order_state") in ("filled", "open", "untriggered"),
            order_id=order.get("order_id", ""),
            instrument=instrument_name,
            direction="buy",
            price=avg_price,
            amount=filled,
        )

    # ── Position & Account ──

    def get_positions(self, currency: str = "BTC", kind: str = "option") -> list[Position]:
        resp = self._private_request("private/get_positions", {
            "currency": currency,
            "kind": kind,
        })
        if not resp:
            return []

        positions: list[Position] = []
        for p in resp:
            if float(p.get("size", 0)) == 0:
                continue
            size = float(p.get("size", 0))
            direction = OptionDirection.SELL if size < 0 else OptionDirection.BUY
            otype = OptionType.CALL if "C" in p.get("instrument_name", "").split("-")[-1] else OptionType.PUT

            positions.append(Position(
                instrument_name=p["instrument_name"],
                direction=direction,
                size=size,
                avg_price=float(p.get("average_price", 0) or 0),
                mark_price=float(p.get("mark_price", 0) or 0),
                unrealized_pnl=float(p.get("floating_profit_loss", 0) or 0),
                realized_pnl=float(p.get("realized_profit_loss", 0) or 0),
                delta=float(p.get("delta", 0) or 0),
                theta=float(p.get("theta", 0) or 0),
                underlying=currency,
                option_type=otype,
                strike=float(parts[2]) if len(parts := p.get("instrument_name", "").split("-")) > 2 else 0,
            ))
        return positions

    def get_account_summary(self, currency: str = "BTC") -> dict:
        resp = self._private_request("private/get_account_summary", {
            "currency": currency,
            "extended": "true",
        })
        return resp or {}

    def cancel_all_by_currency(self, currency: str = "BTC", kind: str = "option") -> bool:
        resp = self._private_request("private/cancel_all_by_currency", {
            "currency": currency,
            "kind": kind,
        })
        return resp is not None
