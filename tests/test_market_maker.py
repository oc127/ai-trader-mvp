"""Tests for HFT market maker — quotes, inventory, skew, flatten, circuit breakers."""

from __future__ import annotations

import time

from src.polymarket.market_maker import HighFreqMarketMaker, MarketInventory, MakerConfig, QuotePair
from src.polymarket.types import Market


def _cfg(**overrides) -> dict:
    base = {
        "polymarket": {
            "hf_market_maker": {},
        }
    }
    if overrides:
        base["polymarket"]["hf_market_maker"].update(overrides)
    return base


def _market(
    cid: str = "cond1",
    question: str = "Will X happen?",
    yes_price: float = 0.50,
    volume_24h: float = 10000,
    liquidity: float = 50000,
    active: bool = True,
) -> Market:
    return Market(
        condition_id=cid, question=question, slug="will-x-happen",
        yes_token_id="tok_yes", no_token_id="tok_no",
        yes_price=yes_price, no_price=1.0 - yes_price,
        volume=100000, volume_24h=volume_24h, liquidity=liquidity,
        active=active,
    )


# ── Market Selection ──

class TestMarketSelection:
    def test_selects_eligible_markets(self):
        mm = HighFreqMarketMaker(_cfg())
        markets = [_market(yes_price=0.50, liquidity=50000, volume_24h=10000)]
        selected = mm.select_markets(markets)
        assert len(selected) == 1

    def test_filters_low_liquidity(self):
        mm = HighFreqMarketMaker(_cfg())
        markets = [_market(liquidity=1000)]
        selected = mm.select_markets(markets)
        assert len(selected) == 0

    def test_filters_low_volume(self):
        mm = HighFreqMarketMaker(_cfg())
        markets = [_market(volume_24h=100)]
        selected = mm.select_markets(markets)
        assert len(selected) == 0

    def test_filters_extreme_price_low(self):
        mm = HighFreqMarketMaker(_cfg())
        markets = [_market(yes_price=0.05)]
        selected = mm.select_markets(markets)
        assert len(selected) == 0

    def test_filters_extreme_price_high(self):
        mm = HighFreqMarketMaker(_cfg())
        markets = [_market(yes_price=0.95)]
        selected = mm.select_markets(markets)
        assert len(selected) == 0

    def test_filters_inactive(self):
        mm = HighFreqMarketMaker(_cfg())
        markets = [_market(active=False)]
        selected = mm.select_markets(markets)
        assert len(selected) == 0

    def test_limits_max_markets(self):
        mm = HighFreqMarketMaker(_cfg(max_markets=2))
        markets = [_market(cid=f"c{i}", volume_24h=10000 + i * 1000) for i in range(5)]
        selected = mm.select_markets(markets)
        assert len(selected) == 2

    def test_ranks_by_quality(self):
        mm = HighFreqMarketMaker(_cfg(max_markets=2))
        m1 = _market(cid="low", volume_24h=5000, liquidity=20000)
        m2 = _market(cid="high", volume_24h=50000, liquidity=100000)
        selected = mm.select_markets([m1, m2])
        assert selected[0].condition_id == "high"


# ── Quote Generation ──

