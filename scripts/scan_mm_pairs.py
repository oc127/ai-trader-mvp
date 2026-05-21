"""Scan Gate.io spot pairs for market making opportunities.

Finds pairs with wide spreads and moderate volume — ideal for retail MM.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.gate_client.rest import GateClient


def main() -> None:
    client = GateClient(
        os.environ.get("GATE_API_KEY", ""),
        os.environ.get("GATE_API_SECRET", ""),
    )

    tickers = client._request("GET", "/api/v4/spot/tickers")

    candidates = []
    for t in tickers:
        pair = t.get("currency_pair", "")
        if not pair.endswith("_USDT"):
            continue

        last = float(t.get("last", 0) or 0)
        vol_24h = float(t.get("quote_volume", 0) or 0)
        high_bid = float(t.get("highest_bid", 0) or 0)
        low_ask = float(t.get("lowest_ask", 0) or 0)

        if last <= 0 or vol_24h < 100_000:
            continue
        if high_bid <= 0 or low_ask <= 0:
            continue

        mid = (high_bid + low_ask) / 2
        spread_bps = (low_ask - high_bid) / mid * 10000

        if spread_bps < 5:
            continue

        candidates.append({
            "pair": pair,
            "price": last,
            "spread_bps": spread_bps,
            "volume_24h": vol_24h,
            "bid": high_bid,
            "ask": low_ask,
        })

    candidates.sort(key=lambda x: x["spread_bps"], reverse=True)

    print(f"\n{'='*80}")
    print(f"  GATE.IO MARKET MAKING OPPORTUNITIES — Spread >= 5 bps, Vol >= $100K/24h")
    print(f"{'='*80}")
    print(f"  {'Pair':<16} {'Price':>12} {'Spread':>10} {'24h Vol':>14} {'Bid':>12} {'Ask':>12}")
    print("  " + "-" * 76)

    for c in candidates[:30]:
        print(
            f"  {c['pair']:<16} "
            f"${c['price']:>11.6f} "
            f"{c['spread_bps']:>8.1f}bps "
            f"${c['volume_24h']:>13,.0f} "
            f"${c['bid']:>11.6f} "
            f"${c['ask']:>11.6f}"
        )

    print(f"\n  Total candidates: {len(candidates)}")
    print()


if __name__ == "__main__":
    main()
