from __future__ import annotations

import pytest

from src.strategy.gate_market_maker import GateMarketMaker, Quote


class FakeClient:
    def __init__(self) -> None:
        self.orders: list[dict] = []
        self._next_id = 1
        self._cancelled: list[str] = []
        self.book = {
            "bids": [["0.100000", "10000"], ["0.099900", "20000"]],
            "asks": [["0.100100", "10000"], ["0.100200", "20000"]],
        }

    def get_order_book(self, pair: str, limit: int = 20) -> dict:
        return self.book

    def spot_limit_buy(self, pair: str, price: float, amount: float) -> dict:
        oid = str(self._next_id)
        self._next_id += 1
        self.orders.append({"id": oid, "side": "buy", "pair": pair, "price": price, "amount": amount})
        return {"id": oid}

    def spot_limit_sell(self, pair: str, price: float, amount: float) -> dict:
        oid = str(self._next_id)
        self._next_id += 1
        self.orders.append({"id": oid, "side": "sell", "pair": pair, "price": price, "amount": amount})
        return {"id": oid}

    def cancel_all_orders(self, pair: str) -> list:
        self._cancelled.extend(o["id"] for o in self.orders if o["pair"] == pair)
        self.orders = [o for o in self.orders if o["pair"] != pair]
        return []

    def list_open_orders(self, pair: str) -> list:
        return [o for o in self.orders if o["pair"] == pair]


@pytest.fixture
def config():
    return {
        "market_maker": {
            "pairs": ["TEST_USDT"],
            "base_spread_bps": 10,
            "min_spread_bps": 5,
            "num_tiers": 2,
            "tier_spacing_bps": 5,
            "tier_size_multiplier": 1.5,
            "base_order_usd": 50,
            "max_inventory_usd": 2000,
            "max_total_inventory_usd": 5000,
            "refresh_interval_sec": 0,
            "skew_intensity": 1.0,
            "price_precision": {"TEST_USDT": 6},
            "amount_precision": {"TEST_USDT": 2},
        },
    }


@pytest.fixture
def client():
    return FakeClient()


@pytest.fixture
def mm(client, config):
    return GateMarketMaker(client, config)


def test_tick_places_orders(mm, client):
    result = mm.tick("TEST_USDT")
    assert result["action"] == "refreshed"
    assert result["n_quotes"] > 0
    assert len(client.orders) > 0

    buy_orders = [o for o in client.orders if o["side"] == "buy"]
    sell_orders = [o for o in client.orders if o["side"] == "sell"]
    assert len(buy_orders) == 2
    assert len(sell_orders) == 2


def test_tick_respects_tiers(mm, client):
    mm.tick("TEST_USDT")
    buys = sorted([o for o in client.orders if o["side"] == "buy"], key=lambda x: -x["price"])
    sells = sorted([o for o in client.orders if o["side"] == "sell"], key=lambda x: x["price"])

    assert buys[0]["price"] > buys[1]["price"]
    assert sells[0]["price"] < sells[1]["price"]
    assert buys[1]["amount"] > buys[0]["amount"]


def test_inventory_skew(mm, client):
    state = mm.get_state("TEST_USDT")
    state.inventory_usd = 1500

    mm.tick("TEST_USDT")

    buys = [o for o in client.orders if o["side"] == "buy"]
    sells = [o for o in client.orders if o["side"] == "sell"]

    if buys and sells:
        mid = 0.10005
        avg_bid = sum(o["price"] for o in buys) / len(buys)
        avg_ask = sum(o["price"] for o in sells) / len(sells)
        skewed_mid = (avg_bid + avg_ask) / 2
        assert skewed_mid < mid


def test_max_inventory_stops_buying(mm, client):
    state = mm.get_state("TEST_USDT")
    state.inventory_usd = 2001

    mm.tick("TEST_USDT")

    buys = [o for o in client.orders if o["side"] == "buy"]
    assert len(buys) == 0


def test_cancel_all(mm, client):
    mm.tick("TEST_USDT")
    assert len(client.orders) > 0

    mm.cancel_all()
    assert len(client.orders) == 0


def test_summary(mm, client):
    mm.tick("TEST_USDT")
    summary = mm.get_summary()
    assert "TEST_USDT" in summary
    assert "pnl" in summary


def test_multiple_pairs(client):
    config = {
        "market_maker": {
            "pairs": ["TEST_USDT", "FOO_USDT"],
            "base_spread_bps": 10,
            "num_tiers": 1,
            "tier_spacing_bps": 5,
            "base_order_usd": 50,
            "max_inventory_usd": 2000,
            "max_total_inventory_usd": 5000,
            "refresh_interval_sec": 0,
        },
    }
    mm = GateMarketMaker(client, config)
    r1 = mm.tick("TEST_USDT")
    r2 = mm.tick("FOO_USDT")
    assert r1["action"] == "refreshed"
    assert r2["action"] == "refreshed"
