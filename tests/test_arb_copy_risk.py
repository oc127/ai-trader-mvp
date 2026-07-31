"""Tests for arbitrage engine, copy trader, and enhanced risk management."""

from __future__ import annotations

from src.polymarket.arbitrage import ArbitrageEngine
from src.polymarket.copy_trader import CopyTrader, TraderProfile
from src.polymarket.risk import PolymarketRiskManager
from src.polymarket.types import BotState, Market, Opportunity, Outcome, Side


def _cfg(**overrides) -> dict:
    base = {
        "polymarket": {
            "paper_mode": True,
            "paper_balance": 10000.0,
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
                "max_monthly_loss_usd": 1500.0,
                "max_drawdown_pct": 0.25,
                "max_total_loss_pct": 0.40,
                "loss_shrink_pct": 0.20,
                "win_grow_pct": 0.10,
                "max_streak_adjustment": 0.50,
            },
            "arbitrage": {},
            "copy_trading": {},
        }
    }
    for k, v in overrides.items():
        base["polymarket"][k] = v
    return base


def _market(
    cid: str = "cond1",
    yes_price: float = 0.60,
    no_price: float = 0.40,
    volume: float = 50000,
    liquidity: float = 10000,
) -> Market:
    return Market(
        condition_id=cid,
        question="Will X happen?",
        slug="will-x-happen",
        yes_token_id="tok_yes",
        no_token_id="tok_no",
        yes_price=yes_price,
        no_price=no_price,
        volume=volume,
        volume_24h=1000,
        liquidity=liquidity,
    )


def _opp(edge: float = 0.10, kelly: float = 0.05, confidence: str = "medium") -> Opportunity:
    m = _market()
    return Opportunity(
        market=m, outcome=Outcome.YES, side=Side.BUY,
        model_prob=m.yes_price + edge, market_prob=m.yes_price,
        edge=edge, ev=edge, kelly_fraction=kelly, confidence=confidence,
    )


# ── Arbitrage Engine ──


class TestArbitrageEngine:
    def test_finds_complete_set_arb(self):
        engine = ArbitrageEngine(_cfg())
        m = _market(yes_price=0.45, no_price=0.50, liquidity=10000, volume=5000)
        opps = engine.scan_arbs([m])
        assert len(opps) == 1
        assert opps[0].arb_type == "complete_set"
        assert abs(opps[0].total_cost - 0.95) < 1e-9
        assert abs(opps[0].gross_profit - 0.05) < 1e-9

    def test_no_arb_when_cost_equals_one(self):
        engine = ArbitrageEngine(_cfg())
        m = _market(yes_price=0.50, no_price=0.50, liquidity=10000, volume=5000)
        opps = engine.scan_arbs([m])
        assert len(opps) == 0

    def test_no_arb_when_cost_exceeds_one(self):
        engine = ArbitrageEngine(_cfg())
        m = _market(yes_price=0.55, no_price=0.50, liquidity=10000, volume=5000)
        opps = engine.scan_arbs([m])
        assert len(opps) == 0

    def test_arb_fee_deducted(self):
        engine = ArbitrageEngine(_cfg())
        m = _market(yes_price=0.45, no_price=0.50, liquidity=10000, volume=5000)
        opps = engine.scan_arbs([m])
        assert len(opps) == 1
        # gross = 0.05, fee = 0.02, net = 0.03
        assert abs(opps[0].net_profit - 0.03) < 0.001

    def test_skips_illiquid_markets(self):
        engine = ArbitrageEngine(_cfg())
        m = _market(yes_price=0.45, no_price=0.50, liquidity=100, volume=5000)
        opps = engine.scan_arbs([m])
        assert len(opps) == 0

    def test_skips_low_volume_markets(self):
        engine = ArbitrageEngine(_cfg())
        m = _market(yes_price=0.45, no_price=0.50, liquidity=10000, volume=100)
        opps = engine.scan_arbs([m])
        assert len(opps) == 0

    def test_skips_below_min_roi(self):
        engine = ArbitrageEngine({
            "polymarket": {"arbitrage": {"min_roi_pct": 50.0}}
        })
        m = _market(yes_price=0.48, no_price=0.50, liquidity=10000, volume=5000)
        opps = engine.scan_arbs([m])
        assert len(opps) == 0

    def test_sorted_by_roi(self):
        engine = ArbitrageEngine(_cfg())
        m1 = _market(cid="c1", yes_price=0.45, no_price=0.50, liquidity=10000, volume=5000)
        m2 = _market(cid="c2", yes_price=0.40, no_price=0.50, liquidity=10000, volume=5000)
        opps = engine.scan_arbs([m1, m2])
        assert len(opps) == 2
        assert opps[0].roi_pct >= opps[1].roi_pct


