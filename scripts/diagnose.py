"""Diagnose why strategy is not trading."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config
from src.data.store import DataStore
from src.hl_client.rest import HLRestClient

TOP_COINS = ["BTC", "ETH", "SOL", "DOGE", "ARB", "OP", "AVAX", "MATIC", "SUI"]
ENTRY_THRESHOLD = 0.0001


def main() -> None:
    cfg = load_config()
    client = HLRestClient(cfg)
    store = DataStore(cfg.get("data", {}).get("db_path", "data/trader.db"))

    print("=== Funding Rate Diagnostic ===\n")

    print("1. Current funding rates from DB:")
    above = 0
    for coin in TOP_COINS:
        rate = store.get_latest_funding_rate(coin)
        if rate:
            flag = " <<< ABOVE THRESHOLD" if rate.rate >= ENTRY_THRESHOLD else ""
            if rate.rate >= ENTRY_THRESHOLD:
                above += 1
            print(f"  {coin:6s}: {rate.rate:+.6f} ({rate.rate * 100:.4f}%) @ {rate.timestamp}{flag}")
        else:
            print(f"  {coin:6s}: no data")

    print(f"\n  Coins above entry threshold ({ENTRY_THRESHOLD}): {above}/{len(TOP_COINS)}")

    print("\n2. Liquidity check (top 5 levels):")
    for coin in TOP_COINS:
        try:
            book = client.get_l2_snapshot(coin)
            levels = book.get("levels", [[], []])
            if len(levels) >= 2:
                bid_depth = sum(float(lv["sz"]) * float(lv["px"]) for lv in levels[0][:5])
                ask_depth = sum(float(lv["sz"]) * float(lv["px"]) for lv in levels[1][:5])
                depth = min(bid_depth, ask_depth)
                flag = " OK" if depth >= 50000 else " LOW"
                print(f"  {coin:6s}: ${depth:,.0f}{flag}")
        except Exception as e:
            print(f"  {coin:6s}: error - {e}")

    print("\n3. DB funding rate count:")
    for coin in TOP_COINS:
        rates = store.get_funding_history(coin)
        print(f"  {coin:6s}: {len(rates)} records")

    store.close()


if __name__ == "__main__":
    main()
