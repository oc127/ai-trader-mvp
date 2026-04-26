"""Tests for HLMarketMaker strategy — 5-tier quoting, inventory skew, book imbalance."""

from __future__ import annotations

from src.strategy.market_maker import HLMarketMaker


def _make_cfg(overrides: dict | None = None) -> dict:
    """Build a minimal config with MM enabled."""
    mm_cfg = {
        "enabled": True,
        "coins": ["BTC", "ETH"],
        "spread_bps": 3,
        "num_tiers": 5,
        "tier_spacing_bps": 2,
        "tier_size_multiplier": 1.5,
        "base_order_usd": 500,
        "max_inventory_usd": 50000,
        "volatility_pause_pct": 0.01,
        "volatility_cooldown_sec": 120,
    }
    if overrides:
        mm_cfg.update(overrides)
    return {"strategy": {"market_maker": mm_cfg}}


# --- Test 1: correct number of tiers ---

def test_generates_correct_number_of_tiers():
    mm = HLMarketMaker(_make_cfg())
    sig = mm.generate_quotes("BTC", 60000.0, 59990.0, 60010.0, 0.0, 0.0)

    assert sig.action == "quote_refresh"
    buy_quotes = [q for q in sig.quotes if q.side == "buy"]
    sell_quotes = [q for q in sig.quotes if q.side == "sell"]
    assert len(buy_quotes) == 5
    assert len(sell_quotes) == 5


# --- Test 2: bid below ask on each tier ---

def test_bid_below_ask_each_tier():
    mm = HLMarketMaker(_make_cfg())
    sig = mm.generate_quotes("BTC", 60000.0, 59990.0, 60010.0, 0.0, 0.0)

    buy_quotes = sorted([q for q in sig.quotes if q.side == "buy"], key=lambda q: q.tier)
    sell_quotes = sorted([q for q in sig.quotes if q.side == "sell"], key=lambda q: q.tier)

    for tier in range(5):
        bid = buy_quotes[tier]
        ask = sell_quotes[tier]
        assert bid.price < ask.price, f"Tier {tier}: bid {bid.price} >= ask {ask.price}"
        assert bid.tier == tier
        assert ask.tier == tier


# --- Test 3: tier sizes increase by multiplier ---

def test_tier_sizes_increase_by_multiplier():
    mm = HLMarketMaker(_make_cfg())
    sig = mm.generate_quotes("BTC", 60000.0, 59990.0, 60010.0, 0.0, 0.0)

    buy_quotes = sorted([q for q in sig.quotes if q.side == "buy"], key=lambda q: q.tier)

    for i in range(1, 5):
        ratio = buy_quotes[i].size / buy_quotes[i - 1].size
        assert abs(ratio - 1.5) < 0.01, f"Tier {i}: size ratio {ratio} != 1.5"

    # Verify tier 0 size in USD (500 USD at 60000 price = 500/60000 coins)
    expected_size_0 = 500 / 60000.0
    assert abs(buy_quotes[0].size - expected_size_0) < 1e-8


# --- Test 4: inventory skew shifts prices ---

def test_inventory_skew_shifts_prices():
    mm = HLMarketMaker(_make_cfg())

    # No inventory: symmetric quotes
    sig_neutral = mm.generate_quotes("BTC", 60000.0, 59990.0, 60010.0, 0.0, 0.0)
    buy_neutral = [q for q in sig_neutral.quotes if q.side == "buy" and q.tier == 0][0]
    sell_neutral = [q for q in sig_neutral.quotes if q.side == "sell" and q.tier == 0][0]
    mid_neutral = (buy_neutral.price + sell_neutral.price) / 2

    # Long inventory: skew should lower mid (encourage sells)
    mm._inventory["BTC"] = 25000.0  # half of max
    sig_long = mm.generate_quotes("BTC", 60000.0, 59990.0, 60010.0, 0.0, 25000.0)
    buy_long = [q for q in sig_long.quotes if q.side == "buy" and q.tier == 0][0]
    sell_long = [q for q in sig_long.quotes if q.side == "sell" and q.tier == 0][0]
    mid_long = (buy_long.price + sell_long.price) / 2

    assert mid_long < mid_neutral, "Long inventory should lower quoted mid"

    # Short inventory: skew should raise mid (encourage buys)
    mm._inventory["BTC"] = -25000.0
    sig_short = mm.generate_quotes("BTC", 60000.0, 59990.0, 60010.0, 0.0, -25000.0)
    buy_short = [q for q in sig_short.quotes if q.side == "buy" and q.tier == 0][0]
    sell_short = [q for q in sig_short.quotes if q.side == "sell" and q.tier == 0][0]
    mid_short = (buy_short.price + sell_short.price) / 2

    assert mid_short > mid_neutral, "Short inventory should raise quoted mid"


