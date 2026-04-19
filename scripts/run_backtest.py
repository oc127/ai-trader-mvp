"""Run backtest on historical funding rate data."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.backtest.engine import BacktestConfig, run_backtest
from src.config import load_config
from src.data.store import DataStore
from src.logger import setup_logging


def main() -> None:
    parser = argparse.ArgumentParser(description="Run funding arb backtest")
    parser.add_argument("--capital", type=float, default=10000, help="Initial capital")
    parser.add_argument("--entry-threshold", type=float, default=0.0001)
    parser.add_argument("--exit-threshold", type=float, default=0.00003)
    parser.add_argument("--coins", nargs="+", default=["BTC", "ETH", "SOL"])
    parser.add_argument("--env", default="testnet")
    args = parser.parse_args()

    cfg = load_config(args.env)
    setup_logging(cfg)

    store = DataStore(cfg.get("data", {}).get("db_path", "data/trader.db"))

    funding_data = {}
    price_data = {}
    for coin in args.coins:
        rates = store.get_funding_history(coin)
        if rates:
            funding_data[coin] = rates
            price_data[coin] = {}
            for r in rates:
                price_data[coin][r.timestamp.isoformat()] = 0

    if not funding_data:
        print("No funding data found. Run fetch_historical.py first.")
        store.close()
        return

    bt_config = BacktestConfig(
        initial_capital=args.capital,
        entry_rate_threshold=args.entry_threshold,
        exit_rate_threshold=args.exit_threshold,
    )

    result = run_backtest(funding_data, price_data, bt_config)
    print(result.metrics.summary())
    print(f"Total trades: {len(result.trades)}")

    store.close()


if __name__ == "__main__":
    main()
