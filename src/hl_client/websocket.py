from __future__ import annotations

import os
import threading
from typing import Any, Callable

from hyperliquid.info import Info

from src.logger import get_logger

log = get_logger(__name__)

MAINNET_URL = "https://api.hyperliquid.xyz"
TESTNET_URL = "https://api.hyperliquid-testnet.xyz"


class HLWebSocketClient:
    def __init__(self, cfg: dict) -> None:
        use_testnet = cfg.get("exchange", {}).get("use_testnet", True)
        base_url = TESTNET_URL if use_testnet else MAINNET_URL
        self._address = os.getenv("HL_WALLET_ADDRESS", "")

        self._info = Info(base_url=base_url, skip_ws=False)
        self._subscriptions: dict[int, dict] = {}
        self._lock = threading.Lock()

        log.info("WS client initialized", extra={"testnet": use_testnet})

    def subscribe_all_mids(self, callback: Callable[[dict[str, float]], None]) -> int:
        def _handler(msg: Any) -> None:
            if isinstance(msg, dict) and "data" in msg:
                mids = msg["data"].get("mids", {})
                callback({k: float(v) for k, v in mids.items()})

        sub_id = self._info.subscribe({"type": "allMids"}, _handler)
        with self._lock:
            self._subscriptions[sub_id] = {"type": "allMids"}
        return sub_id

    def subscribe_l2_book(self, coin: str, callback: Callable[[dict], None]) -> int:
        def _handler(msg: Any) -> None:
            if isinstance(msg, dict) and "data" in msg:
                callback(msg["data"])

        sub_id = self._info.subscribe({"type": "l2Book", "coin": coin}, _handler)
        with self._lock:
            self._subscriptions[sub_id] = {"type": "l2Book", "coin": coin}
        return sub_id

    def subscribe_trades(self, coin: str, callback: Callable[[list[dict]], None]) -> int:
        def _handler(msg: Any) -> None:
            if isinstance(msg, dict) and "data" in msg:
                callback(msg["data"])

        sub_id = self._info.subscribe({"type": "trades", "coin": coin}, _handler)
        with self._lock:
            self._subscriptions[sub_id] = {"type": "trades", "coin": coin}
        return sub_id

    def subscribe_user_events(self, callback: Callable[[dict], None]) -> int:
        if not self._address:
            raise RuntimeError("No wallet address for user event subscription")

        def _handler(msg: Any) -> None:
            if isinstance(msg, dict):
                callback(msg)

        sub_id = self._info.subscribe({"type": "userEvents", "user": self._address}, _handler)
        with self._lock:
            self._subscriptions[sub_id] = {"type": "userEvents", "user": self._address}
        return sub_id

    def subscribe_user_fills(self, callback: Callable[[dict], None]) -> int:
        if not self._address:
            raise RuntimeError("No wallet address for user fills subscription")

        def _handler(msg: Any) -> None:
            if isinstance(msg, dict):
                callback(msg)

        sub_id = self._info.subscribe({"type": "userFills", "user": self._address}, _handler)
        with self._lock:
            self._subscriptions[sub_id] = {"type": "userFills", "user": self._address}
        return sub_id

    def subscribe_user_fundings(self, callback: Callable[[dict], None]) -> int:
        if not self._address:
            raise RuntimeError("No wallet address for funding subscription")

        def _handler(msg: Any) -> None:
            if isinstance(msg, dict):
                callback(msg)

        sub_id = self._info.subscribe({"type": "userFundings", "user": self._address}, _handler)
        with self._lock:
            self._subscriptions[sub_id] = {"type": "userFundings", "user": self._address}
        return sub_id

    def subscribe_asset_ctx(self, coin: str, callback: Callable[[dict], None]) -> int:
        def _handler(msg: Any) -> None:
            if isinstance(msg, dict):
                callback(msg)

        sub_id = self._info.subscribe({"type": "activeAssetCtx", "coin": coin}, _handler)
        with self._lock:
            self._subscriptions[sub_id] = {"type": "activeAssetCtx", "coin": coin}
        return sub_id

    def unsubscribe(self, sub_id: int) -> bool:
        with self._lock:
            sub = self._subscriptions.pop(sub_id, None)
        if sub is None:
            return False
        return self._info.unsubscribe(sub, sub_id)

    def unsubscribe_all(self) -> None:
        with self._lock:
            subs = list(self._subscriptions.items())
            self._subscriptions.clear()
        for sub_id, sub in subs:
            try:
                self._info.unsubscribe(sub, sub_id)
            except Exception:
                pass
