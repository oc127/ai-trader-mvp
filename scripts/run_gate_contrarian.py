"""Gate.io funding rate contrarian bot.

Shorts coins with extreme positive funding (overcrowded longs),
longs coins with extreme negative funding (overcrowded shorts).
Directional strategy with stop-loss and take-profit.

Usage:
    python scripts/run_gate_contrarian.py              # paper mode (default)
    python scripts/run_gate_contrarian.py --live        # real trades (requires confirmation)
    python scripts/run_gate_contrarian.py --scan        # just show current signals
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.gate_client.rest import GateClient
from src.gate_client.paper import PaperGateClient
from src.logger import get_logger, setup_logging
from src.monitor.alerts import AlertManager
from src.strategy.gate_funding_contrarian import GateFundingContrarianStrategy

log = get_logger(__name__)


def load_config() -> dict:
    config_path = Path(__file__).parent.parent / "config" / "gate_contrarian.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def scan_signals(client: GateClient, config: dict) -> None:
    strategy = GateFundingContrarianStrategy(client, config)
    signals = strategy.scan_signals()

    print(f"\n{'=' * 75}")
    print(f"  FUNDING RATE CONTRARIAN SIGNALS — {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}")
    print(f"{'=' * 75}")

    cfg = config.get("contrarian", {})
    short_thresh = cfg.get("short_rate_threshold", 0.001)
    long_thresh = cfg.get("long_rate_threshold", -0.0005)

    shorts = [s for s in signals if s["direction"] == "short"]
    longs = [s for s in signals if s["direction"] == "long"]

    if shorts:
        print(f"\n  SHORT signals (funding >= {short_thresh*100:.2f}%/8h → longs overcrowded):")
        print(f"  {'Coin':<10} {'Rate/8h':>10} {'APY':>10} {'24h Vol':>14} {'Price':>12}")
        print("  " + "-" * 60)
        for s in shorts[:10]:
            print(
                f"  {s['coin']:<10} "
                f"{s['rate_8h']*100:>+9.4f}% "
                f"{s['apy']*100:>+9.1f}% "
                f"{s['volume_24h']:>14,.0f} "
                f"${s['mark_price']:>11.4f}"
            )

    if longs:
        print(f"\n  LONG signals (funding <= {long_thresh*100:.3f}%/8h → shorts overcrowded):")
        print(f"  {'Coin':<10} {'Rate/8h':>10} {'APY':>10} {'24h Vol':>14} {'Price':>12}")
        print("  " + "-" * 60)
        for s in longs[:10]:
            print(
                f"  {s['coin']:<10} "
                f"{s['rate_8h']*100:>+9.4f}% "
                f"{s['apy']*100:>+9.1f}% "
                f"{s['volume_24h']:>14,.0f} "
                f"${s['mark_price']:>11.4f}"
            )

    if not shorts and not longs:
        print("\n  No extreme funding signals right now.")

    print(f"\n  Total: {len(shorts)} short signals, {len(longs)} long signals")
    print()


def run_bot(client, config: dict, paper: bool) -> None:
    strategy = GateFundingContrarianStrategy(client, config, paper=paper)
    alerts = AlertManager(config)

    mode = "PAPER" if paper else "LIVE"
    log.info("Funding contrarian bot starting", extra={"mode": mode})
    alerts.send(f"Funding Contrarian started ({mode})")

    running = True
    last_summary = 0.0
    tick_interval = config["contrarian"].get("tick_interval_sec", 30)
    summary_interval = config.get("monitor", {}).get("summary_interval_sec", 1800)

    def handle_signal(signum, frame):
        nonlocal running
        log.info("Shutdown signal received")
        running = False

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    while running:
        try:
            actions = strategy.evaluate()

            for action in actions:
                if action["action"] == "enter":
                    coin = action["coin"]
                    direction = action["direction"]
                    rate = action.get("rate_8h", 0)

                    success = strategy.execute_enter(
                        coin, action["contract"], direction,
                    )
                    if success:
                        alerts.send(
                            f"Opened {direction.upper()} {coin} "
                            f"(rate={rate*100:+.4f}%/8h)"
                        )

                elif action["action"] == "exit":
                    coin = action["coin"]
                    reason = action["reason"]

                    success = strategy.execute_exit(coin, reason)
                    if success:
                        alerts.send(f"Closed {coin}: {reason}")

            now = time.time()
            if now - last_summary >= summary_interval:
                status = strategy.get_status()
                log.info(status)
                alerts.send(status)
                last_summary = now

        except KeyboardInterrupt:
            break
        except Exception:
            log.exception("Error in contrarian main loop")
            time.sleep(30)
            continue

        time.sleep(tick_interval)

    log.info("Shutting down, closing all positions...")
    strategy.close_all()
    alerts.send("Funding Contrarian stopped")


def main() -> None:
    parser = argparse.ArgumentParser(description="Gate.io Funding Contrarian Bot")
    parser.add_argument("--live", action="store_true", help="LIVE trading (real orders, real money)")
    parser.add_argument("--scan", action="store_true", help="Scan and display current signals")
    args = parser.parse_args()

    config = load_config()
    setup_logging(config)

    api_key = os.environ.get("GATE_API_KEY", "")
    api_secret = os.environ.get("GATE_API_SECRET", "")
    if not api_key or not api_secret:
        print("Error: set GATE_API_KEY and GATE_API_SECRET environment variables")
        sys.exit(1)

    client = GateClient(api_key, api_secret)

    if args.scan:
        scan_signals(client, config)
    elif args.live:
        print("\n  *** WARNING: LIVE TRADING MODE ***")
        print("  This bot takes DIRECTIONAL bets with LEVERAGE.")
        print("  You can lose your entire position on a bad trade.")
        confirm = input("  Type 'YES' to confirm: ")
        if confirm.strip() != "YES":
            print("  Aborted.")
            sys.exit(0)
        run_bot(client, config, paper=False)
    else:
        paper_client = PaperGateClient(client)
        run_bot(paper_client, config, paper=True)


if __name__ == "__main__":
    main()
