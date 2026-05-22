"""Sell all non-core spot holdings back to USDT.

Usage:
    python scripts/cleanup_coins.py          # dry run, show what would be sold
    python scripts/cleanup_coins.py --sell    # actually sell
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.gate_client.rest import GateClient

KEEP = {"USDT", "ETH", "BTC"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sell", action="store_true", help="Actually sell (default: dry run)")
    args = parser.parse_args()

    client = GateClient(os.environ["GATE_API_KEY"], os.environ["GATE_API_SECRET"])
    balances = client.get_spot_balances()
    tickers = client.get_spot_tickers()

    print(f"\n{'='*60}")
    print(f"  {'DRY RUN' if not args.sell else 'SELLING'} — Cleanup non-core coins")
    print(f"{'='*60}")

    total_recovered = 0
    for coin, amount in sorted(balances.items()):
        if coin in KEEP:
            continue
        price = tickers.get(coin, 0)
        usd_value = amount * price
        if usd_value < 3:
            continue

        pair = f"{coin}_USDT"

        if args.sell:
            try:
                client.spot_market_sell(pair, amount)
                print(f"  SOLD {coin:>10}: {amount:>12.2f} = ${usd_value:>8.2f}")
                total_recovered += usd_value
                time.sleep(0.3)
            except Exception as e:
                print(f"  FAIL {coin:>10}: {amount:>12.2f} = ${usd_value:>8.2f}  ({e})")
        else:
            print(f"  WOULD SELL {coin:>10}: {amount:>12.2f} = ${usd_value:>8.2f}")
            total_recovered += usd_value

    print(f"\n  {'Recovered' if args.sell else 'Would recover'}: ${total_recovered:.2f}")
    print(f"  USDT balance: ${balances.get('USDT', 0):.2f}")
    print(f"  ETH balance: {balances.get('ETH', 0):.4f}")
    print()


if __name__ == "__main__":
    main()
