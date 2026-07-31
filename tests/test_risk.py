from src.hl_client.types import AccountState, OrderRequest, OrderType, Side
from src.risk.manager import RiskManager
from src.strategy.base import Signal


class MockClient:
    def get_all_mids(self):
        return {"ETH": 3000.0, "BTC": 60000.0}


def _make_account(equity=10000, margin_used=1000, available=9000):
    return AccountState(
        equity=equity,
        available_balance=available,
        margin_used=margin_used,
    )


def test_drawdown_halt():
    cfg = {
        "risk": {
            "max_drawdown_pct": 0.05,
            "margin_utilization_warn": 0.7,
            "margin_utilization_halt": 0.85,
            "max_position_size_usd": 10000,
        }
    }
    rm = RiskManager(MockClient(), cfg)

    rm.update(_make_account(equity=10000))
    assert not rm.is_halted

    alerts = rm.update(_make_account(equity=9400))
    assert rm.is_halted
    assert any("HALT" in a for a in alerts)


def test_margin_warning():
    cfg = {
        "risk": {
            "max_drawdown_pct": 0.05,
            "margin_utilization_warn": 0.7,
            "margin_utilization_halt": 0.85,
            "max_position_size_usd": 10000,
        }
    }
    rm = RiskManager(MockClient(), cfg)

    alerts = rm.update(_make_account(equity=10000, margin_used=7500))
    assert any("WARNING" in a for a in alerts)


def test_position_size_check():
    cfg = {
        "risk": {
            "max_drawdown_pct": 0.05,
            "margin_utilization_warn": 0.7,
            "margin_utilization_halt": 0.85,
            "max_position_size_usd": 5000,
        }
    }
    rm = RiskManager(MockClient(), cfg)

    big_signal = Signal(
        coin="ETH",
        action="open",
        orders=[OrderRequest(coin="ETH", side=Side.BUY, size=2.0, order_type=OrderType.MARKET)],
    )
    check = rm.check_signal(big_signal, _make_account())
    assert not check.passed
    assert "notional" in check.reason
