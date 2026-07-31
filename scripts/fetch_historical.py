"""Fetch historical funding rate data for backtesting."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config
from src.data.collector import FundingCollector
from src.data.store import DataStore
from src.hl_client.rest import HLRestClient
from src.logger import setup_logging

TOP_COINS = [
    "BTC",
    "ETH",
    "SOL",
    "DOGE",
    "ARB",
    "OP",
    "AVAX",
    "SUI",
    "LINK",
    "WIF",
    "PEPE",
    "NEAR",
    "FTM",
    "INJ",
    "TIA",
    "JUP",
    "RENDER",
    "SEI",
    "APT",
    "STRK",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch historical funding rates")
    parser.add_argument("--days", type=int, default=180, help="Days of history to fetch")
    parser.add_argument("--coins", nargs="+", default=TOP_COINS, help="Coins to fetch")
    parser.add_argument("--env", default="testnet")
    args = parser.parse_args()

    cfg = load_config(args.env)
    setup_logging(cfg)

    client = HLRestClient(cfg)
    store = DataStore(cfg.get("data", {}).get("db_path", "data/trader.db"))
    collector = FundingCollector(client, store, args.coins)

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=args.days)

    print(f"Fetching {args.days} days of funding data for {args.coins}...")
    total = collector.backfill(start, end)
    print(f"Done — {total} records saved")

    store.close()


if __name__ == "__main__":
    main()
