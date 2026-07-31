"""Trading tools that the AI agent can call."""

from __future__ import annotations

import json
from typing import Any

from src.hl_client.rest import HLRestClient
from src.hl_client.types import OrderRequest, OrderType, Side
from src.logger import get_logger

log = get_logger(__name__)

TOOL_DEFINITIONS = [
    {
        "name": "get_account",
        "description": "获取 Hyperliquid 账户信息：余额、持仓、盈亏。",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_price",
        "description": "获取一个或多个币的当前价格。",
        "input_schema": {
            "type": "object",
            "properties": {
                "coins": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "币种列表，如 ['BTC', 'ETH', 'PURR']。留空则返回所有币价。",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_orderbook",
        "description": "获取某个币的订单簿（买卖盘深度）。",
        "input_schema": {
            "type": "object",
            "properties": {
                "coin": {"type": "string", "description": "币种，如 'BTC'"},
            },
            "required": ["coin"],
        },
    },
    {
        "name": "scan_pairs",
        "description": "扫描 Hyperliquid 所有交易对，找出价差大、适合做市的币种。",
        "input_schema": {
            "type": "object",
            "properties": {
                "top_n": {
                    "type": "integer",
                    "description": "返回前 N 个最佳做市候选币（默认 15）",
                },
            },
            "required": [],
        },
    },
    {
        "name": "place_order",
        "description": "下一个限价单。",
        "input_schema": {
            "type": "object",
            "properties": {
                "coin": {"type": "string", "description": "币种"},
                "side": {"type": "string", "enum": ["buy", "sell"], "description": "买或卖"},
                "size": {"type": "number", "description": "数量（币的数量）"},
                "price": {"type": "number", "description": "限价价格"},
            },
            "required": ["coin", "side", "size", "price"],
        },
    },
    {
        "name": "market_order",
        "description": "下一个市价单（立即成交）。",
        "input_schema": {
            "type": "object",
            "properties": {
                "coin": {"type": "string", "description": "币种"},
                "side": {"type": "string", "enum": ["buy", "sell"], "description": "买或卖"},
                "size": {"type": "number", "description": "数量（币的数量）"},
            },
            "required": ["coin", "side", "size"],
        },
    },
    {
        "name": "cancel_orders",
        "description": "撤销某个币的所有挂单。",
        "input_schema": {
            "type": "object",
            "properties": {
                "coin": {"type": "string", "description": "币种"},
            },
            "required": ["coin"],
        },
    },
    {
        "name": "close_position",
        "description": "平仓（市价关闭某个币的所有持仓）。",
        "input_schema": {
            "type": "object",
            "properties": {
                "coin": {"type": "string", "description": "币种"},
            },
            "required": ["coin"],
        },
    },
    {
        "name": "get_open_orders",
        "description": "查看当前所有挂单。",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "set_leverage",
        "description": "设置某个币的杠杆倍数。",
        "input_schema": {
            "type": "object",
            "properties": {
                "coin": {"type": "string", "description": "币种"},
                "leverage": {"type": "integer", "description": "杠杆倍数，如 3, 5, 10"},
            },
            "required": ["coin", "leverage"],
        },
    },
    {
        "name": "get_funding_rates",
        "description": "查看某个币最近的资金费率。",
        "input_schema": {
            "type": "object",
            "properties": {
                "coin": {"type": "string", "description": "币种"},
            },
            "required": ["coin"],
        },
    },
]


class TradingTools:
    def __init__(self, client: HLRestClient, paper: bool = True) -> None:
        self._client = client
        self._paper = paper
        self._tag = "[PAPER] " if paper else ""

    def execute(self, name: str, args: dict[str, Any]) -> str:
        handler = getattr(self, f"_tool_{name}", None)
        if not handler:
            return json.dumps({"error": f"Unknown tool: {name}"})
        try:
            result = handler(**args)
            return json.dumps(result, ensure_ascii=False, default=str)
        except Exception as e:
            log.exception("Tool %s failed", name)
            return json.dumps({"error": str(e)})

    def _tool_get_account(self) -> dict:
        account = self._client.get_account_state()
        positions = []
        for p in account.positions:
            if abs(p.size) < 1e-8:
                continue
            positions.append({
                "coin": p.coin,
                "size": p.size,
                "entry_price": p.entry_price,
                "mark_price": p.mark_price,
                "unrealized_pnl": p.unrealized_pnl,
                "leverage": p.leverage,
            })
        return {
            "equity": account.equity,
            "available_balance": account.available_balance,
            "margin_used": account.margin_used,
            "positions": positions,
        }

    def _tool_get_price(self, coins: list[str] | None = None) -> dict:
        mids = self._client.get_all_mids()
        if coins:
            return {c: mids.get(c, "not found") for c in coins}
        return dict(list(mids.items())[:20])

    def _tool_get_orderbook(self, coin: str) -> dict:
        book = self._client.get_l2_snapshot(coin)
        levels = book.get("levels", [[], []])
        bids = levels[0][:5] if len(levels) > 0 else []
        asks = levels[1][:5] if len(levels) > 1 else []

        mid = 0.0
        if bids and asks:
            bb = float(bids[0]["px"])
            ba = float(asks[0]["px"])
            mid = (bb + ba) / 2
            spread_bps = (ba - bb) / mid * 10000 if mid > 0 else 0
        else:
            spread_bps = 0

        return {
            "coin": coin,
            "mid": mid,
            "spread_bps": round(spread_bps, 1),
            "bids": [{"price": b["px"], "size": b["sz"]} for b in bids],
            "asks": [{"price": a["px"], "size": a["sz"]} for a in asks],
        }

    def _tool_scan_pairs(self, top_n: int = 15) -> dict:
        meta = self._client.get_meta()
        mids = self._client.get_all_mids()
        universe = meta.get("universe", [])

        results = []
        for asset in universe:
            coin = asset.get("name", "")
            mid = mids.get(coin, 0)
            if not mid or mid <= 0:
                continue
            try:
                book = self._client.get_l2_snapshot(coin)
            except Exception:
                continue

            levels = book.get("levels", [[], []])
            bids = levels[0] if len(levels) > 0 else []
            asks = levels[1] if len(levels) > 1 else []
            if not bids or not asks:
                continue

            bb = float(bids[0]["px"])
            ba = float(asks[0]["px"])
            spread_bps = (ba - bb) / mid * 10000

            bid_depth = sum(float(b["px"]) * float(b["sz"]) for b in bids[:5])
            ask_depth = sum(float(a["px"]) * float(a["sz"]) for a in asks[:5])
            total_depth = bid_depth + ask_depth
            score = spread_bps * min(total_depth, 50000) / 10000

            results.append({
                "coin": coin,
                "mid": round(mid, 6),
                "spread_bps": round(spread_bps, 1),
                "depth_usd": round(total_depth, 0),
                "score": round(score, 1),
            })

        results.sort(key=lambda x: x["score"], reverse=True)
        return {"pairs": results[:top_n], "total_scanned": len(results)}

    def _tool_place_order(self, coin: str, side: str, size: float, price: float) -> dict:
        if self._paper:
            log.info("%sPlace %s %s %.6f @ %.6f", self._tag, side, coin, size, price)
            return {"status": "paper_placed", "coin": coin, "side": side, "size": size, "price": price}

        req = OrderRequest(
            coin=coin,
            side=Side.BUY if side == "buy" else Side.SELL,
            size=size,
            price=price,
            order_type=OrderType.LIMIT,
            reduce_only=False,
        )
        result = self._client.place_order(req)
        return {
            "status": result.status.value,
            "order_id": result.order_id,
            "filled_size": result.filled_size,
            "price": result.price,
        }

    def _tool_market_order(self, coin: str, side: str, size: float) -> dict:
        if self._paper:
            log.info("%sMarket %s %s %.6f", self._tag, side, coin, size)
            return {"status": "paper_filled", "coin": coin, "side": side, "size": size}

        req = OrderRequest(
            coin=coin,
            side=Side.BUY if side == "buy" else Side.SELL,
            size=size,
            order_type=OrderType.MARKET,
            reduce_only=False,
        )
        result = self._client.place_order(req)
        return {
            "status": result.status.value,
            "order_id": result.order_id,
            "filled_size": result.filled_size,
            "price": result.price,
        }

    def _tool_cancel_orders(self, coin: str) -> dict:
        if self._paper:
            log.info("%sCancel all orders for %s", self._tag, coin)
            return {"status": "paper_cancelled", "coin": coin}

        open_orders = self._client.get_open_orders()
        cancelled = 0
        for order in open_orders:
            if order.get("coin") == coin:
                try:
                    self._client.cancel_order(coin, order["oid"])
                    cancelled += 1
                except Exception:
                    pass
        return {"cancelled": cancelled, "coin": coin}

    def _tool_close_position(self, coin: str) -> dict:
        if self._paper:
            log.info("%sClose position %s", self._tag, coin)
            return {"status": "paper_closed", "coin": coin}

        try:
            self._client.close_position(coin)
            return {"status": "closed", "coin": coin}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def _tool_get_open_orders(self) -> dict:
        orders = self._client.get_open_orders()
        return {"orders": orders[:20], "total": len(orders)}

    def _tool_set_leverage(self, coin: str, leverage: int) -> dict:
        if self._paper:
            log.info("%sSet leverage %s x%d", self._tag, coin, leverage)
            return {"status": "paper_set", "coin": coin, "leverage": leverage}

        try:
            self._client.update_leverage(coin, leverage)
            return {"status": "set", "coin": coin, "leverage": leverage}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def _tool_get_funding_rates(self, coin: str) -> dict:
        import time
        end = int(time.time() * 1000)
        start = end - 24 * 3600 * 1000
        rates = self._client.get_funding_rates(coin, start, end)
        return {
            "coin": coin,
            "rates": [
                {"rate": r.rate, "time": r.timestamp.isoformat()}
                for r in rates[-8:]
            ],
        }
