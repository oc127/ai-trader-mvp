from datetime import datetime, timedelta, timezone

from src.backtest.engine import BacktestConfig, run_backtest
from src.backtest.metrics import compute_metrics
from src.hl_client.types import FundingRate


def test_compute_metrics_basic():
    curve = [10000, 10100, 10200, 10150, 10300]
    m = compute_metrics(curve, 300, 4, [24, 48, 12, 36])
    assert m.total_return > 0
    assert m.max_drawdown > 0
    assert m.sharpe_ratio != 0
    assert m.avg_holding_hours == 30.0


def test_backtest_no_data():
    result = run_backtest({}, {})
    assert result.metrics.total_trades == 0


def test_backtest_with_funding():
    base = datetime(2025, 1, 1, tzinfo=timezone.utc)
    rates = []
    prices = {}
    for i in range(100):
        ts = base + timedelta(hours=i)
        rates.append(FundingRate(coin="ETH", rate=0.0002, timestamp=ts))
        prices[ts.isoformat()] = 3000.0

    funding_data = {"ETH": rates}
    price_data = {"ETH": prices}
    config = BacktestConfig(
        initial_capital=10000, entry_rate_threshold=0.0001, exit_rate_threshold=0.00003
    )

    result = run_backtest(funding_data, price_data, config)
    assert result.metrics.total_funding_earned > 0
    assert len(result.equity_curve) > 1