class TestResolutionSnipe:
    def test_finds_snipe_opportunity(self):
        engine = ArbitrageEngine(_cfg())
        m = _market(yes_price=0.96, no_price=0.04, liquidity=10000, volume=5000)
        opps = engine.scan_snipes([m])
        assert len(opps) >= 1
        assert opps[0].arb_type == "resolution_snipe"
        assert opps[0].snipe_side == "YES"

    def test_no_snipe_below_threshold(self):
        engine = ArbitrageEngine(_cfg())
        m = _market(yes_price=0.80, no_price=0.20, liquidity=10000, volume=5000)
        opps = engine.scan_snipes([m])
        assert len(opps) == 0

    def test_no_snipe_at_exact_dollar(self):
        engine = ArbitrageEngine(_cfg())
        m = _market(yes_price=1.00, no_price=0.00, liquidity=10000, volume=5000)
        opps = engine.scan_snipes([m])
        assert len(opps) == 0

    def test_snipe_net_profit_after_fee(self):
        engine = ArbitrageEngine(_cfg())
        m = _market(yes_price=0.96, no_price=0.04, liquidity=10000, volume=5000)
        opps = engine.scan_snipes([m])
        yes_opps = [o for o in opps if o.snipe_side == "YES"]
        assert len(yes_opps) == 1
        # cost=0.96, gross=0.04, fee=0.02, net=0.02
        assert abs(yes_opps[0].net_profit - 0.02) < 0.001


class TestArbScanAll:
    def test_combines_arbs_and_snipes(self):
        engine = ArbitrageEngine(_cfg())
        m1 = _market(cid="c1", yes_price=0.45, no_price=0.50, liquidity=10000, volume=5000)
        m2 = _market(cid="c2", yes_price=0.96, no_price=0.04, liquidity=10000, volume=5000)
        combined = engine.scan_all([m1, m2])
        types = {o.arb_type for o in combined}
        assert "complete_set" in types
        assert "resolution_snipe" in types

    def test_sorted_by_net_profit(self):
        engine = ArbitrageEngine(_cfg())
        m1 = _market(cid="c1", yes_price=0.45, no_price=0.50, liquidity=10000, volume=5000)
        m2 = _market(cid="c2", yes_price=0.40, no_price=0.50, liquidity=10000, volume=5000)
        combined = engine.scan_all([m1, m2])
        for i in range(len(combined) - 1):
            assert combined[i].net_profit >= combined[i + 1].net_profit

    def test_record_fill_arb(self):
        engine = ArbitrageEngine(_cfg())
        m = _market(yes_price=0.45, no_price=0.50, liquidity=10000, volume=5000)
        opps = engine.scan_arbs([m])
        engine.record_fill(opps[0], filled_size=10.0, fill_price=0.95)
        status = engine.status()
        assert status["arb_count"] == 1
        assert status["arb_pnl"] > 0

    def test_record_fill_snipe(self):
        engine = ArbitrageEngine(_cfg())
        m = _market(yes_price=0.96, no_price=0.04, liquidity=10000, volume=5000)
        opps = engine.scan_snipes([m])
        engine.record_fill(opps[0], filled_size=10.0, fill_price=0.96)
        status = engine.status()
        assert status["snipe_count"] == 1

    def test_reset_daily(self):
        engine = ArbitrageEngine(_cfg())
        m = _market(yes_price=0.45, no_price=0.50, liquidity=10000, volume=5000)
        opps = engine.scan_arbs([m])
        engine.record_fill(opps[0], filled_size=10.0, fill_price=0.95)
        engine.reset_daily()
        status = engine.status()
        assert status["arb_count"] == 0
        assert status["arb_pnl"] == 0


# ── Copy Trader ──


