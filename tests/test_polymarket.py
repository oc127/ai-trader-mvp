"""Tests for Polymarket bot — paper mode, risk, scanner, strategies."""

from __future__ import annotations

from src.polymarket.types import BotState, Market, Opportunity, Outcome, Side
from src.polymarket.paper import PaperExecutor
from src.polymarket.risk import PolymarketRiskManager
from src.polymarket.strategy import EdgeStrategy, MeanReversionStrategy, MarketMakerStrategy


def _cfg(**overrides) -> dict:
    base = {
        "polymarket": {
            "paper_mode": True,
            "paper_balance": 10000.0,
            "paper_fill_probability": 1.0,
            "risk": {
                "max_position_usd": 500,
                "max_total_exposure_usd": 2000,
                "max_single_market_pct": 0.20,
                "max_daily_trades": 50,
                "max_daily_loss_usd": 500,
                "min_kelly": 0.02,
                "max_kelly": 0.10,
                "min_edge": 0.05,
                "min_confidence": "low",
                "max_errors_before_halt": 10,
            },
            "scanner": {"min_edge": 0.05},
            "strategy": {"min_edge": 0.05, "reversion_size": 0.08, "vol_liq_threshold": 2.0},
            "market_maker": {"half_spread": 0.02, "min_liquidity": 5000, "price_band_low": 0.25, "price_band_high": 0.75},
        }
    }
    for k, v in overrides.items():
        base["polymarket"][k] = v
    return base


def _market(
    cid: str = "cond1",
    question: str = "Will X happen?",
    yes_price: float = 0.60,
    volume: float = 50000,
    liquidity: float = 10000,
) -> Market:
    return Market(
        condition_id=cid, question=question, slug="will-x-happen",
        yes_token_id="tok_yes", no_token_id="tok_no",
        yes_price=yes_price, no_price=1.0 - yes_price,
        volume=volume, volume_24h=1000, liquidity=liquidity,
    )


def _opp(edge: float = 0.10, kelly: float = 0.05, confidence: str = "medium") -> Opportunity:
    m = _market()
    return Opportunity(
        market=m, outcome=Outcome.YES, side=Side.BUY,
        model_prob=m.yes_price + edge, market_prob=m.yes_price,
        edge=edge, ev=edge, kelly_fraction=kelly, confidence=confidence,
    )


# ── Paper Executor ──

class TestPaperExecutor:
    def test_initial_balance(self):
        pe = PaperExecutor(_cfg())
        assert pe.get_balance() == 10000.0

    def test_buy_reduces_balance(self):
        pe = PaperExecutor(_cfg())
        result = pe.place_order("tok_yes", Side.BUY, 0.60, 100, _market())
        assert result.success
        assert result.filled_size == 100
        assert pe.get_balance() < 10000.0

    def test_sell_requires_position(self):
        pe = PaperExecutor(_cfg())
        result = pe.place_order("tok_yes", Side.SELL, 0.65, 100)
        assert not result.success
        assert "Insufficient" in result.error

    def test_buy_then_sell(self):
        pe = PaperExecutor(_cfg())
        pe.place_order("tok_yes", Side.BUY, 0.60, 100, _market())
        result = pe.place_order("tok_yes", Side.SELL, 0.65, 100)
        assert result.success
        # profit: (0.65 - 0.60) * 100 = $5
        assert pe.account.total_pnl > 0

    def test_insufficient_balance(self):
        pe = PaperExecutor(_cfg(paper_balance=10.0))
        result = pe.place_order("tok_yes", Side.BUY, 0.60, 100)
        assert not result.success
        assert "Insufficient" in result.error

    def test_summary(self):
        pe = PaperExecutor(_cfg())
        s = pe.summary()
        assert "Paper Account" in s
        assert "$10000" in s


# ── Risk Manager ──

