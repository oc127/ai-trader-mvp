"""Independent watchdog process for CB T+0 trading.

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
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

CHECK_INTERVAL_SEC = 10
MAX_DAILY_LOSS = 15000
MAX_DAILY_TRADES = 300


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
        time.sleep(3)
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


def _get_china_today() -> str:
    """Get today's date in Asia/Shanghai timezone (UTC+8)."""
    from datetime import timedelta, timezone

    cst = timezone(timedelta(hours=8))
    return datetime.now(cst).strftime("%Y-%m-%d")


def check_db_health(db_path: str) -> dict:
    result = {"ok": True, "reasons": []}
    if not Path(db_path).exists():
        return result

    try:
        conn = sqlite3.connect(db_path, timeout=5)
        conn.row_factory = sqlite3.Row

        today = _get_china_today()

        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM trades WHERE timestamp LIKE ? AND backtest_run_id IS NULL",
            (f"{today}%",),
        ).fetchone()

        if row:
            trade_count = row["cnt"]
            if trade_count > MAX_DAILY_TRADES:
                result["ok"] = False
                result["reasons"].append(f"Trade count {trade_count} > {MAX_DAILY_TRADES}")

        rows = conn.execute(
            "SELECT price, shares, side FROM trades WHERE timestamp LIKE ? AND backtest_run_id IS NULL",
            (f"{today}%",),
        ).fetchall()
        if rows:
            rough_pnl = sum(
                r["price"] * r["shares"] * (1 if r["side"] == "SELL" else -1) for r in rows
            )
            if rough_pnl < -MAX_DAILY_LOSS:
                result["ok"] = False
                result["reasons"].append(f"Estimated daily PnL ¥{rough_pnl:,.0f} < -¥{MAX_DAILY_LOSS:,}")

        conn.close()
    except Exception as e:
        result["reasons"].append(f"DB check error: {e}")

    return result


def run_watchdog(pid: int, db_path: str) -> None:
    print(f"[WATCHDOG] Monitoring PID={pid}, db={db_path}")
    print(f"[WATCHDOG] Limits: max_loss={MAX_DAILY_LOSS}, max_trades={MAX_DAILY_TRADES}")
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

            from datetime import timedelta, timezone

            cst = timezone(timedelta(hours=8))
            now = datetime.now(cst)
            if now.hour == 15 and now.minute >= 5:
                if process_alive(pid):
                    print("[WATCHDOG] Market closed (15:05). Sending SIGTERM.")
                    kill_process(pid, "Market closed, forcing shutdown")
                    break

        except KeyboardInterrupt:
            print("[WATCHDOG] Stopped by user")
            break
        except Exception as e:
            print(f"[WATCHDOG] Error: {e}")

        time.sleep(CHECK_INTERVAL_SEC)


def main() -> None:
    parser = argparse.ArgumentParser(description="CB Trader Watchdog")
    parser.add_argument("--pid", type=int, default=None, help="Trading process PID")
    parser.add_argument("--pidfile", type=str, default=None, help="File containing PID")
    parser.add_argument("--db", type=str, default="data/cb_trader.db", help="Database path")
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
