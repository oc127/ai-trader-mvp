from __future__ import annotations

import pytest

from src.strategy.gate_market_maker import GateMarketMaker, Quote


class FakeClient:
    def __init__(self) -> None:
        self.orders: list[dict] = []
        self._next_id = 1
        self.book = {
            "bids": [["0.100000", "10000"], ["0.099900", "20000"]],
            "asks": [["0.100100", "10000"], ["0.100200", "20000"]],
        }
        self.balances = {"USDT": 500.0, "TEST": 0.0}

    def get_order_book(self, pair: str, limit: int = 20) -> dict:
        return self.book

    def get_spot_balances(self) -> dict[str, float]:
        return dict(self.balances)

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

    def spot_market_buy(self, pair: str, spend_usdt: float) -> dict:
        return {"id": "market_1"}

    def cancel_all_orders(self, pair: str) -> list:
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
            "amount_precision": {"TEST_USDT": 0},
        },
    }


@pytest.fixture
def client():
    return FakeClient()


@pytest.fixture
def mm(client, config):
    return GateMarketMaker(client, config)


def test_tick_buy_only_no_coins(mm, client):
    result = mm.tick("TEST_USDT")
    assert result["action"] == "refreshed"
    buys = [o for o in client.orders if o["side"] == "buy"]
    sells = [o for o in client.orders if o["side"] == "sell"]
    assert len(buys) == 2
    assert len(sells) == 0


def test_tick_both_sides_with_coins(mm, client):
    client.balances["TEST"] = 5000.0
    result = mm.tick("TEST_USDT")
    assert result["action"] == "refreshed"
    buys = [o for o in client.orders if o["side"] == "buy"]
    sells = [o for o in client.orders if o["side"] == "sell"]
    assert len(buys) > 0
    assert len(sells) > 0


def test_sell_limited_to_balance(mm, client):
    client.balances["TEST"] = 100.0
    mm.tick("TEST_USDT")
    sells = [o for o in client.orders if o["side"] == "sell"]
    total_sell = sum(o["amount"] for o in sells)
    assert total_sell <= 100.0


def test_no_buy_when_max_inventory(mm, client):
    client.balances["TEST"] = 100000.0
    mm.tick("TEST_USDT")
    buys = [o for o in client.orders if o["side"] == "buy"]
    assert len(buys) == 0


def test_no_buy_when_no_usdt(mm, client):
    client.balances["USDT"] = 0.0
    mm.tick("TEST_USDT")
    buys = [o for o in client.orders if o["side"] == "buy"]
    assert len(buys) == 0


def test_cancel_all(mm, client):
    client.balances["TEST"] = 5000.0
    mm.tick("TEST_USDT")
    assert len(client.orders) > 0
    mm.cancel_all()
    assert len(client.orders) == 0


def test_summary(mm, client):
    mm.tick("TEST_USDT")
    summary = mm.get_summary()
    assert "TEST_USDT" in summary
    assert "pnl" in summary


def test_tiers_respect_price_order(mm, client):
    client.balances["TEST"] = 5000.0
    mm.tick("TEST_USDT")
    buys = sorted([o for o in client.orders if o["side"] == "buy"], key=lambda x: -x["price"])
    sells = sorted([o for o in client.orders if o["side"] == "sell"], key=lambda x: x["price"])
    assert buys[0]["price"] > buys[1]["price"]
    assert sells[0]["price"] < sells[1]["price"]
