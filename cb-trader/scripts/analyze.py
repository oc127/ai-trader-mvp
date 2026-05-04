"""Analyze backtest results from the database."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import load_config
from src.data.store import DataStore
from src.logger import setup_logging
from src.models import Side


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(description="Analyze backtest results")
    parser.add_argument("--config-dir", type=Path, default=None)
    parser.add_argument("--run-id", type=int, required=True, help="Backtest run ID to analyze")
    args = parser.parse_args()

    config = load_config(config_dir=args.config_dir)
    db_path = Path(config["data"]["db_path"])
    store = DataStore(db_path)

    trades = store.get_trades(args.run_id)
    if not trades:
        print(f"No trades found for run_id={args.run_id}")
        return

    buy_trades = [t for t in trades if t.side == Side.BUY]
    sell_trades = [t for t in trades if t.side == Side.SELL]

    print(f"\n{'=' * 60}")
    print(f"TRADE ANALYSIS — Run #{args.run_id}")
    print(f"{'=' * 60}")
    print(f"Total Trades:  {len(trades)}")
    print(f"  Buys:        {len(buy_trades)}")
    print(f"  Sells:       {len(sell_trades)}")

    if buy_trades:
        avg_buy = np.mean([t.price for t in buy_trades])
        avg_buy_size = np.mean([t.shares for t in buy_trades])
        print(f"  Avg Buy Price:  {avg_buy:.2f}")
        print(f"  Avg Buy Size:   {avg_buy_size:.0f} shares")

    if sell_trades:
        avg_sell = np.mean([t.price for t in sell_trades])
        print(f"  Avg Sell Price: {avg_sell:.2f}")

    total_cost = sum(t.cost for t in trades)
    print(f"  Total Cost:  {total_cost:.2f} CNY")

    codes = set(t.code for t in trades)
    print(f"\nBonds Traded: {len(codes)}")

    print(f"\n{'=' * 60}")
    print("PER-BOND BREAKDOWN")
    print(f"{'=' * 60}")

    for code in sorted(codes):
        code_trades = [t for t in trades if t.code == code]
        code_buys = [t for t in code_trades if t.side == Side.BUY]
        code_sells = [t for t in code_trades if t.side == Side.SELL]
        code_cost = sum(t.cost for t in code_trades)
        print(f"  {code}: {len(code_buys)} buys, {len(code_sells)} sells, cost={code_cost:.2f}")

    if trades:
        first_trade = min(t.timestamp for t in trades)
        last_trade = max(t.timestamp for t in trades)
        print(f"\nFirst Trade: {first_trade}")
        print(f"Last Trade:  {last_trade}")

    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
