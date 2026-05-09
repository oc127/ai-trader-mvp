"""Independent watchdog process for crypto trading bots.

Virtu-style Layer 3 risk control: completely independent from the trading system.
Monitors health and kills the trading process if anomalies are detected.

Usage:
    uv run python scripts/watchdog.py --pid <trading_process_pid>
    uv run python scripts/watchdog.py --pidfile data/trader.pid
"""

from __future__ import annotations

import argparse
import os
import signal
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

HEARTBEAT_TIMEOUT_SEC = 120
CHECK_INTERVAL_SEC = 15
MAX_DRAWDOWN_PCT = 0.08
MAX_TRADES_PER_HOUR = 100


def send_telegram(message: str) -> None:
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not (bot_token and chat_id):
        return
    try:
        import httpx

        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        httpx.post(url, json={"chat_id": chat_id, "text": f"🚨 WATCHDOG: {message}"}, timeout=10)
    except Exception:
        pass


def kill_process(pid: int, reason: str) -> None:
    print(f"[WATCHDOG KILL] PID={pid} reason={reason}")
    send_telegram(f"KILLED trading process (PID {pid}): {reason}")
    try:
        os.kill(pid, signal.SIGTERM)
        time.sleep(5)
        if process_alive(pid):
            os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def check_db_health(db_path: str) -> dict:
    result = {"ok": True, "reasons": []}
    if not Path(db_path).exists():
        return result

    try:
        conn = sqlite3.connect(db_path, timeout=5)
        conn.row_factory = sqlite3.Row

        now_utc = datetime.now(timezone.utc)
        one_hour_ago = (now_utc - timedelta(hours=1)).isoformat()

        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM trades WHERE timestamp > ?",
            (one_hour_ago,),
        ).fetchone()
        if row and row["cnt"] > MAX_TRADES_PER_HOUR:
            result["ok"] = False
            result["reasons"].append(f"Trade rate {row['cnt']}/hr > {MAX_TRADES_PER_HOUR}")

        row = conn.execute(
            "SELECT equity FROM pnl_snapshots ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        if row:
            current_equity = row["equity"]
            peak_row = conn.execute(
                "SELECT MAX(equity) as peak FROM pnl_snapshots"
            ).fetchone()
            if peak_row and peak_row["peak"] > 0:
                dd = (peak_row["peak"] - current_equity) / peak_row["peak"]
                if dd > MAX_DRAWDOWN_PCT:
                    result["ok"] = False
                    result["reasons"].append(f"Drawdown {dd:.2%} > {MAX_DRAWDOWN_PCT:.2%}")

        last_snapshot = conn.execute(
            "SELECT timestamp FROM pnl_snapshots ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        if last_snapshot:
            last_ts = datetime.fromisoformat(last_snapshot["timestamp"].replace("Z", "+00:00"))
            if (now_utc - last_ts).total_seconds() > HEARTBEAT_TIMEOUT_SEC * 10:
                result["reasons"].append(f"No PnL snapshot for {(now_utc - last_ts).total_seconds():.0f}s")

        conn.close()
    except Exception as e:
        result["reasons"].append(f"DB check error: {e}")

    return result


def run_watchdog(pid: int, db_path: str) -> None:
    print(f"[WATCHDOG] Monitoring PID={pid}, db={db_path}")
    print(f"[WATCHDOG] Limits: max_dd={MAX_DRAWDOWN_PCT:.0%}, max_trades/hr={MAX_TRADES_PER_HOUR}")
    send_telegram(f"Watchdog started, monitoring PID {pid}")

    while True:
        try:
            if not process_alive(pid):
                print(f"[WATCHDOG] PID {pid} is not running. Exiting.")
                send_telegram(f"Trading process (PID {pid}) is no longer running")
                break

            health = check_db_health(db_path)
            if not health["ok"]:
                reason = "; ".join(health["reasons"])
                kill_process(pid, reason)
                break

            if health["reasons"]:
                for r in health["reasons"]:
                    print(f"[WATCHDOG WARNING] {r}")

        except KeyboardInterrupt:
            print("[WATCHDOG] Stopped by user")
            break
        except Exception as e:
            print(f"[WATCHDOG] Error: {e}")

        time.sleep(CHECK_INTERVAL_SEC)


def main() -> None:
    parser = argparse.ArgumentParser(description="Crypto Trader Watchdog")
    parser.add_argument("--pid", type=int, default=None, help="Trading process PID")
    parser.add_argument("--pidfile", type=str, default=None, help="File containing PID")
    parser.add_argument("--db", type=str, default="data/trader.db", help="Database path")
    args = parser.parse_args()

    pid = args.pid
    if pid is None and args.pidfile:
        pid = int(Path(args.pidfile).read_text().strip())
    if pid is None:
        print("Error: provide --pid or --pidfile")
        sys.exit(1)

    run_watchdog(pid, args.db)


if __name__ == "__main__":
    main()