# --- Test 5: max inventory stops quoting one side ---

def test_max_inventory_stops_quoting_one_side():
    mm = HLMarketMaker(_make_cfg())

    # Max long inventory: should only quote sells
    mm._inventory["BTC"] = 50000.0  # at limit
    sig = mm.generate_quotes("BTC", 60000.0, 59990.0, 60010.0, 0.0, 50000.0)
    buy_quotes = [q for q in sig.quotes if q.side == "buy"]
    sell_quotes = [q for q in sig.quotes if q.side == "sell"]
    assert len(buy_quotes) == 0, "Should not quote buys at max long inventory"
    assert len(sell_quotes) == 5, "Should still quote sells"

    # Max short inventory: should only quote buys
    mm._inventory["BTC"] = -50000.0
    sig = mm.generate_quotes("BTC", 60000.0, 59990.0, 60010.0, 0.0, -50000.0)
    buy_quotes = [q for q in sig.quotes if q.side == "buy"]
    sell_quotes = [q for q in sig.quotes if q.side == "sell"]
    assert len(buy_quotes) == 5, "Should still quote buys"
    assert len(sell_quotes) == 0, "Should not quote sells at max short inventory"


# --- Test 6: volatility detection widens spread ---

def test_volatility_widens_spread():
    mm = HLMarketMaker(_make_cfg())

    # First tick establishes baseline mid
    sig1 = mm.generate_quotes("BTC", 60000.0, 59990.0, 60010.0, 0.0, 0.0)
    buy1 = [q for q in sig1.quotes if q.side == "buy" and q.tier == 0][0]
    sell1 = [q for q in sig1.quotes if q.side == "sell" and q.tier == 0][0]
    spread1 = sell1.price - buy1.price

    # Second tick with >1% move: spread should be wider
    # Reset the cooldown so we don't get cancelled
    mm._volatility_pause_until["BTC"] = 0
    new_price = 60000.0 * 1.015  # 1.5% up
    sig2 = mm.generate_quotes("BTC", new_price, new_price - 10, new_price + 10, 0.0, 0.0)

    assert sig2.action == "quote_refresh"
    buy2 = [q for q in sig2.quotes if q.side == "buy" and q.tier == 0][0]
    sell2 = [q for q in sig2.quotes if q.side == "sell" and q.tier == 0][0]
    spread2 = sell2.price - buy2.price

    # Spread should be wider (2x) after volatility
    assert spread2 > spread1 * 1.5, f"Volatile spread {spread2} not wider than normal {spread1}"


# --- Test 7: fill tracking updates inventory ---

def test_fill_updates_inventory():
    mm = HLMarketMaker(_make_cfg())

    assert mm.get_inventory("BTC") == 0.0

    mm.on_fill("BTC", "buy", 0.1, 60000.0)  # bought 0.1 BTC at 60k = 6000 USD
    assert mm.get_inventory("BTC") == 6000.0

    mm.on_fill("BTC", "sell", 0.05, 60000.0)  # sold 0.05 BTC = 3000 USD
    assert mm.get_inventory("BTC") == 3000.0

    mm.on_fill("BTC", "sell", 0.1, 60000.0)  # sold 0.1 BTC = -3000 net
    assert mm.get_inventory("BTC") == -3000.0


# --- Test 8: book imbalance widens spread ---

def test_book_imbalance_widens_spread():
    mm = HLMarketMaker(_make_cfg())

    # Balanced book
    sig_balanced = mm.generate_quotes("BTC", 60000.0, 59990.0, 60010.0, 0.0, 0.0)
    buy_b = [q for q in sig_balanced.quotes if q.side == "buy" and q.tier == 0][0]
    sell_b = [q for q in sig_balanced.quotes if q.side == "sell" and q.tier == 0][0]
    spread_balanced = sell_b.price - buy_b.price

    # Highly imbalanced book (e.g. 0.8)
    mm2 = HLMarketMaker(_make_cfg())
    sig_imbalanced = mm2.generate_quotes("BTC", 60000.0, 59990.0, 60010.0, 0.8, 0.0)
    buy_i = [q for q in sig_imbalanced.quotes if q.side == "buy" and q.tier == 0][0]
    sell_i = [q for q in sig_imbalanced.quotes if q.side == "sell" and q.tier == 0][0]
    spread_imbalanced = sell_i.price - buy_i.price

    assert spread_imbalanced > spread_balanced, "Imbalanced book should widen spread"


