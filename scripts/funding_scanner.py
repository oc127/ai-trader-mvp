"""Cross-exchange funding rate scanner.

Compares BTC/ETH funding rates across Binance, Gate.io, and OKX.
No API keys needed — all public endpoints.

Usage:
    python scripts/funding_scanner.py
    python scripts/funding_scanner.py --coins BTC ETH SOL
    python scripts/funding_scanner.py --loop 300   # refresh every 5 min
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone

import requests

COINS = ["BTC", "ETH", "SOL", "DOGE", "AVAX", "LINK"]
TIMEOUT = 10


def fetch_binance(client: requests.Session, coins: list[str]) -> dict[str, dict]:
    results = {}
    for coin in coins:
        try:
            resp = client.get(
                "https://fapi.binance.com/fapi/v1/premiumIndex",
                params={"symbol": f"{coin}USDT"},
                timeout=TIMEOUT,
            )
            if resp.status_code == 200:
                data = resp.json()
                rate = float(data["lastFundingRate"])
                next_ts = int(data["nextFundingTime"]) / 1000
                next_time = datetime.fromtimestamp(next_ts, tz=timezone.utc)
                results[coin] = {
                    "rate_8h": rate * 100,
                    "annual": rate * 3 * 365 * 100,
                    "next": next_time.strftime("%H:%M UTC"),
                }
        except Exception:
            pass
    return results


def fetch_gate(client: requests.Session, coins: list[str]) -> dict[str, dict]:
    results = {}
    for coin in coins:
        try:
            resp = client.get(
                f"https://api.gateio.ws/api/v4/futures/usdt/contracts/{coin}_USDT",
                timeout=TIMEOUT,
            )
            if resp.status_code == 200:
                data = resp.json()
                rate = float(data.get("funding_rate", 0))
                next_ts = float(data.get("funding_next_apply", 0))
                next_time = datetime.fromtimestamp(next_ts, tz=timezone.utc) if next_ts else None
                results[coin] = {
                    "rate_8h": rate * 100,
                    "annual": rate * 3 * 365 * 100,
                    "next": next_time.strftime("%H:%M UTC") if next_time else "--",
                }
        except Exception:
            pass
    return results


def fetch_okx(client: requests.Session, coins: list[str]) -> dict[str, dict]:
    results = {}
    for coin in coins:
        try:
            resp = client.get(
                "https://www.okx.com/api/v5/public/funding-rate",
                params={"instId": f"{coin}-USDT-SWAP"},
                timeout=TIMEOUT,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("data"):
                    item = data["data"][0]
                    rate = float(item["fundingRate"])
                    next_ts = int(item["nextFundingTime"]) / 1000
                    next_time = datetime.fromtimestamp(next_ts, tz=timezone.utc)
                    results[coin] = {
                        "rate_8h": rate * 100,
                        "annual": rate * 3 * 365 * 100,
                        "next": next_time.strftime("%H:%M UTC"),
                    }
        except Exception:
            pass
    return results


def print_table(coins: list[str], binance: dict, gate: dict, okx: dict) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n{'=' * 72}")
    print(f"  FUNDING RATE SCANNER — {now}")
    print(f"{'=' * 72}")
    print(f"{'Coin':<6} {'Binance':>12} {'Gate.io':>12} {'OKX':>12}  {'Best':>8}  Action")
    print(f"{'':.<6} {'(8h / APY)':>12} {'(8h / APY)':>12} {'(8h / APY)':>12}")
    print("-" * 72)

    for coin in coins:
        b = binance.get(coin)
        g = gate.get(coin)
        o = okx.get(coin)

        b_str = f"{b['rate_8h']:+.4f}%/{b['annual']:+.1f}%" if b else "  --"
        g_str = f"{g['rate_8h']:+.4f}%/{g['annual']:+.1f}%" if g else "  --"
        o_str = f"{o['rate_8h']:+.4f}%/{o['annual']:+.1f}%" if o else "  --"

        rates = {}
        if b:
            rates["Binance"] = b["rate_8h"]
        if g:
            rates["Gate"] = g["rate_8h"]
        if o:
            rates["OKX"] = o["rate_8h"]

        if rates:
            best_exchange = max(rates, key=rates.get)
            best_rate = rates[best_exchange]
            if best_rate > 0:
                action = f"SHORT on {best_exchange}"
            else:
                action = "SKIP (all negative)"
        else:
            best_exchange = "--"
            action = "NO DATA"

        print(f"{coin:<6} {b_str:>12} {g_str:>12} {o_str:>12}  {best_exchange:>8}  {action}")

    print("-" * 72)

    # Settlement times
    sample = binance.get(coins[0]) or gate.get(coins[0]) or okx.get(coins[0])
    if sample:
        parts = []
        if binance:
            parts.append(f"Binance next: {list(binance.values())[0]['next']}")
        if gate:
            parts.append(f"Gate next: {list(gate.values())[0]['next']}")
        if okx:
            parts.append(f"OKX next: {list(okx.values())[0]['next']}")
        print("  " + " | ".join(parts))
    print()

    # Earnings estimate for 20 BTC
    btc_rates = {}
    if "BTC" in binance:
        btc_rates["Binance"] = binance["BTC"]["rate_8h"]
    if "BTC" in gate:
        btc_rates["Gate"] = gate["BTC"]["rate_8h"]
    if "BTC" in okx:
        btc_rates["OKX"] = okx["BTC"]["rate_8h"]

    if btc_rates:
        print("  20 BTC earnings estimate (at current rate):")
        btc_price = 81340  # approximate
        notional = 20 * btc_price
        for ex, rate in sorted(btc_rates.items(), key=lambda x: -x[1]):
            daily = notional * (rate / 100) * 3
            monthly = daily * 30
            sign = "+" if daily >= 0 else ""
            status = "EARNING" if rate > 0 else "PAYING!"
            print(f"    {ex:<10} {sign}${daily:.0f}/day  {sign}${monthly:.0f}/month  [{status}]")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Cross-exchange funding rate scanner")
    parser.add_argument("--coins", nargs="+", default=COINS, help="Coins to scan")
    parser.add_argument("--loop", type=int, default=0, help="Refresh interval in seconds (0=once)")
    args = parser.parse_args()

    coins = [c.upper() for c in args.coins]

    while True:
        client = requests.Session()
        binance = fetch_binance(client, coins)
        gate = fetch_gate(client, coins)
        okx = fetch_okx(client, coins)

        print_table(coins, binance, gate, okx)

        if args.loop <= 0:
            break
        print(f"  Refreshing in {args.loop}s... (Ctrl+C to stop)")
        try:
            time.sleep(args.loop)
        except KeyboardInterrupt:
            print("\nStopped.")
            break


if __name__ == "__main__":
    main()
