"""Run backtest on historical convertible bond data."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.backtest.engine import BacktestEngine
from src.config import load_config
from src.data.store import DataStore
from src.logger import get_logger, setup_logging

logger = get_logger(__name__)


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(description="Run CB backtest")
    parser.add_argument("--config-dir", type=Path, default=None)
    parser.add_argument("--start", type=str, default=None, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", type=str, default=None, help="End date YYYY-MM-DD")
    args = parser.parse_args()

    config = load_config(config_dir=args.config_dir)
    db_path = Path(config["data"]["db_path"])
    store = DataStore(db_path)

    start_date = date.fromisoformat(args.start or config["backtest"]["start_date"])
    end_date = date.fromisoformat(args.end or config["backtest"]["end_date"])

    bonds = store.get_all_bond_info()
    if not bonds:
        logger.error("No bond_info in database. Run fetch_data.py first.")
        return

    bond_codes = [b.code for b in bonds]
    stock_codes = {b.code: b.stock_code for b in bonds}

    logger.info("Running backtest: %s to %s, %d bonds", start_date, end_date, len(bond_codes))

    engine = BacktestEngine(config, store)
    result = engine.run(start_date, end_date, bond_codes, stock_codes)

    run_id = store.create_backtest_run(start_date, end_date, config)
    store.update_backtest_run(run_id, result)
    if result.trades:
        store.insert_trades(result.trades, backtest_run_id=run_id)

    print("\n" + "=" * 60)
    print("BACKTEST RESULTS")
    print("=" * 60)
    print(f"Period:            {start_date} to {end_date}")
    print(f"Total Return:      {result.total_return * 100:.2f}%")
    print(f"Annualized Return: {result.annualized_return * 100:.2f}%")
    print(f"Max Drawdown:      {result.max_drawdown * 100:.2f}%")
    print(f"Sharpe Ratio:      {result.sharpe_ratio:.2f}")
    print(f"Win Rate:          {result.win_rate * 100:.1f}%")
    print(f"Total Trades:      {result.total_trades}")
    print(f"Avg Trade PnL:     {result.avg_trade_pnl:.2f} CNY")
    print(f"Run ID:            {run_id}")
    print("=" * 60)


if __name__ == "__main__":
    main()
