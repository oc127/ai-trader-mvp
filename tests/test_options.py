"""Tests for the options premium seller module."""

from __future__ import annotations

import time

from src.options.premium_seller import SoldOption, load_seller_config
from src.options.scanner import OptionsScanner, ScanResult, default_scan_config
from src.options.types import (
    Greeks,
    OptionInstrument,
    OptionType,
    SellerState,
    TradeResult,
)


def _make_instrument(
    name: str = "BTC-28JUN26-80000-P",
    underlying: str = "BTC",
    option_type: OptionType = OptionType.PUT,
    strike: float = 80000,
    expiry_ts: int = 0,
    bid: float = 0.002,
    ask: float = 0.003,
    mark_price: float = 0.0025,
    iv: float = 0.55,
    delta: float = -0.12,
    theta: float = -5.0,
    underlying_price: float = 100000,
    open_interest: float = 50,
) -> OptionInstrument:
    if expiry_ts == 0:
        expiry_ts = int(time.time()) + 30 * 86400  # 30 days from now
    return OptionInstrument(
        instrument_name=name,
        underlying=underlying,
        option_type=option_type,
        strike=strike,
        expiry_ts=expiry_ts,
        settlement="delivery",
        min_trade_amount=0.1,
        tick_size=0.0001,
        contract_size=1.0,
        bid=bid,
        ask=ask,
        mark_price=mark_price,
        iv=iv,
        greeks=Greeks(delta=delta, theta=theta),
        open_interest=open_interest,
        underlying_price=underlying_price,
    )


# ── Types ──

class TestOptionInstrument:
    def test_mid_price(self):
        inst = _make_instrument(bid=0.002, ask=0.004)
        assert inst.mid == 0.003

    def test_mid_falls_back_to_mark(self):
        inst = _make_instrument(bid=0, ask=0, mark_price=0.005)
        assert inst.mid == 0.005

    def test_premium_usd(self):
        inst = _make_instrument(bid=0.002, ask=0.004, underlying_price=100000)
        assert inst.premium_usd == 300.0  # 0.003 * 100000

    def test_otm_pct_put(self):
        inst = _make_instrument(option_type=OptionType.PUT, strike=80000, underlying_price=100000)
        assert abs(inst.otm_pct - 0.20) < 0.001

    def test_otm_pct_call(self):
        inst = _make_instrument(option_type=OptionType.CALL, strike=120000, underlying_price=100000)
        assert abs(inst.otm_pct - 0.20) < 0.001

    def test_days_to_expiry(self):
        inst = _make_instrument(expiry_ts=int(time.time()) + 15 * 86400)
        assert 14.9 < inst.days_to_expiry < 15.1

    def test_bid_usd(self):
        inst = _make_instrument(bid=0.001, underlying_price=50000)
        assert inst.bid_usd == 50.0


class TestTradeResult:
    def test_success(self):
        r = TradeResult(success=True, order_id="123", premium_usd=25.0)
        assert r.success
        assert r.premium_usd == 25.0

    def test_failure(self):
        r = TradeResult(success=False, error="insufficient funds")
        assert not r.success
        assert r.error == "insufficient funds"


class TestSellerState:
    def test_defaults(self):
        s = SellerState()
        assert s.cycle_count == 0
        assert s.premium_collected_today == 0.0
        assert not s.halted


# ── Scanner Config ──

class TestScanConfig:
    def test_defaults(self):
        sc = default_scan_config({})
        assert sc.min_delta == 0.05
        assert sc.max_delta == 0.20
        assert sc.min_dte == 7
        assert sc.max_dte == 45
        assert "BTC" in sc.currencies

    def test_custom(self):
        cfg = {"options": {"scanner": {
            "min_delta": 0.10,
            "max_delta": 0.15,
            "currencies": ["ETH"],
        }}}
        sc = default_scan_config(cfg)
        assert sc.min_delta == 0.10
        assert sc.max_delta == 0.15
        assert sc.currencies == ["ETH"]


# ── Scanner Filtering ──