class TestQuoteGeneration:
    def test_generates_valid_quotes(self):
        mm = HighFreqMarketMaker(_cfg())
        m = _market(yes_price=0.50)
        quote = mm.generate_quotes(m, book_spread=0.06)
        assert quote is not None
        assert quote.bid_price < quote.ask_price
        assert quote.bid_price > 0
        assert quote.ask_price < 1
        assert quote.spread > 0

    def test_bid_below_ask(self):
        mm = HighFreqMarketMaker(_cfg())
        m = _market(yes_price=0.50)
        quote = mm.generate_quotes(m, book_spread=0.10)
        assert quote.bid_price < quote.ask_price

    def test_quotes_on_narrow_spread(self):
        """Bot places resting orders even on tight-spread markets."""
        mm = HighFreqMarketMaker(_cfg())
        m = _market(yes_price=0.50)
        quote = mm.generate_quotes(m, book_spread=0.01)
        assert quote is not None
        assert quote.spread >= mm.config.min_half_spread * 2

    def test_spread_inside_book(self):
        mm = HighFreqMarketMaker(_cfg())
        m = _market(yes_price=0.50)
        quote = mm.generate_quotes(m, book_spread=0.10)
        assert quote.spread <= 0.10

    def test_quotes_centered_on_mid(self):
        mm = HighFreqMarketMaker(_cfg())
        m = _market(yes_price=0.50)
        quote = mm.generate_quotes(m, book_spread=0.10)
        mid = (quote.bid_price + quote.ask_price) / 2
        assert abs(mid - 0.50) < 0.02

    def test_skew_with_long_inventory(self):
        mm = HighFreqMarketMaker(_cfg())
        m = _market(yes_price=0.50)
        mm.on_fill("cond1", "Will X?", "tok_yes", "tok_no", "BUY", "tok_yes", 0.50, 50)
        quote = mm.generate_quotes(m, book_spread=0.10)
        assert quote is not None
        mid = (quote.bid_price + quote.ask_price) / 2
        assert mid < 0.50

    def test_no_quote_at_position_limit(self):
        mm = HighFreqMarketMaker(_cfg(max_position_per_market=10))
        m = _market(yes_price=0.50)
        mm.on_fill("cond1", "Will X?", "tok_yes", "tok_no", "BUY", "tok_yes", 0.50, 100)
        quote = mm.generate_quotes(m, book_spread=0.10)
        assert quote is None

    def test_no_quote_at_total_exposure_limit(self):
        mm = HighFreqMarketMaker(_cfg(max_total_exposure=5))
        m = _market(yes_price=0.50)
        mm.on_fill("cond1", "Will X?", "tok_yes", "tok_no", "BUY", "tok_yes", 0.50, 100)
        quote = mm.generate_quotes(m, book_spread=0.10)
        assert quote is None

    def test_reduced_size_near_limit(self):
        mm = HighFreqMarketMaker(_cfg(max_position_per_market=100, flatten_at_pct=0.50))
        m = _market(yes_price=0.50)
        mm.on_fill("cond1", "Will X?", "tok_yes", "tok_no", "BUY", "tok_yes", 0.50, 60)
        quote = mm.generate_quotes(m, book_spread=0.10)
        assert quote is not None
        normal_quote = HighFreqMarketMaker(_cfg()).generate_quotes(m, book_spread=0.10)
        # compare USD notional (size * price), not shares — skew shifts price
        assert quote.bid_size * quote.bid_price < normal_quote.bid_size * normal_quote.bid_price


# ── Inventory Tracking ──

class TestInventoryTracking:
    def test_buy_yes_updates_inventory(self):
        mm = HighFreqMarketMaker(_cfg())
        mm.on_fill("c1", "Q?", "ty", "tn", "BUY", "ty", 0.50, 100)
        inv = mm._inventory["c1"]
        assert inv.yes_shares == 100
        assert inv.yes_avg_price == 0.50

    def test_buy_no_updates_inventory(self):
        mm = HighFreqMarketMaker(_cfg())
        mm.on_fill("c1", "Q?", "ty", "tn", "BUY", "tn", 0.40, 50)
        inv = mm._inventory["c1"]
        assert inv.no_shares == 50
        assert inv.no_avg_price == 0.40

    def test_avg_price_updates_on_multiple_buys(self):
        mm = HighFreqMarketMaker(_cfg())
        mm.on_fill("c1", "Q?", "ty", "tn", "BUY", "ty", 0.50, 100)
        mm.on_fill("c1", "Q?", "ty", "tn", "BUY", "ty", 0.60, 100)
        inv = mm._inventory["c1"]
        assert inv.yes_shares == 200
        assert abs(inv.yes_avg_price - 0.55) < 0.001

    def test_sell_realizes_pnl(self):
        mm = HighFreqMarketMaker(_cfg())
        mm.on_fill("c1", "Q?", "ty", "tn", "BUY", "ty", 0.50, 100)
        mm.on_fill("c1", "Q?", "ty", "tn", "SELL", "ty", 0.55, 100)
        inv = mm._inventory["c1"]
        assert inv.yes_shares == 0
        assert inv.realized_pnl > 0
        assert mm.daily_pnl > 0

    def test_net_exposure(self):
        mm = HighFreqMarketMaker(_cfg())
        mm.on_fill("c1", "Q?", "ty", "tn", "BUY", "ty", 0.50, 100)
        mm.on_fill("c1", "Q?", "ty", "tn", "BUY", "tn", 0.40, 80)
        inv = mm._inventory["c1"]
        expected = 100 * 0.50 - 80 * 0.40
        assert abs(inv.net_exposure - expected) < 0.01

    def test_is_flat(self):
        mm = HighFreqMarketMaker(_cfg())
        mm.on_fill("c1", "Q?", "ty", "tn", "BUY", "ty", 0.50, 100)
        assert not mm._inventory["c1"].is_flat
        mm.on_fill("c1", "Q?", "ty", "tn", "SELL", "ty", 0.50, 100)
        assert mm._inventory["c1"].is_flat


# ── Auto-Flatten ──

