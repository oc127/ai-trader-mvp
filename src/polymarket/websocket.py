"""Polymarket WebSocket client for real-time orderbook data.

Replaces REST polling with streaming updates from the CLOB WebSocket feed.
Designed for the high-frequency market maker — provides thread-safe access
to the latest orderbook snapshot per token.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from src.logger import get_logger

try:
    import websocket
    HAS_WEBSOCKET = True
except ImportError:
    websocket = None  # type: ignore[assignment]
    HAS_WEBSOCKET = False

log = get_logger(__name__)

WS_ENDPOINT = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

DEFAULT_RECONNECT_DELAY = 1.0
MAX_RECONNECT_DELAY = 30.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_PING_INTERVAL = 30
DEFAULT_PING_TIMEOUT = 10


class PolymarketWebSocket:
    """Real-time orderbook feed from Polymarket CLOB WebSocket.

    Thread-safe. Maintains a background receiver thread and stores the latest
    orderbook snapshot per token_id in an internal dict guarded by a lock.
    Reconnects automatically with exponential backoff on disconnect.
    """

    def __init__(self, cfg: dict) -> None:
        if not HAS_WEBSOCKET:
            log.warning(
                "websocket-client not installed — WebSocket feed unavailable. "
                "Install with: pip install websocket-client"
            )

        pm_cfg = cfg.get("polymarket", {})
        ws_cfg = pm_cfg.get("websocket", {})

        self._endpoint: str = ws_cfg.get("endpoint", WS_ENDPOINT)
        self._max_retries: int = ws_cfg.get("max_retries", DEFAULT_MAX_RETRIES)
        self._reconnect_delay: float = ws_cfg.get("reconnect_delay", DEFAULT_RECONNECT_DELAY)
        self._ping_interval: int = ws_cfg.get("ping_interval", DEFAULT_PING_INTERVAL)
        self._ping_timeout: int = ws_cfg.get("ping_timeout", DEFAULT_PING_TIMEOUT)
        self._default_spread: float = ws_cfg.get("default_spread", 0.10)

        self._ws: Optional[websocket.WebSocketApp] = None  # type: ignore[union-attr]
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._books: dict[str, dict] = {}
        self._subscribed: set[str] = set()
        self._connected = threading.Event()
        self._running = False
        self._retry_count = 0

    def connect(self) -> None:
        if not HAS_WEBSOCKET:
            log.error("Cannot connect: websocket-client not installed")
            return

        if self._running:
            log.debug("WebSocket already running")
            return

        self._running = True
        self._retry_count = 0
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        log.info("WebSocket receiver thread started")

    def _run_loop(self) -> None:
        while self._running:
            try:
                self._connect_once()
            except Exception:
                log.exception("WebSocket run loop error")

            if not self._running:
                break

            self._connected.clear()
            self._retry_count += 1

            if self._retry_count > self._max_retries:
                log.error(
                    f"WebSocket exceeded max retries ({self._max_retries}), stopping"
                )
                self._running = False
                break

            delay = min(
                self._reconnect_delay * (2 ** (self._retry_count - 1)),
                MAX_RECONNECT_DELAY,
            )
            log.warning(
                f"WebSocket reconnecting in {delay:.1f}s "
                f"(attempt {self._retry_count}/{self._max_retries})"
            )
            time.sleep(delay)

    def _connect_once(self) -> None:
        self._ws = websocket.WebSocketApp(
            self._endpoint,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        self._ws.run_forever(
            ping_interval=self._ping_interval,
            ping_timeout=self._ping_timeout,
        )

    def _on_open(self, ws: websocket.WebSocketApp) -> None:
        log.info(f"WebSocket connected to {self._endpoint}")
        self._retry_count = 0
        self._connected.set()

        with self._lock:
            token_ids = list(self._subscribed)

        if token_ids:
            self._send_subscribe(token_ids)

    def _on_message(self, ws: websocket.WebSocketApp, message: str) -> None:
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            log.debug(f"Non-JSON WebSocket message: {message[:100]}")
            return

        self._process_message(data)

    def _on_error(self, ws: websocket.WebSocketApp, error: Exception) -> None:
        log.error(f"WebSocket error: {error}")

    def _on_close(self, ws: websocket.WebSocketApp, close_status_code: int, close_msg: str) -> None:
        log.warning(f"WebSocket closed: status={close_status_code} msg={close_msg}")
        self._connected.clear()

    def _process_message(self, data: dict) -> None:
        msg_type = data.get("type", data.get("event_type", ""))

        if msg_type in ("book", "market"):
            self._handle_book_snapshot(data)
        elif msg_type == "price_change":
            self._handle_price_change(data)
        elif msg_type in ("error", "err"):
            log.error(f"WebSocket server error: {data}")
        elif msg_type in ("subscribed", "subscription"):
            log.debug(f"Subscription confirmed: {data}")
        else:
            self._handle_generic_update(data)

    def _handle_book_snapshot(self, data: dict) -> None:
        asset_id = data.get("asset_id", data.get("market", ""))
        if not asset_id:
            return

        bids_raw = data.get("bids", [])
        asks_raw = data.get("asks", [])

        bids = self._parse_levels(bids_raw, reverse=True)
        asks = self._parse_levels(asks_raw, reverse=False)

        best_bid = bids[0][0] if bids else 0.0
        best_ask = asks[0][0] if asks else 1.0
        spread = best_ask - best_bid
        mid_price = (best_bid + best_ask) / 2.0

        book = {
            "bids": bids,
            "asks": asks,
            "spread": round(spread, 6),
            "mid_price": round(mid_price, 6),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        with self._lock:
            self._books[asset_id] = book

    def _handle_price_change(self, data: dict) -> None:
        asset_id = data.get("asset_id", "")
        if not asset_id:
            return

        with self._lock:
            existing = self._books.get(asset_id)

        if not existing:
            return

        changes = data.get("changes", [])

        if changes:
            bids = list(existing["bids"])
            asks = list(existing["asks"])

            for change in changes:
                side = change.get("side", "")
                c_price = float(change.get("price", 0))
                c_size = float(change.get("size", 0))

                if side.upper() == "BUY":
                    bids = self._apply_delta(bids, c_price, c_size, reverse=True)
                elif side.upper() == "SELL":
                    asks = self._apply_delta(asks, c_price, c_size, reverse=False)

            best_bid = bids[0][0] if bids else 0.0
            best_ask = asks[0][0] if asks else 1.0
            spread = best_ask - best_bid
            mid_price = (best_bid + best_ask) / 2.0

            book = {
                "bids": bids,
                "asks": asks,
                "spread": round(spread, 6),
                "mid_price": round(mid_price, 6),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            with self._lock:
                self._books[asset_id] = book

    def _handle_generic_update(self, data: dict) -> None:
        asset_id = data.get("asset_id", data.get("market", ""))
        if not asset_id:
            return

        if "bids" in data or "asks" in data:
            self._handle_book_snapshot(data)

    @staticmethod
    def _parse_levels(
        raw: list, reverse: bool = False
    ) -> list[tuple[float, float]]:
        levels: list[tuple[float, float]] = []
        for entry in raw:
            if isinstance(entry, dict):
                price = float(entry.get("price", 0))
                size = float(entry.get("size", entry.get("quantity", 0)))
            elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
                price = float(entry[0])
                size = float(entry[1])
            else:
                continue
            if size > 0:
                levels.append((price, size))

        levels.sort(key=lambda x: x[0], reverse=reverse)
        return levels

    @staticmethod
    def _apply_delta(
        levels: list[tuple[float, float]],
        price: float,
        size: float,
        reverse: bool = False,
    ) -> list[tuple[float, float]]:
        updated = [(p, s) for p, s in levels if abs(p - price) > 1e-9]
        if size > 0:
            updated.append((price, size))
        updated.sort(key=lambda x: x[0], reverse=reverse)
        return updated

    def _send_subscribe(self, token_ids: list[str]) -> None:
        if not self._ws or not self._connected.is_set():
            return

        msg = json.dumps({"type": "market", "assets_ids": token_ids})
        try:
            self._ws.send(msg)
            log.info(f"Subscribed to {len(token_ids)} token(s)")
        except Exception:
            log.exception("Failed to send subscribe message")

    def subscribe(self, token_ids: list[str]) -> None:
        with self._lock:
            self._subscribed.update(token_ids)

        if self._connected.is_set():
            self._send_subscribe(token_ids)
        else:
            log.debug(
                f"Queued {len(token_ids)} subscription(s) — will subscribe on connect"
            )

    def unsubscribe(self, token_ids: list[str]) -> None:
        with self._lock:
            self._subscribed -= set(token_ids)
            for tid in token_ids:
                self._books.pop(tid, None)

        if self._ws and self._connected.is_set():
            msg = json.dumps({"type": "unsubscribe", "assets_ids": token_ids})
            try:
                self._ws.send(msg)
                log.info(f"Unsubscribed from {len(token_ids)} token(s)")
            except Exception:
                log.exception("Failed to send unsubscribe message")

    def get_book(self, token_id: str) -> Optional[dict]:
        with self._lock:
            book = self._books.get(token_id)
        if book is None:
            return None
        return dict(book)

    def get_spread(self, token_id: str) -> float:
        book = self.get_book(token_id)
        if book is None:
            return self._default_spread
        spread = book.get("spread", self._default_spread)
        if spread <= 0:
            return self._default_spread
        return spread

    def close(self) -> None:
        self._running = False
        self._connected.clear()

        if self._ws:
            try:
                self._ws.close()
            except Exception:
                log.debug("Error closing WebSocket (ignored)")
            self._ws = None

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
            self._thread = None

        with self._lock:
            self._books.clear()
            self._subscribed.clear()

        log.info("WebSocket client closed")

    @property
    def is_connected(self) -> bool:
        return self._connected.is_set()

    @property
    def subscribed_tokens(self) -> set[str]:
        with self._lock:
            return set(self._subscribed)

    @property
    def available_books(self) -> list[str]:
        with self._lock:
            return list(self._books.keys())
