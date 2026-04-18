from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info

from src.hl_client.types import (
    AccountState,
    FundingRate,
    OrderRequest,
    OrderResult,
    OrderStatus,
    OrderType,
    Position,
    Side,
    SpotBalance,
)
from src.logger import get_logger

log = get_logger(__name__)

MAINNET_URL = "https://api.hyperliquid.xyz"
TESTNET_URL = "https://api.hyperliquid-testnet.xyz"


class HLRestClient:
    def __init__(self, cfg: dict) -> None:
        use_testnet = cfg.get("exchange", {}).get("use_testnet", True)
        self._base_url = TESTNET_URL if use_testnet else MAINNET_URL

        private_key = os.getenv("HL_PRIVATE_KEY", "")
        self._address = os.getenv("HL_WALLET_ADDRESS", "")

        self._info = Info(base_url=self._base_url, skip_ws=True)

        self._exchange: Exchange | None = None
        if private_key:
            wallet = Account.from_key(private_key)
            self._exchange = Exchange(wallet=wallet, base_url=self._base_url)
            if not self._address:
                self._address = wallet.address
            log.info(
                "HL client initialized", extra={"testnet": use_testnet, "address": self._address}
            )
        else:
            log.warning("No HL_PRIVATE_KEY — read-only mode")

    @property
    def address(self) -> str:
        return self._address

    def get_all_mids(self) -> dict[str, float]:
        raw = self._info.all_mids()
        return {k: float(v) for k, v in raw.items()}

    def get_funding_rates(
        self, coin: str, start_time: int, end_time: int | None = None
    ) -> list[FundingRate]:
        raw = self._info.funding_history(coin, start_time, end_time)
        rates = []
        for entry in raw:
            rates.append(
                FundingRate(
                    coin=entry["coin"],
                    rate=float(entry["fundingRate"]),
                    premium=float(entry.get("premium", 0)),
                    timestamp=datetime.fromtimestamp(entry["time"] / 1000, tz=timezone.utc),
                )
            )
        return rates

    def get_meta(self) -> dict[str, Any]:
        return self._info.meta()

    def get_spot_meta(self) -> dict[str, Any]:
        return self._info.spot_meta()

    def get_l2_snapshot(self, coin: str) -> dict[str, Any]:
        return self._info.l2_snapshot(coin)

    def get_account_state(self) -> AccountState:
        raw = self._info.user_state(self._address)
        positions = []
        for p in raw.get("assetPositions", []):
            pos = p["position"]
            positions.append(
                Position(
                    coin=pos["coin"],
                    size=float(pos["szi"]),
                    entry_price=float(pos["entryPx"]),
                    mark_price=float(pos.get("markPx", pos["entryPx"])),
                    unrealized_pnl=float(pos["unrealizedPnl"]),
                    margin_used=float(pos.get("marginUsed", 0)),
                    leverage=float(pos.get("leverage", {}).get("value", 1)),
                )
            )

        margin = raw.get("marginSummary", raw.get("crossMarginSummary", {}))
        return AccountState(
            equity=float(margin.get("accountValue", 0)),
            available_balance=float(raw.get("withdrawable", 0)),
            margin_used=float(margin.get("totalMarginUsed", 0)),
            positions=positions,
        )

    def get_spot_balances(self) -> list[SpotBalance]:
        raw = self._info.spot_user_state(self._address)
        balances = []
        for b in raw.get("balances", []):
            balances.append(
                SpotBalance(
                    coin=b["coin"],
                    total=float(b["total"]),
                    available=float(b.get("hold", b["total"])),
                )
            )
        return balances

    def place_order(self, req: OrderRequest) -> OrderResult:
        if not self._exchange:
            raise RuntimeError("No private key configured — cannot trade")

        coin = req.coin
        is_buy = req.side == Side.BUY

        if req.order_type == OrderType.MARKET:
            result = self._exchange.market_open(
                name=coin,
                is_buy=is_buy,
                sz=req.size,
                slippage=0.05,
            )
        else:
            result = self._exchange.order(
                name=coin,
                is_buy=is_buy,
                sz=req.size,
                limit_px=req.price or 0,
                order_type={"limit": {"tif": "Gtc"}},
                reduce_only=req.reduce_only,
            )

        return self._parse_order_result(result, req)

    def cancel_order(self, coin: str, order_id: int) -> Any:
        if not self._exchange:
            raise RuntimeError("No private key configured — cannot trade")
        return self._exchange.cancel(coin, order_id)

    def close_position(self, coin: str, size: float | None = None) -> Any:
        if not self._exchange:
            raise RuntimeError("No private key configured — cannot trade")
        return self._exchange.market_close(coin, sz=size, slippage=0.05)

    def get_open_orders(self) -> list[dict[str, Any]]:
        return self._info.open_orders(self._address)

    def update_leverage(self, coin: str, leverage: int, is_cross: bool = True) -> Any:
        if not self._exchange:
            raise RuntimeError("No private key configured — cannot trade")
        return self._exchange.update_leverage(leverage, coin, is_cross=is_cross)

    def _parse_order_result(self, result: Any, req: OrderRequest) -> OrderResult:
        status = OrderStatus.REJECTED
        order_id = ""
        filled_size = 0.0
        price = req.price or 0.0

        if isinstance(result, dict):
            resp_status = result.get("status", "")
            if resp_status == "ok":
                data = result.get("response", {}).get("data", {})
                if isinstance(data, dict):
                    statuses = data.get("statuses", [{}])
                    if statuses:
                        s = statuses[0]
                        if "resting" in s:
                            status = OrderStatus.OPEN
                            order_id = str(s["resting"]["oid"])
                        elif "filled" in s:
                            status = OrderStatus.FILLED
                            order_id = str(s["filled"]["oid"])
                            filled_size = float(s["filled"].get("totalSz", req.size))
                            price = float(s["filled"].get("avgPx", price))

        return OrderResult(
            order_id=order_id,
            coin=req.coin,
            side=req.side,
            size=req.size,
            filled_size=filled_size,
            price=price,
            status=status,
            is_spot=req.is_spot,
        )
