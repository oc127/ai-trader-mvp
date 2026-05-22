"""Scan Hyperliquid perp pairs for market making opportunities.

Finds pairs with wide spreads and moderate volume — ideal for small-capital MM.

Usage:
    python3 scripts/scan_hl_pairs.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.hl_client.rest import HLRestClient


def main() -> None:
    client = HLRestClient({"exchange": {"use_testnet": False}})
    meta = client.get_meta()
    mids = client.get_all_mids()

    universe = meta.get("universe", [])

    results = []
    for asset in universe:
        coin = asset.get("name", "")
        if coin not in mids:
            continue

        mid = mids[coin]
        if mid <= 0:
            continue

        try:
            book = client.get_l2_snapshot(coin)
        except Exception:
            continue

        bids = book.get("levels", [[]])[0] if book.get("levels") else []
        asks = book.get("levels", [[], []])[1] if len(book.get("levels", [])) > 1 else []

        if not bids or not asks:
            continue

        best_bid = float(bids[0].get("px", 0))
        best_ask = float(asks[0].get("px", 0))

        if best_bid <= 0 or best_ask <= 0:
            continue

        spread_bps = (best_ask - best_bid) / mid * 10000
        bid_depth = sum(float(b.get("px", 0)) * float(b.get("sz", 0)) for b in bids[:5])
        ask_depth = sum(float(a.get("px", 0)) * float(a.get("sz", 0)) for a in asks[:5])

        results.append({
            "coin": coin,
            "mid": mid,
            "spread_bps": spread_bps,
            "bid_depth_5": bid_depth,
            "ask_depth_5": ask_depth,
        })

    results.sort(key=lambda x: x["spread_bps"], reverse=True)

    print(f"\n{'=' * 80}")
    print(f"  HYPERLIQUID MM PAIR SCANNER")
    print(f"{'=' * 80}")
    print(f"  {'Coin':<12} {'Mid Price':>12} {'Spread':>10} {'Bid Depth':>12} {'Ask Depth':>12} {'Score':>8}")
    print("  " + "-" * 70)

    for r in results[:30]:
        total_depth = r["bid_depth_5"] + r["ask_depth_5"]
        score = r["spread_bps"] * min(total_depth, 50000) / 10000
        print(
            f"  {r['coin']:<12} "
            f"${r['mid']:>11.4f} "
            f"{r['spread_bps']:>8.1f}bp "
            f"${r['bid_depth_5']:>10,.0f} "
            f"${r['ask_depth_5']:>10,.0f} "
            f"{score:>7.1f}"
        )

    print(f"\n  Score = spread_bps × min(depth, $50K) / 10000")
    print(f"  High score = wide spread + decent depth = good MM opportunity")
    print(f"  Total pairs scanned: {len(results)}")
    print()


if __name__ == "__main__":
    main()
