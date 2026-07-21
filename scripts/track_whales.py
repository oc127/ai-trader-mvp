"""Track top traders' recent trades and positions.

Usage:
    uv run python scripts/track_whales.py              # show all whale positions
    uv run python scripts/track_whales.py --trades      # show recent trades
    uv run python scripts/track_whales.py --watch       # live watch mode (poll every 60s)
    uv run python scripts/track_whales.py --record      # record trades to data/whale_trades.jsonl
"""

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

DATA_API = "https://data-api.polymarket.com"

WHALES = {
    "0x204f72f35326db932158cba6adff0b9a1da95e14": "swisstony",
}

LOG_DIR = Path(__file__).resolve().parent.parent / "data"


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


def record_trades():
    """Continuously record trades to a JSONL file for later analysis."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / "whale_trades.jsonl"

    known_trades: dict[str, set] = {addr: set() for addr in WHALES}

    # Load already-recorded trade IDs to avoid duplicates on restart
    if log_file.exists():
        with open(log_file) as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    addr = rec.get("trader_addr", "")
                    tid = rec.get("trade_id", "")
                    if addr in known_trades and tid:
                        known_trades[addr].add(tid)
                except json.JSONDecodeError:
                    pass
        print(f"Loaded {sum(len(v) for v in known_trades.values())} known trades from log")

    print(f"Recording trades to {log_file}")
    print("Press Ctrl+C to stop\n")

    cycle = 0
    while True:
        for addr, name in WHALES.items():
            trades = fetch_trades(addr, 30)
            new_count = 0

            for t in trades:
                tid = t.get("transactionHash", t.get("id", t.get("tradeId", "")))
                if not tid or tid in known_trades[addr]:
                    continue

                known_trades[addr].add(tid)
                new_count += 1

                side = t.get("side", "?")
                price = float(t.get("price", 0) or 0)
                size = float(t.get("size", 0) or 0)
                market = t.get("market", t.get("title", "?"))
                if isinstance(market, dict):
                    market = market.get("question", "?")
                outcome = t.get("outcome", t.get("asset", "?"))

                ts_raw = t.get("timestamp", t.get("createdAt", ""))
                if isinstance(ts_raw, (int, float)):
                    ts_str = datetime.fromtimestamp(ts_raw, tz=timezone.utc).isoformat()
                elif isinstance(ts_raw, str):
                    ts_str = ts_raw
                else:
                    ts_str = ""

                record = {
                    "trade_id": tid,
                    "trader_addr": addr,
                    "trader_name": name,
                    "timestamp": ts_str,
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                    "side": side,
                    "price": price,
                    "size": size,
                    "cost_usd": round(price * size, 2),
                    "outcome": str(outcome),
                    "market": str(market),
                    "price_bucket": (
                        "snipe_97+" if price >= 0.97
                        else "snipe_93+" if price >= 0.93
                        else "high_80+" if price >= 0.80
                        else "mid_50+" if price >= 0.50
                        else "low_bet"
                    ),
                    "raw": t,
                }

                with open(log_file, "a") as f:
                    f.write(json.dumps(record) + "\n")

                now = time.strftime("%H:%M:%S")
                bucket = record["price_bucket"]
                print(
                    f"[{now}] {name}: {side} ${size * price:.1f} @ {price:.3f} "
                    f"[{bucket}] — {str(market)[:50]}"
                )

            if new_count == 0 and cycle % 10 == 0:
                now = time.strftime("%H:%M:%S")
                total = len(known_trades[addr])
                print(f"[{now}] {name}: no new trades (total recorded: {total})")

        cycle += 1
        time.sleep(60)


def analyze_log():
    """Print summary stats from recorded trades."""
    log_file = LOG_DIR / "whale_trades.jsonl"
    if not log_file.exists():
        print("No trade log found. Run --record first.")
        return

    trades = []
    with open(log_file) as f:
        for line in f:
            try:
                trades.append(json.loads(line))
            except json.JSONDecodeError:
                pass

    if not trades:
        print("Log is empty.")
        return

    print(f"\n{'='*70}")
    print(f"  Whale Trade Analysis — {len(trades)} trades recorded")
    print(f"{'='*70}")

    for name in set(t["trader_name"] for t in trades):
        tt = [t for t in trades if t["trader_name"] == name]
        print(f"\n  {name}: {len(tt)} trades")

        buckets: dict[str, list] = {}
        for t in tt:
            b = t.get("price_bucket", "unknown")
            buckets.setdefault(b, []).append(t)

        print(f"  {'Bucket':<15} {'Count':>6} {'Total $':>10} {'Avg Price':>10}")
        print(f"  {'─'*45}")
        for bucket in ["snipe_97+", "snipe_93+", "high_80+", "mid_50+", "low_bet"]:
            if bucket not in buckets:
                continue
            bt = buckets[bucket]
            total = sum(t.get("cost_usd", 0) for t in bt)
            avg_price = sum(t.get("price", 0) for t in bt) / len(bt) if bt else 0
            buys = sum(1 for t in bt if t.get("side", "").upper() == "BUY")
            sells = len(bt) - buys
            print(f"  {bucket:<15} {len(bt):>6} ${total:>9,.0f}   {avg_price:.3f}  (B:{buys}/S:{sells})")

        # Time distribution
        hours: dict[int, int] = {}
        for t in tt:
            ts = t.get("timestamp", "")
            if "T" in str(ts):
                try:
                    h = int(str(ts).split("T")[1][:2])
                    hours[h] = hours.get(h, 0) + 1
                except (ValueError, IndexError):
                    pass

        if hours:
            peak_hour = max(hours, key=hours.get)
            print(f"\n  Peak trading hour (UTC): {peak_hour:02d}:00 ({hours[peak_hour]} trades)")
            active_hours = sorted(h for h, c in hours.items() if c >= 2)
            if active_hours:
                print(f"  Active hours: {', '.join(f'{h:02d}' for h in active_hours)}")

        # Top markets
        markets: dict[str, int] = {}
        for t in tt:
            m = str(t.get("market", "?"))[:50]
            markets[m] = markets.get(m, 0) + 1
        top_markets = sorted(markets.items(), key=lambda x: x[1], reverse=True)[:10]
        if top_markets:
            print(f"\n  Top markets:")
            for m, c in top_markets:
                print(f"    {c:>3}x  {m}")


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
    parser.add_argument("--record", action="store_true", help="Record trades to JSONL file")
    parser.add_argument("--analyze", action="store_true", help="Analyze recorded trades")
    parser.add_argument("--addr", type=str, help="Specific address to track")
    args = parser.parse_args()

    if args.record:
        record_trades()
        return

    if args.analyze:
        analyze_log()
        return

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