class TestCopyTrader:
    def test_config_defaults(self):
        ct = CopyTrader(_cfg())
        assert ct.config.min_pnl == 500.0
        assert ct.config.min_win_rate == 0.60
        assert ct.config.copy_size_pct == 0.10

    def test_filter_traders_passes_good(self):
        ct = CopyTrader(_cfg())
        # consistency = pnl_per_trade / avg_trade = (5000/100) / (10000/100) = 0.5
        trader = TraderProfile(
            address="0xabc123",
            username="whale",
            pnl=5000.0,
            volume=10000.0,
            win_rate=0.70,
            num_trades=100,
            profit_factor=2.0,
            markets_traded=20,
        )
        result = ct.filter_traders([trader])
        assert len(result) == 1
        assert result[0].score > 0

    def test_filter_traders_rejects_low_pnl(self):
        ct = CopyTrader(_cfg())
        trader = TraderProfile(
            address="0xabc",
            pnl=100.0,
            volume=50000.0,
            win_rate=0.70,
            num_trades=100,
            profit_factor=2.0,
        )
        result = ct.filter_traders([trader])
        assert len(result) == 0

    def test_filter_traders_rejects_low_winrate(self):
        ct = CopyTrader(_cfg())
        trader = TraderProfile(
            address="0xabc",
            pnl=1000.0,
            volume=50000.0,
            win_rate=0.40,
            num_trades=100,
            profit_factor=2.0,
        )
        result = ct.filter_traders([trader])
        assert len(result) == 0

    def test_filter_traders_rejects_low_trades(self):
        ct = CopyTrader(_cfg())
        trader = TraderProfile(
            address="0xabc",
            pnl=1000.0,
            volume=50000.0,
            win_rate=0.70,
            num_trades=5,
            profit_factor=2.0,
        )
        result = ct.filter_traders([trader])
        assert len(result) == 0

    def test_filter_traders_max_followed(self):
        ct = CopyTrader({"polymarket": {"copy_trading": {"max_traders_to_follow": 2}}})
        traders = [
            TraderProfile(
                address=f"0x{i}",
                pnl=5000.0 + i * 100,
                volume=10000.0,
                win_rate=0.70,
                num_trades=100,
                profit_factor=2.0,
            )
            for i in range(5)
        ]
        result = ct.filter_traders(traders)
        assert len(result) == 2

    def test_calculate_copy_size(self):
        ct = CopyTrader(_cfg())
        assert ct.calculate_copy_size(100.0) == 10.0  # 10% of 100
        assert ct.calculate_copy_size(10.0) == 2.0    # min $2
        assert ct.calculate_copy_size(500.0) == 25.0   # max $25

    def test_should_copy_cooldown(self):
        ct = CopyTrader(_cfg())
        assert ct.should_copy("0xabc") is True
        ct.record_copy("0xabc")
        assert ct.should_copy("0xabc") is False

    def test_detect_new_trades(self):
        ct = CopyTrader(_cfg())
        trades1 = [{"id": "t1", "side": "BUY"}, {"id": "t2", "side": "SELL"}]
        new = ct.detect_new_trades("0xabc", trades1)
        assert len(new) == 2

        trades2 = [{"id": "t1", "side": "BUY"}, {"id": "t2", "side": "SELL"}, {"id": "t3", "side": "BUY"}]
        new = ct.detect_new_trades("0xabc", trades2)
        assert len(new) == 1
        assert new[0]["id"] == "t3"

    def test_update_followed(self):
        ct = CopyTrader(_cfg())
        traders = [TraderProfile(address="0xa"), TraderProfile(address="0xb")]
        ct.update_followed(traders)
        status = ct.status()
        assert status["followed_traders"] == 2

    def test_record_copy_increments(self):
        ct = CopyTrader(_cfg())
        ct.record_copy("0xabc", pnl=0.5)
        ct.record_copy("0xdef", pnl=-0.1)
        status = ct.status()
        assert status["copy_count"] == 2
        assert abs(status["copy_pnl"] - 0.4) < 0.001

    def test_reset_daily(self):
        ct = CopyTrader(_cfg())
        ct.record_copy("0xabc", pnl=1.0)
        ct.reset_daily()
        status = ct.status()
        assert status["copy_count"] == 0
        assert status["copy_pnl"] == 0.0


# ── Enhanced Risk Manager (multi-layer + dynamic sizing) ──


