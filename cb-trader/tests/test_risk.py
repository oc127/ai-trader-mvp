from __future__ import annotations

from src.risk.manager import RiskManager


def _default_config() -> dict:
    return {
        "risk": {
            "max_daily_loss": 10000,
            "max_daily_loss_pct": 0.005,
            "max_single_loss_pct": 0.01,
            "max_daily_trades": 200,
            "halt_after_consecutive_losses": 3,
            "max_capital_usage": 0.80,
            "max_per_bond": 200000,
        },
        "position": {
            "total_capital": 2_000_000,
            "max_positions": 10,
            "liquidity_limit_pct": 0.01,
        },
    }


class TestPreTradeCheck:
    def test_allows_normal_trade(self) -> None:
        rm = RiskManager(_default_config())
        check = rm.check_pre_trade(current_positions=0, used_capital=0)
        assert check.allowed is True
        assert check.reasons == []

    def test_blocks_after_daily_loss(self) -> None:
        rm = RiskManager(_default_config())
        rm.daily_pnl = -15000
        check = rm.check_pre_trade(current_positions=0, used_capital=0)
        assert check.allowed is False
        assert any("Daily loss limit" in r for r in check.reasons)

    def test_blocks_after_daily_loss_pct(self) -> None:
        rm = RiskManager(_default_config())
        rm.daily_pnl = -12000
        check = rm.check_pre_trade(current_positions=0, used_capital=0)
        assert check.allowed is False

    def test_blocks_max_trades(self) -> None:
        rm = RiskManager(_default_config())
        rm.trade_count = 200
        check = rm.check_pre_trade(current_positions=0, used_capital=0)
        assert check.allowed is False
        assert any("Max trades" in r for r in check.reasons)

    def test_blocks_consecutive_losses(self) -> None:
        rm = RiskManager(_default_config())
        rm.consecutive_losses = 3
        check = rm.check_pre_trade(current_positions=0, used_capital=0)
        assert check.allowed is False

    def test_blocks_max_positions(self) -> None:
        rm = RiskManager(_default_config())
        check = rm.check_pre_trade(current_positions=10, used_capital=0)
        assert check.allowed is False

    def test_blocks_capital_usage(self) -> None:
        rm = RiskManager(_default_config())
        check = rm.check_pre_trade(current_positions=0, used_capital=1_700_000)
        assert check.allowed is False

    def test_blocks_halted(self) -> None:
        rm = RiskManager(_default_config())
        rm.is_halted = True
        check = rm.check_pre_trade(current_positions=0, used_capital=0)
        assert check.allowed is False


class TestOnTradeClose:
    def test_accumulates_pnl(self) -> None:
        rm = RiskManager(_default_config())
        rm.on_trade_close(500)
        rm.on_trade_close(-200)
        assert rm.daily_pnl == 300
        assert rm.trade_count == 2

    def test_tracks_consecutive_losses(self) -> None:
        rm = RiskManager(_default_config())
        rm.on_trade_close(-100)
        rm.on_trade_close(-200)
        assert rm.consecutive_losses == 2
        rm.on_trade_close(50)
        assert rm.consecutive_losses == 0

    def test_halts_on_big_loss(self) -> None:
        rm = RiskManager(_default_config())
        rm.on_trade_close(-11000)
        assert rm.is_halted is True


class TestPositionSizing:
    def test_normal_sizing(self) -> None:
        rm = RiskManager(_default_config())
        shares = rm.calc_position_size(
            signal_strength=1.5, current_price=120.0, daily_volume_cny=100_000_000, used_capital=0
        )
        assert shares > 0
        assert shares % 10 == 0

    def test_stronger_signal_larger_position(self) -> None:
        rm = RiskManager(_default_config())
        small = rm.calc_position_size(
            signal_strength=0.5, current_price=120.0, daily_volume_cny=100_000_000, used_capital=0
        )
        large = rm.calc_position_size(
            signal_strength=2.5, current_price=120.0, daily_volume_cny=100_000_000, used_capital=0
        )
        assert large > small

    def test_zero_when_no_capital(self) -> None:
        rm = RiskManager(_default_config())
        shares = rm.calc_position_size(
            signal_strength=2.0, current_price=120.0, daily_volume_cny=100_000_000, used_capital=1_600_001
        )
        assert shares == 0

    def test_limited_by_liquidity(self) -> None:
        rm = RiskManager(_default_config())
        shares = rm.calc_position_size(
            signal_strength=3.0, current_price=120.0, daily_volume_cny=1_000_000, used_capital=0
        )
        max_amount = 1_000_000 * 0.01
        assert shares * 120.0 <= max_amount + 120 * 10

    def test_zero_price(self) -> None:
        rm = RiskManager(_default_config())
        assert (
            rm.calc_position_size(signal_strength=1.0, current_price=0, daily_volume_cny=100_000_000, used_capital=0)
            == 0
        )


class TestHaltRisk:
    def test_normal(self) -> None:
        rm = RiskManager(_default_config())
        risk = rm.check_halt_risk(current_price=105, open_price=100)
        assert risk.level == "LOW"

    def test_near_20_pct(self) -> None:
        rm = RiskManager(_default_config())
        risk = rm.check_halt_risk(current_price=119, open_price=100)
        assert risk.level == "HIGH"
        assert risk.action == "NO_NEW_POSITIONS"

    def test_near_30_pct(self) -> None:
        rm = RiskManager(_default_config())
        risk = rm.check_halt_risk(current_price=126, open_price=100)
        assert risk.level == "CRITICAL"
        assert risk.action == "CLOSE_IMMEDIATELY"

    def test_drop_near_20_pct(self) -> None:
        rm = RiskManager(_default_config())
        risk = rm.check_halt_risk(current_price=81, open_price=100)
        assert risk.level == "HIGH"

    def test_zero_open(self) -> None:
        rm = RiskManager(_default_config())
        risk = rm.check_halt_risk(current_price=100, open_price=0)
        assert risk.level == "LOW"


class TestShouldCloseAll:
    def test_before_1450(self) -> None:
        rm = RiskManager(_default_config())
        assert rm.should_close_all(14, 49) is False

    def test_at_1450(self) -> None:
        rm = RiskManager(_default_config())
        assert rm.should_close_all(14, 50) is True

    def test_at_1455(self) -> None:
        rm = RiskManager(_default_config())
        assert rm.should_close_all(14, 55) is True


class TestResetDaily:
    def test_resets_all_counters(self) -> None:
        rm = RiskManager(_default_config())
        rm.daily_pnl = -5000
        rm.trade_count = 100
        rm.consecutive_losses = 2
        rm.is_halted = True

        rm.reset_daily()
        assert rm.daily_pnl == 0.0
        assert rm.trade_count == 0
        assert rm.consecutive_losses == 0
        assert rm.is_halted is False