# --- Test 9: disabled returns no quotes ---

def test_disabled_returns_no_quotes():
    cfg = _make_cfg({"enabled": False})
    mm = HLMarketMaker(cfg)

    sig = mm.generate_quotes("BTC", 60000.0, 59990.0, 60010.0, 0.0, 0.0)
    assert sig.action == "none"
    assert len(sig.quotes) == 0
    assert "disabled" in sig.reason.lower()


# --- Test 10: multiple coins independent signals ---

def test_multiple_coins_independent():
    mm = HLMarketMaker(_make_cfg())

    sig_btc = mm.generate_quotes("BTC", 60000.0, 59990.0, 60010.0, 0.0, 0.0)
    sig_eth = mm.generate_quotes("ETH", 3000.0, 2999.0, 3001.0, 0.0, 0.0)

    assert sig_btc.coin == "BTC"
    assert sig_eth.coin == "ETH"

    # BTC quotes should be around 60k, ETH around 3k
    btc_buy = [q for q in sig_btc.quotes if q.side == "buy" and q.tier == 0][0]
    eth_buy = [q for q in sig_eth.quotes if q.side == "buy" and q.tier == 0][0]

    assert btc_buy.price > 50000
    assert eth_buy.price < 5000

    # Inventory on one coin shouldn't affect the other
    mm.on_fill("BTC", "buy", 0.5, 60000.0)
    assert mm.get_inventory("BTC") == 30000.0
    assert mm.get_inventory("ETH") == 0.0


# --- Test 11: volatility cooldown cancels quotes ---

def test_volatility_cooldown_cancels():
    mm = HLMarketMaker(_make_cfg())

    # First tick at base price
    mm.generate_quotes("BTC", 60000.0, 59990.0, 60010.0, 0.0, 0.0)

    # Big move triggers cooldown
    mm._volatility_pause_until["BTC"] = 0  # clear so we can trigger
    mm.generate_quotes("BTC", 61200.0, 61190.0, 61210.0, 0.0, 0.0)  # >1% move

    # Next tick during cooldown should cancel
    sig = mm.generate_quotes("BTC", 61200.0, 61190.0, 61210.0, 0.0, 0.0)
    assert sig.action == "cancel"
    assert len(sig.quotes) == 0


# --- Test 12: invalid mid price returns none ---

def test_invalid_mid_price():
    mm = HLMarketMaker(_make_cfg())

    sig = mm.generate_quotes("BTC", 0.0, 0.0, 0.0, 0.0, 0.0)
    assert sig.action == "none"
    assert "Invalid" in sig.reason

    sig2 = mm.generate_quotes("BTC", -100.0, -110.0, -90.0, 0.0, 0.0)
    assert sig2.action == "none"


# --- Test 13: total exposure tracking ---

def test_total_exposure():
    mm = HLMarketMaker(_make_cfg())

    mm.on_fill("BTC", "buy", 0.5, 60000.0)  # +30000
    mm.on_fill("ETH", "sell", 5.0, 3000.0)  # -15000

    assert mm.get_total_exposure() == 45000.0  # 30000 + 15000


# --- Test 14: skew calculation ---

def test_skew_calculation():
    mm = HLMarketMaker(_make_cfg())

    # No inventory -> no skew
    skew = mm._calculate_skew("BTC")
    assert skew == 0.0

    # Half max long -> positive skew
    mm._inventory["BTC"] = 25000.0
    skew = mm._calculate_skew("BTC")
    assert skew > 0

    # Half max short -> negative skew
    mm._inventory["BTC"] = -25000.0
    skew = mm._calculate_skew("BTC")
    assert skew < 0

    # Skew formula: (inv / max_inv) * (spread_bps / 10000) * 0.5
    # = (25000 / 50000) * (3 / 10000) * 0.5 = 0.5 * 0.0003 * 0.5 = 0.000075
    mm._inventory["BTC"] = 25000.0
    expected = 0.5 * (3 / 10000.0) * 0.5
    assert abs(mm._calculate_skew("BTC") - expected) < 1e-10