class TestScannerFilters:
    def _scanner(self, **overrides):
        return OptionsScanner.__new__(OptionsScanner)

    def test_passes_good_instrument(self):
        scanner = OptionsScanner.__new__(OptionsScanner)
        scanner._scan_config = default_scan_config({})
        inst = _make_instrument()
        assert scanner._passes_filters(inst)

    def test_rejects_low_delta(self):
        scanner = OptionsScanner.__new__(OptionsScanner)
        scanner._scan_config = default_scan_config({})
        inst = _make_instrument(delta=-0.02)  # too low
        assert not scanner._passes_filters(inst)

    def test_rejects_high_delta(self):
        scanner = OptionsScanner.__new__(OptionsScanner)
        scanner._scan_config = default_scan_config({})
        inst = _make_instrument(delta=-0.35)  # too high (not far enough OTM)
        assert not scanner._passes_filters(inst)

    def test_rejects_low_premium(self):
        scanner = OptionsScanner.__new__(OptionsScanner)
        scanner._scan_config = default_scan_config({})
        inst = _make_instrument(bid=0.00001, ask=0.00002, mark_price=0.000015, underlying_price=100000)
        assert not scanner._passes_filters(inst)

    def test_rejects_wide_spread(self):
        scanner = OptionsScanner.__new__(OptionsScanner)
        scanner._scan_config = default_scan_config({})
        inst = _make_instrument(bid=0.001, ask=0.010)  # very wide
        assert not scanner._passes_filters(inst)

    def test_rejects_low_iv(self):
        scanner = OptionsScanner.__new__(OptionsScanner)
        scanner._scan_config = default_scan_config({})
        inst = _make_instrument(iv=0.15)  # too low
        assert not scanner._passes_filters(inst)

    def test_rejects_no_open_interest(self):
        scanner = OptionsScanner.__new__(OptionsScanner)
        scanner._scan_config = default_scan_config({})
        inst = _make_instrument(open_interest=2)  # too low
        assert not scanner._passes_filters(inst)


# ── Scanner Scoring ──

class TestScannerScoring:
    def test_score_positive(self):
        scanner = OptionsScanner.__new__(OptionsScanner)
        scanner._scan_config = default_scan_config({})
        inst = _make_instrument()
        score = scanner._score(inst)
        assert score > 0

    def test_higher_theta_higher_score(self):
        scanner = OptionsScanner.__new__(OptionsScanner)
        scanner._scan_config = default_scan_config({})
        low_theta = _make_instrument(theta=-2.0)
        high_theta = _make_instrument(theta=-10.0)
        assert scanner._score(high_theta) > scanner._score(low_theta)

    def test_annualized_yield(self):
        scanner = OptionsScanner.__new__(OptionsScanner)
        scanner._scan_config = default_scan_config({})
        inst = _make_instrument(
            option_type=OptionType.PUT,
            strike=80000,
            bid=0.002, ask=0.004,
            underlying_price=100000,
            expiry_ts=int(time.time()) + 30 * 86400,
        )
        ann = scanner._annualized_yield(inst)
        assert ann > 0


# ── Seller Config ──

class TestSellerConfig:
    def test_defaults(self):
        sc = load_seller_config({})
        assert sc.max_capital_usd == 1000.0
        assert sc.take_profit_pct == 0.50
        assert sc.stop_loss_multiplier == 2.0

    def test_custom(self):
        cfg = {"options": {"seller": {
            "max_capital_usd": 5000,
            "max_positions": 10,
        }}}
        sc = load_seller_config(cfg)
        assert sc.max_capital_usd == 5000
        assert sc.max_positions == 10


# ── Format Report ──

class TestFormatReport:
    def test_empty_report(self):
        scanner = OptionsScanner.__new__(OptionsScanner)
        scanner._scan_config = default_scan_config({})
        report = scanner.format_report([])
        assert "No options found" in report

    def test_report_has_content(self):
        scanner = OptionsScanner.__new__(OptionsScanner)
        scanner._scan_config = default_scan_config({})
        inst = _make_instrument()
        results = [ScanResult(instrument=inst, score=42.0, annualized_yield=0.35)]
        report = scanner.format_report(results)
        assert "BTC-28JUN26-80000-P" in report
        assert "Top pick" in report


# ── SoldOption tracking ──

class TestSoldOption:
    def test_creation(self):
        sold = SoldOption(
            instrument_name="BTC-28JUN26-80000-P",
            entry_price=0.0025,
            entry_premium_usd=25.0,
            size=0.1,
            underlying_price_at_entry=100000,
            sold_at=time.monotonic(),
            option_type=OptionType.PUT,
            strike=80000,
        )
        assert sold.entry_premium_usd == 25.0
        assert sold.option_type == OptionType.PUT