class TestAutoFlatten:
    def test_stale_position_detected(self):
        mm = HighFreqMarketMaker(_cfg(max_inventory_age_seconds=0.01))
        mm.on_fill("c1", "Q?", "ty", "tn", "BUY", "ty", 0.50, 100)
        time.sleep(0.02)
        stale = mm.get_stale_positions()
        assert len(stale) == 1

    def test_fresh_position_not_stale(self):
        mm = HighFreqMarketMaker(_cfg(max_inventory_age_seconds=300))
        mm.on_fill("c1", "Q?", "ty", "tn", "BUY", "ty", 0.50, 100)
        stale = mm.get_stale_positions()
        assert len(stale) == 0

    def test_flat_position_not_stale(self):
        mm = HighFreqMarketMaker(_cfg(max_inventory_age_seconds=0.01))
        mm.on_fill("c1", "Q?", "ty", "tn", "BUY", "ty", 0.50, 100)
        mm.on_fill("c1", "Q?", "ty", "tn", "SELL", "ty", 0.50, 100)
        time.sleep(0.02)
        stale = mm.get_stale_positions()
        assert len(stale) == 0

    def test_flatten_yes_position(self):
        mm = HighFreqMarketMaker(_cfg())
        mm.on_fill("c1", "Q?", "ty", "tn", "BUY", "ty", 0.50, 100)
        order = mm.flatten_inventory("c1")
        assert order is not None
        assert order["side"] == "SELL"
        assert order["token_id"] == "ty"
        assert order["size"] == 100

    def test_flatten_no_position(self):
        mm = HighFreqMarketMaker(_cfg())
        mm.on_fill("c1", "Q?", "ty", "tn", "BUY", "tn", 0.40, 50)
        order = mm.flatten_inventory("c1")
        assert order is not None
        assert order["side"] == "SELL"
        assert order["token_id"] == "tn"
        assert order["size"] == 50

    def test_flatten_flat_returns_none(self):
        mm = HighFreqMarketMaker(_cfg())
        order = mm.flatten_inventory("nonexistent")
        assert order is None


# ── Circuit Breakers ──

class TestCircuitBreakers:
    def test_paused_after_daily_loss(self):
        mm = HighFreqMarketMaker(_cfg(max_daily_loss=1.0))
        mm.on_fill("c1", "Q?", "ty", "tn", "BUY", "ty", 0.50, 100)
        mm.on_fill("c1", "Q?", "ty", "tn", "SELL", "ty", 0.48, 100)
        assert mm.is_paused

    def test_paused_after_consecutive_losses(self):
        mm = HighFreqMarketMaker(_cfg(max_consecutive_losses=2, pause_after_loss_seconds=0.01))
        for i in range(3):
            mm.on_fill(f"c{i}", "Q?", "ty", "tn", "BUY", "ty", 0.50, 10)
            mm.on_fill(f"c{i}", "Q?", "ty", "tn", "SELL", "ty", 0.49, 10)
        assert mm._consecutive_losses >= 2

    def test_winning_trade_resets_streak(self):
        mm = HighFreqMarketMaker(_cfg())
        mm.on_fill("c1", "Q?", "ty", "tn", "BUY", "ty", 0.50, 10)
        mm.on_fill("c1", "Q?", "ty", "tn", "SELL", "ty", 0.49, 10)
        assert mm._consecutive_losses == 1
        mm.on_fill("c2", "Q?", "ty", "tn", "BUY", "ty", 0.50, 10)
        mm.on_fill("c2", "Q?", "ty", "tn", "SELL", "ty", 0.55, 10)
        assert mm._consecutive_losses == 0

    def test_no_quotes_when_paused(self):
        mm = HighFreqMarketMaker(_cfg(max_daily_loss=0.01))
        mm._daily_pnl = -1.0
        m = _market(yes_price=0.50)
        quote = mm.generate_quotes(m, book_spread=0.10)
        assert quote is None

    def test_daily_reset(self):
        mm = HighFreqMarketMaker(_cfg())
        mm._daily_pnl = -100.0
        mm._consecutive_losses = 10
        mm._paused_until = time.monotonic() + 9999
        mm.reset_daily()
        assert mm._daily_pnl == 0.0
        assert mm._consecutive_losses == 0
        assert not mm.is_paused


# ── Status ──

class TestStatus:
    def test_status_empty(self):
        mm = HighFreqMarketMaker(_cfg())
        s = mm.status()
        assert s["daily_pnl"] == 0
        assert s["total_trades"] == 0
        assert s["active_markets"] == 0
        assert s["win_rate"] == 0

    def test_status_with_trades(self):
        mm = HighFreqMarketMaker(_cfg())
        mm.on_fill("c1", "Q?", "ty", "tn", "BUY", "ty", 0.50, 100)
        mm.on_fill("c1", "Q?", "ty", "tn", "SELL", "ty", 0.55, 100)
        s = mm.status()
        assert s["total_trades"] == 2
        assert s["daily_pnl"] > 0
        assert s["win_rate"] > 0

    def test_total_exposure(self):
        mm = HighFreqMarketMaker(_cfg())
        mm.on_fill("c1", "Q?", "ty", "tn", "BUY", "ty", 0.50, 100)
        assert mm.total_exposure > 0