class TestMultiLayerCircuitBreakers:
    def test_layer1_daily_loss(self):
        rm = PolymarketRiskManager(_cfg())
        state = BotState()
        rc = rm.check_circuit_breakers(state, current_pnl=-600)
        assert not rc.passed
        assert state.halted
        assert "Daily" in state.halt_reason

    def test_layer2_monthly_loss(self):
        rm = PolymarketRiskManager(_cfg())
        rm._monthly_pnl = -1600.0
        state = BotState()
        rc = rm.check_circuit_breakers(state, current_pnl=0)
        assert not rc.passed
        assert state.halted
        assert "Monthly" in state.halt_reason

    def test_layer3_drawdown(self):
        rm = PolymarketRiskManager(_cfg())
        rm.set_initial_capital(10000)
        rm.update_equity(10000)
        rm.update_equity(7000)  # 30% drawdown > 25% limit
        state = BotState()
        rc = rm.check_circuit_breakers(state, current_pnl=0)
        assert not rc.passed
        assert state.halted
        assert "Drawdown" in state.halt_reason

    def test_layer3_drawdown_ok_within_limit(self):
        rm = PolymarketRiskManager(_cfg())
        rm.set_initial_capital(10000)
        rm.update_equity(10000)
        rm.update_equity(8000)  # 20% drawdown < 25% limit
        state = BotState()
        rc = rm.check_circuit_breakers(state, current_pnl=0)
        assert rc.passed

    def test_layer4_total_loss(self):
        rm = PolymarketRiskManager(_cfg())
        rm.set_initial_capital(10000)
        rm._total_pnl = -4500.0  # 45% of capital > 40% limit
        state = BotState()
        rc = rm.check_circuit_breakers(state, current_pnl=0)
        assert not rc.passed
        assert state.halted
        assert "Total" in state.halt_reason

    def test_error_count_breaker(self):
        rm = PolymarketRiskManager(_cfg())
        state = BotState(errors_today=10)
        rc = rm.check_circuit_breakers(state, current_pnl=0)
        assert not rc.passed
        assert state.halted

    def test_all_layers_pass(self):
        rm = PolymarketRiskManager(_cfg())
        rm.set_initial_capital(10000)
        rm.update_equity(10000)
        state = BotState()
        rc = rm.check_circuit_breakers(state, current_pnl=0)
        assert rc.passed
        assert not state.halted


class TestDynamicPositionSizing:
    def test_loss_streak_shrinks_size(self):
        rm = PolymarketRiskManager(_cfg())
        base_size = rm.size_position(_opp(kelly=0.08), balance=10000, current_exposure=0)
        rm.record_trade_result(-10.0)
        rm.record_trade_result(-10.0)
        rm.record_trade_result(-10.0)
        shrunk_size = rm.size_position(_opp(kelly=0.08), balance=10000, current_exposure=0)
        assert shrunk_size < base_size

    def test_win_streak_grows_size(self):
        rm = PolymarketRiskManager(_cfg())
        rm.record_trade_result(-10.0)
        rm.record_trade_result(-10.0)
        shrunk_size = rm.size_position(_opp(kelly=0.08), balance=10000, current_exposure=0)
        rm.record_trade_result(10.0)
        rm.record_trade_result(10.0)
        rm.record_trade_result(10.0)
        grown_size = rm.size_position(_opp(kelly=0.08), balance=10000, current_exposure=0)
        assert grown_size > shrunk_size

    def test_win_resets_loss_streak(self):
        rm = PolymarketRiskManager(_cfg())
        rm.record_trade_result(-10.0)
        rm.record_trade_result(-10.0)
        assert rm._consecutive_losses == 2
        rm.record_trade_result(5.0)
        assert rm._consecutive_losses == 0
        assert rm._consecutive_wins == 1

    def test_loss_resets_win_streak(self):
        rm = PolymarketRiskManager(_cfg())
        rm.record_trade_result(10.0)
        rm.record_trade_result(10.0)
        assert rm._consecutive_wins == 2
        rm.record_trade_result(-5.0)
        assert rm._consecutive_wins == 0
        assert rm._consecutive_losses == 1

    def test_size_multiplier_floor(self):
        rm = PolymarketRiskManager(_cfg())
        for _ in range(20):
            rm.record_trade_result(-10.0)
        assert rm.size_multiplier >= 0.50

    def test_size_multiplier_ceiling(self):
        rm = PolymarketRiskManager(_cfg())
        for _ in range(20):
            rm.record_trade_result(10.0)
        assert rm.size_multiplier <= 2.0

    def test_monthly_pnl_accumulates(self):
        rm = PolymarketRiskManager(_cfg())
        rm.record_trade_result(100.0)
        rm.record_trade_result(-50.0)
        assert rm._monthly_pnl == 50.0
        assert rm._total_pnl == 50.0

    def test_reset_monthly(self):
        rm = PolymarketRiskManager(_cfg())
        rm.record_trade_result(100.0)
        rm.reset_monthly()
        assert rm._monthly_pnl == 0.0
        assert rm._total_pnl == 100.0  # total persists

    def test_status_output(self):
        rm = PolymarketRiskManager(_cfg())
        rm.set_initial_capital(10000)
        rm.update_equity(9500)
        rm.record_trade_result(-500.0)
        rm.record_trade_result(100.0)
        status = rm.status()
        assert "monthly_pnl" in status
        assert "drawdown_pct" in status
        assert "size_multiplier" in status
        assert status["consecutive_wins"] == 1

    def test_peak_equity_tracks_high_water(self):
        rm = PolymarketRiskManager(_cfg())
        rm.set_initial_capital(10000)
        rm.update_equity(10000)
        rm.update_equity(11000)
        rm.update_equity(10500)
        assert rm._peak_equity == 11000
