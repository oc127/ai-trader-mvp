"""Track top traders' recent trades and positions.

Usage:
    uv run python scripts/track_whales.py              # show all whale positions
    uv run python scripts/track_whales.py --trades      # show recent trades
    uv run python scripts/track_whales.py --watch       # live watch mode (poll every 60s)
"""

import argparse
import json
import time
import requests

DATA_API = "https://data-api.polymarket.com"

WHALES = {
    "0x204f72f35326db932158cba6adff0b9a1da95e14": "swisstony",
}


def fetch_positions(addr: str) -> list[dict]:
    try:
        resp = requests.get(
            f"{DATA_API}/positions",
            params={"user": addr, "sizeThreshold": "0.1", "limit": "100"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"  Error fetching positions: {e}")
        return []


def fetch_trades(addr: str, limit: int = 20) -> list[dict]:
    try:
        resp = requests.get(
            f"{DATA_API}/trades",
            params={"user": addr, "limit": str(limit)},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"  Error fetching trades: {e}")
        return []


def fetch_profile(addr: str) -> dict:
    try:
        resp = requests.get(f"{DATA_API}/profile/{addr}", timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return {}


def show_positions(addr: str, name: str):
    print(f"\n{'='*70}")
    print(f"  {name} — {addr[:16]}...")
    print(f"{'='*70}")

    profile = fetch_profile(addr)
    if profile:
        pnl = float(profile.get("pnl", 0) or 0)
        vol = float(profile.get("volume", profile.get("vol", 0)) or 0)
        print(f"  PnL: ${pnl:,.2f} | Volume: ${vol:,.0f}")

    positions = fetch_positions(addr)
    if not positions:
        print("  No positions found")
        return

    # Sort by value
    for p in positions:
        p["_value"] = float(p.get("size", 0) or 0) * float(p.get("curPrice", 0) or 0)
    positions.sort(key=lambda p: p["_value"], reverse=True)

    total_value = sum(p["_value"] for p in positions)
    print(f"  Positions: {len(positions)} | Total value: ${total_value:,.0f}")
    print(f"{'─'*70}")
    print(f"  {'Market':<40} {'Side':<8} {'Avg':>6} {'Cur':>6} {'Value':>10} {'PnL':>10}")
    print(f"{'─'*70}")

    for p in positions[:30]:
        title = str(p.get("title", p.get("market", {}).get("question", "?")))[:38]
        outcome = p.get("outcome", "?")
        size = float(p.get("size", 0) or 0)
        avg = float(p.get("avgPrice", 0) or 0)
        cur = float(p.get("curPrice", 0) or 0)
        val = size * cur
        pnl = (cur - avg) * size if avg > 0 else 0
        pnl_pct = ((cur - avg) / avg * 100) if avg > 0 else 0

        pnl_str = f"${pnl:+,.0f}" if abs(pnl) >= 1 else f"${pnl:+.2f}"
        print(f"  {title:<40} {outcome:<8} {avg:.3f}  {cur:.3f}  ${val:>8,.0f}  {pnl_str:>8} ({pnl_pct:+.1f}%)")

    if len(positions) > 30:
        print(f"  ... and {len(positions) - 30} more positions")


def show_trades(addr: str, name: str, limit: int = 20):
    print(f"\n{'='*70}")
    print(f"  Recent trades: {name}")
    print(f"{'='*70}")

    trades = fetch_trades(addr, limit)
    if not trades:
        print("  No trades found")
        return

    print(f"  {'Time':<20} {'Side':<5} {'Market':<30} {'Price':>6} {'Size':>8}")
    print(f"{'─'*70}")

    for t in trades:
        ts = t.get("timestamp", t.get("createdAt", "?"))
        if isinstance(ts, (int, float)):
            from datetime import datetime, timezone
            ts = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%m-%d %H:%M:%S")
        elif isinstance(ts, str) and len(ts) > 19:
            ts = ts[5:19]

        side = t.get("side", "?")
        market = t.get("market", t.get("title", "?"))
        if isinstance(market, dict):
            market = market.get("question", "?")
        market = str(market)[:28]
        price = float(t.get("price", 0) or 0)
        size = float(t.get("size", 0) or 0)
        cost = price * size

        print(f"  {ts:<20} {side:<5} {market:<30} {price:.3f}  ${cost:>7,.1f}")


def watch_mode():
    """Poll for new trades every 60 seconds."""
    known_trades: dict[str, set] = {addr: set() for addr in WHALES}
    print("Watching whales for new trades... (Ctrl+C to stop)\n")

    while True:
        for addr, name in WHALES.items():
            trades = fetch_trades(addr, 10)
            for t in trades:
                tid = t.get("transactionHash", t.get("id", t.get("tradeId", "")))
                if tid and tid not in known_trades[addr]:
                    known_trades[addr].add(tid)
                    side = t.get("side", "?")
                    price = float(t.get("price", 0) or 0)
                    size = float(t.get("size", 0) or 0)
                    market = t.get("market", t.get("title", "?"))
                    if isinstance(market, dict):
                        market = market.get("question", "?")
                    ts = time.strftime("%H:%M:%S")
                    print(f"[{ts}] {name}: {side} ${size * price:.1f} @ {price:.3f} — {str(market)[:50]}")

        time.sleep(60)


def main():
    parser = argparse.ArgumentParser(description="Track top Polymarket traders")
    parser.add_argument("--trades", action="store_true", help="Show recent trades")
    parser.add_argument("--watch", action="store_true", help="Live watch mode")
    parser.add_argument("--addr", type=str, help="Specific address to track")
    args = parser.parse_args()

    if args.watch:
        watch_mode()
        return

    targets = WHALES
    if args.addr:
        targets = {args.addr: args.addr[:12]}

    for addr, name in targets.items():
        if args.trades:
            show_trades(addr, name)
        else:
            show_positions(addr, name)


if __name__ == "__main__":
    main()