class TestRiskManager:
    def test_passes_good_opportunity(self):
        rm = PolymarketRiskManager(_cfg())
        state = BotState()
        rc = rm.check_opportunity(_opp(), state, current_exposure=0, balance=10000)
        assert rc.passed

    def test_blocks_low_edge(self):
        rm = PolymarketRiskManager(_cfg())
        state = BotState()
        rc = rm.check_opportunity(_opp(edge=0.01), state, current_exposure=0, balance=10000)
        assert not rc.passed
        assert "Edge" in rc.reason

    def test_blocks_low_kelly(self):
        rm = PolymarketRiskManager(_cfg())
        state = BotState()
        rc = rm.check_opportunity(_opp(kelly=0.001), state, current_exposure=0, balance=10000)
        assert not rc.passed
        assert "Kelly" in rc.reason

    def test_blocks_max_daily_trades(self):
        rm = PolymarketRiskManager(_cfg())
        state = BotState(trades_today=50)
        rc = rm.check_opportunity(_opp(), state, current_exposure=0, balance=10000)
        assert not rc.passed
        assert "Daily" in rc.reason

    def test_blocks_max_exposure(self):
        rm = PolymarketRiskManager(_cfg())
        state = BotState()
        rc = rm.check_opportunity(_opp(), state, current_exposure=2000, balance=10000)
        assert not rc.passed
        assert "exposure" in rc.reason.lower()

    def test_blocks_halted(self):
        rm = PolymarketRiskManager(_cfg())
        state = BotState(halted=True, halt_reason="test halt")
        rc = rm.check_opportunity(_opp(), state, current_exposure=0, balance=10000)
        assert not rc.passed

    def test_position_sizing(self):
        rm = PolymarketRiskManager(_cfg())
        size = rm.size_position(_opp(kelly=0.08), balance=10000, current_exposure=0)
        assert 0 < size <= 500  # capped by max_position_usd

    def test_circuit_breaker_loss(self):
        rm = PolymarketRiskManager(_cfg())
        state = BotState()
        rc = rm.check_circuit_breakers(state, current_pnl=-600)
        assert not rc.passed
        assert state.halted

    def test_circuit_breaker_errors(self):
        rm = PolymarketRiskManager(_cfg())
        state = BotState(errors_today=10)
        rc = rm.check_circuit_breakers(state, current_pnl=0)
        assert not rc.passed
        assert state.halted


# ── Strategies ──

class TestMeanReversionStrategy:
    def test_finds_extreme_low(self):
        strat = MeanReversionStrategy(_cfg())
        markets = [_market(yes_price=0.10, volume=5000, liquidity=5000)]
        opps = strat.evaluate(markets)
        assert len(opps) >= 1
        assert opps[0].outcome == Outcome.YES

    def test_finds_extreme_high(self):
        strat = MeanReversionStrategy(_cfg())
        markets = [_market(yes_price=0.90, volume=5000, liquidity=5000)]
        opps = strat.evaluate(markets)
        assert len(opps) >= 1
        assert opps[0].outcome == Outcome.NO

    def test_skips_normal_price(self):
        strat = MeanReversionStrategy(_cfg())
        markets = [_market(yes_price=0.50, volume=50000, liquidity=10000)]
        opps = strat.evaluate(markets)
        assert len(opps) == 0

    def test_skips_high_vol_liq(self):
        strat = MeanReversionStrategy(_cfg())
        markets = [_market(yes_price=0.10, volume=50000, liquidity=5000)]  # vol/liq = 10 > threshold
        opps = strat.evaluate(markets)
        assert len(opps) == 0


class TestEdgeStrategy:
    def test_finds_edge_with_model(self):
        probs = {"cond1": 0.75}
        strat = EdgeStrategy(_cfg(), model_probs=probs)
        markets = [_market(cid="cond1", yes_price=0.60)]
        opps = strat.evaluate(markets)
        assert len(opps) >= 1
        assert opps[0].edge >= 0.05

    def test_no_edge_without_model(self):
        strat = EdgeStrategy(_cfg())
        markets = [_market()]
        opps = strat.evaluate(markets)
        assert len(opps) == 0


class TestMarketMakerStrategy:
    def test_generates_two_sides(self):
        strat = MarketMakerStrategy(_cfg())
        markets = [_market(yes_price=0.50, liquidity=20000)]
        opps = strat.evaluate(markets)
        assert len(opps) == 2
        outcomes = {o.outcome for o in opps}
        assert Outcome.YES in outcomes
        assert Outcome.NO in outcomes

    def test_skips_extreme_price(self):
        strat = MarketMakerStrategy(_cfg())
        markets = [_market(yes_price=0.10, liquidity=20000)]
        opps = strat.evaluate(markets)
        assert len(opps) == 0
