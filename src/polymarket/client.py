"""Polymarket CLOB V2 API client.

Uses py-clob-client-v2 (the V1 SDK is archived and non-functional).
Docs: https://docs.polymarket.com/
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Optional

from src.logger import get_logger
from src.polymarket.types import (
    Market,
    Order,
    OrderBook,
    OrderStatus,
    Outcome,
    Side,
    TradeResult,
)

log = get_logger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"


class PolymarketClient:
    """REST client for Polymarket CLOB V2 + Gamma APIs."""

    def __init__(self, cfg: dict) -> None:
        pm_cfg = cfg.get("polymarket", {})
        self._api_key = pm_cfg.get("api_key") or os.getenv("POLYMARKET_API_KEY", "")
        self._api_secret = pm_cfg.get("api_secret") or os.getenv("POLYMARKET_API_SECRET", "")
        self._api_passphrase = pm_cfg.get("api_passphrase") or os.getenv("POLYMARKET_API_PASSPHRASE", "")
        self._private_key = pm_cfg.get("private_key") or os.getenv("POLYMARKET_PRIVATE_KEY", "")
        self._funder = pm_cfg.get("funder_address") or os.getenv("POLYMARKET_FUNDER_ADDRESS", "")
        self._chain_id = pm_cfg.get("chain_id", 137)
        self._signature_type = pm_cfg.get("signature_type", 0)  # EOA default

        self._clob_client: Any = None
        self._rate_limit_delay = pm_cfg.get("rate_limit_delay", 0.2)
        self._last_request_ts = 0.0
        self._initial_balance = float(pm_cfg.get("initial_balance", 0))

        # heartbeat thread — required or all orders get cancelled
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._heartbeat_running = False

    @staticmethod
    def _make_api_creds(api_key: str, api_secret: str, api_passphrase: str):
        """Build an ApiCreds object the SDK expects (not a plain dict)."""
        try:
            from py_clob_client_v2.clob_types import ApiCreds
            return ApiCreds(api_key=api_key, api_secret=api_secret, api_passphrase=api_passphrase)
        except (ImportError, TypeError):
            from types import SimpleNamespace
            return SimpleNamespace(api_key=api_key, api_secret=api_secret, api_passphrase=api_passphrase)

    def _init_clob(self) -> Any:
        """Lazy-init the official CLOB V2 client."""
        if self._clob_client is not None:
            return self._clob_client

        try:
            from py_clob_client_v2 import ClobClient

            kwargs: dict[str, Any] = {
                "host": CLOB_API,
                "chain_id": self._chain_id,
                "key": self._private_key,
                "signature_type": self._signature_type,
            }
            if self._funder:
                kwargs["funder"] = self._funder

            self._clob_client = ClobClient(**kwargs)

            if self._api_key and self._api_secret and self._api_passphrase:
                self._clob_client.set_api_creds(
                    self._make_api_creds(self._api_key, self._api_secret, self._api_passphrase)
                )
            else:
                creds = self._clob_client.create_or_derive_api_key()
                if isinstance(creds, dict):
                    api_key = creds.get("apiKey", creds.get("api_key", ""))
                    api_secret = creds.get("secret", creds.get("api_secret", ""))
                    api_pass = creds.get("passphrase", creds.get("api_passphrase", ""))
                else:
                    api_key = getattr(creds, "api_key", getattr(creds, "apiKey", ""))
                    api_secret = getattr(creds, "api_secret", getattr(creds, "secret", ""))
                    api_pass = getattr(creds, "api_passphrase", getattr(creds, "passphrase", ""))
                self._clob_client.set_api_creds(
                    self._make_api_creds(api_key, api_secret, api_pass)
                )
                log.info(f"Derived API creds: key={api_key[:8]}...")

            log.info("CLOB V2 client initialized")
            return self._clob_client
        except ImportError:
            log.error("py-clob-client-v2 not installed: pip install py-clob-client-v2")
            raise
        except Exception:
            log.exception("Failed to init CLOB V2 client")
            raise

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < self._rate_limit_delay:
            time.sleep(self._rate_limit_delay - elapsed)
        self._last_request_ts = time.monotonic()

    # ── Heartbeat (critical: without this, all orders are auto-cancelled) ──

    def start_heartbeat(self) -> None:
        """Start background heartbeat thread (POST /v1/heartbeats every 10s)."""
        if self._heartbeat_running:
            return
        self._heartbeat_running = True
        self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()
        log.info("Heartbeat thread started")

    def stop_heartbeat(self) -> None:
        self._heartbeat_running = False

    def _heartbeat_loop(self) -> None:
        heartbeat_id = ""
        while self._heartbeat_running:
            try:
                client = self._init_clob()
                resp = client.post_heartbeat(heartbeat_id)
                if isinstance(resp, dict) and resp.get("heartbeat_id"):
                    heartbeat_id = resp["heartbeat_id"]
            except Exception:
                heartbeat_id = ""
                log.debug("Heartbeat failed (will retry)")
            time.sleep(10)

    # ── Market data (Gamma API — no auth needed) ──

    def get_markets(
        self,
        active: bool = True,
        limit: int = 100,
        min_volume: float = 0,
        min_liquidity: float = 0,
    ) -> list[Market]:
        """Fetch markets from Gamma API."""
        import requests

        self._throttle()
        params: dict[str, Any] = {"limit": limit, "active": active}
        resp = requests.get(f"{GAMMA_API}/markets", params=params, timeout=15)
        resp.raise_for_status()
        markets: list[Market] = []

        for m in resp.json():
            tokens_raw = m.get("clobTokenIds") or m.get("tokens", [])
            if isinstance(tokens_raw, str):
                import json as _json2
                try:
                    tokens_raw = _json2.loads(tokens_raw)
                except (ValueError, TypeError):
                    continue
            tokens = tokens_raw
            if not tokens or len(tokens) < 2:
                continue

            prices_raw = m.get("outcomePrices", "0.5,0.5")
            if isinstance(prices_raw, list):
                yes_price = float(prices_raw[0]) if prices_raw else 0.5
            elif isinstance(prices_raw, str):
                import json as _json
                try:
                    parsed = _json.loads(prices_raw)
                    yes_price = float(parsed[0]) if parsed else 0.5
                except (ValueError, TypeError):
                    parts = prices_raw.split(",")
                    yes_price = float(parts[0]) if parts else 0.5
            else:
                yes_price = 0.5

            no_price = 1.0 - yes_price
            vol = float(m.get("volume", 0) or 0)
            vol_24h = float(m.get("volume24hr", 0) or 0)
            liq = float(m.get("liquidity", 0) or 0)

            if vol < min_volume or liq < min_liquidity:
                continue

            yes_tid = tokens[0] if isinstance(tokens[0], str) else str(tokens[0])
            no_tid = tokens[1] if isinstance(tokens[1], str) else str(tokens[1])

            markets.append(Market(
                condition_id=str(m.get("conditionId", m.get("condition_id", ""))),
                question=str(m.get("question", "")),
                slug=str(m.get("slug", "")),
                yes_token_id=yes_tid,
                no_token_id=no_tid,
                yes_price=yes_price,
                no_price=no_price,
                volume=vol,
                volume_24h=vol_24h,
                liquidity=liq,
                end_date=m.get("endDate") or m.get("end_date_iso"),
                category=str(m.get("groupItemTitle", "") or m.get("category", "")),
                active=bool(m.get("active", True)),
            ))

        return markets

    def get_orderbook(self, token_id: str, market: Market) -> OrderBook:
        """Fetch order book from CLOB API."""
        import requests

        self._throttle()
        resp = requests.get(f"{CLOB_API}/book", params={"token_id": token_id}, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        bids = [(float(o["price"]), float(o["size"])) for o in data.get("bids", [])]
        asks = [(float(o["price"]), float(o["size"])) for o in data.get("asks", [])]

        bids.sort(key=lambda x: -x[0])
        asks.sort(key=lambda x: x[0])

        best_bid = bids[0][0] if bids else 0.0
        best_ask = asks[0][0] if asks else 1.0
        spread = best_ask - best_bid
        mid = (best_bid + best_ask) / 2.0

        return OrderBook(market=market, bids=bids, asks=asks, spread=spread, mid_price=mid)

    def get_tick_size(self, token_id: str) -> float:
        """Get tick size for a market (changes when price crosses 0.96 or 0.04)."""
        import requests

        self._throttle()
        resp = requests.get(f"{CLOB_API}/tick-size", params={"token_id": token_id}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return float(data.get("minimum_tick_size", 0.01))

    # ── Trading (requires auth + heartbeat) ──

    def place_order(
        self,
        token_id: str,
        side: Side,
        price: float,
        size: float,
    ) -> TradeResult:
        """Place a GTC limit order on the CLOB V2."""
        client = self._init_clob()
        self._throttle()

        try:
            from py_clob_client_v2 import OrderArgs, OrderType
            from py_clob_client_v2 import Side as ClobSide

            clob_side = ClobSide.BUY if side == Side.BUY else ClobSide.SELL
            resp = client.create_and_post_order(
                order_args=OrderArgs(
                    token_id=token_id,
                    price=price,
                    size=size,
                    side=clob_side,
                ),
                order_type=OrderType.GTC,
            )
            order_id = resp.get("orderID", resp.get("id", ""))
            log.info(f"Order placed: {side.value} {size}@{price} token={token_id[:12]}... id={order_id}")
            return TradeResult(success=True, order_id=str(order_id))
        except Exception as e:
            log.error(f"Order failed: {e}")
            return TradeResult(success=False, error=str(e))

    def place_market_order(
        self,
        token_id: str,
        side: Side,
        amount: float,
    ) -> TradeResult:
        """Place a FOK (fill-or-kill) market order."""
        client = self._init_clob()
        self._throttle()

        try:
            from py_clob_client_v2 import OrderArgs, OrderType
            from py_clob_client_v2 import Side as ClobSide

            clob_side = ClobSide.BUY if side == Side.BUY else ClobSide.SELL
            worst_price = 0.99 if side == Side.BUY else 0.01
            resp = client.create_and_post_order(
                order_args=OrderArgs(
                    token_id=token_id,
                    price=worst_price,
                    size=amount,
                    side=clob_side,
                ),
                order_type=OrderType.FOK,
            )
            order_id = resp.get("orderID", resp.get("id", ""))
            log.info(f"Market order: {side.value} ${amount} token={token_id[:12]}... id={order_id}")
            return TradeResult(success=True, order_id=str(order_id))
        except Exception as e:
            log.error(f"Market order failed: {e}")
            return TradeResult(success=False, error=str(e))

    def cancel_order(self, order_id: str) -> bool:
        client = self._init_clob()
        self._throttle()
        try:
            client.cancel(order_id)
            log.info(f"Cancelled order {order_id}")
            return True
        except Exception as e:
            log.error(f"Cancel failed for {order_id}: {e}")
            return False

    def cancel_all(self) -> int:
        client = self._init_clob()
        self._throttle()
        try:
            result = client.cancel_all()
            count = len(result) if isinstance(result, list) else 0
            log.info(f"Cancelled {count} orders")
            return count
        except Exception as e:
            log.error(f"Cancel all failed: {e}")
            return 0

    def get_open_orders(self) -> list[Order]:
        client = self._init_clob()
        self._throttle()
        try:
            raw = client.get_orders()
            orders: list[Order] = []
            for o in (raw or []):
                orders.append(Order(
                    order_id=str(o.get("id", "")),
                    market_condition_id=str(o.get("asset_id", "")),
                    token_id=str(o.get("token_id", o.get("asset_id", ""))),
                    side=Side.BUY if str(o.get("side", "")).upper() == "BUY" else Side.SELL,
                    price=float(o.get("price", 0)),
                    size=float(o.get("original_size", o.get("size", 0))),
                    outcome=Outcome.YES,
                    status=OrderStatus.LIVE,
                    filled_size=float(o.get("size_matched", 0)),
                ))
            return orders
        except Exception as e:
            log.error(f"Failed to get orders: {e}")
            return []

    def get_balance(self) -> float:
        """Get USDC balance on Polymarket (multi-method with fallback).

        The CLOB API returns balance for the signer address, but for proxy wallets
        the funds are in the funder address. We try multiple methods:
        1. SDK balance-allowance (works if API maps signer→proxy correctly)
        2. On-chain USDC query of the proxy wallet via Polygon RPC
        3. Config-specified initial balance as final fallback
        """
        # Method 1: SDK get_balance_allowance
        amount = self._balance_via_sdk()
        if amount > 0:
            return amount

        # Method 2: Direct on-chain USDC query on Polygon (proxy wallet)
        amount = self._balance_via_polygon_rpc()
        if amount > 0:
            return amount

        # Method 3: Config fallback (user-specified known balance)
        if self._initial_balance > 0:
            log.info(f"Using config initial_balance: ${self._initial_balance:.2f}")
            return self._initial_balance

        log.warning("All balance methods returned 0 — set polymarket.initial_balance in config")
        return 0.0

    def _balance_via_sdk(self) -> float:
        """Try getting balance through the SDK."""
        try:
            client = self._init_clob()
            self._throttle()
            from py_clob_client_v2.clob_types import AssetType, BalanceAllowanceParams

            params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            bal = client.get_balance_allowance(params)

            if isinstance(bal, dict):
                raw = float(bal.get("balance", 0) or 0)
                if raw > 0:
                    amount = raw / 1e6 if raw > 1_000_000 else raw
                    log.info(f"SDK balance: ${amount:.2f}")
                    return amount
        except Exception as e:
            log.debug(f"SDK balance failed: {e}")
        return 0.0

    def _balance_via_polygon_rpc(self) -> float:
        """Query USDC balance of proxy wallet directly on Polygon."""
        wallet = self._funder
        if not wallet:
            try:
                from eth_account import Account
                wallet = Account.from_key(self._private_key).address
            except Exception:
                return 0.0

        log.debug(f"Querying Polygon RPC for wallet {wallet[:12]}...")

        # USDC contracts on Polygon
        usdc_contracts = [
            "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",  # USDC.e (bridged, 6 dec)
            "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",  # native USDC (6 dec)
        ]
        rpcs = [
            "https://polygon-rpc.com",
            "https://rpc.ankr.com/polygon",
            "https://polygon.llamarpc.com",
            "https://1rpc.io/matic",
        ]

        total = 0.0
        for contract in usdc_contracts:
            bal = self._erc20_balance_of(wallet, contract, rpcs)
            if bal > 0:
                total += bal

        if total > 0:
            log.info(f"Polygon RPC balance: ${total:.2f} (wallet={wallet[:10]}...)")
        else:
            log.debug("Polygon RPC: all USDC balances are 0 (funds likely in exchange contract)")
        return total

    @staticmethod
    def _erc20_balance_of(wallet: str, contract: str, rpcs: list[str]) -> float:
        """Call balanceOf(address) on an ERC-20 contract via JSON-RPC."""
        import requests

        addr_padded = wallet.lower().replace("0x", "").zfill(64)
        data = f"0x70a08231000000000000000000000000{addr_padded}"
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_call",
            "params": [{"to": contract, "data": data}, "latest"],
        }

        for rpc in rpcs:
            try:
                resp = requests.post(rpc, json=payload, timeout=8)
                if resp.status_code != 200:
                    continue
                result = resp.json().get("result", "0x0")
                if result and result != "0x":
                    wei = int(result, 16)
                    return wei / 1e6
            except Exception:
                continue
        return 0.0
