"""Gate.io multi-coin funding rate arbitrage bot.

Scans all Gate.io perp contracts for high funding rates,
opens spot+short positions (delta neutral), and rotates
capital to the best opportunities.

Usage:
    python scripts/run_gate_arb.py
    python scripts/run_gate_arb.py --paper     # dry run, no real trades
    python scripts/run_gate_arb.py --scan-only  # just show opportunities
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
from src.logger import get_logger, setup_logging
from src.monitor.alerts import AlertManager
from src.strategy.gate_funding_arb import GateFundingArbStrategy

log = get_logger(__name__)


def load_config() -> dict:
    config_path = Path(__file__).parent.parent / "config" / "gate_arb.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def scan_only(client: GateClient, config: dict) -> None:
    strategy = GateFundingArbStrategy(client, config)
    opportunities = strategy.scan_opportunities()

    print(f"\n{'=' * 70}")
    print(f"  GATE.IO FUNDING ARB OPPORTUNITIES — {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}")
    print(f"{'=' * 70}")
    print(f"  {'Coin':<12} {'Rate/8h':>10} {'APY':>8} {'24h Vol':>14} {'Price':>10}")
    print("  " + "-" * 58)

    for opp in opportunities[:20]:
        apy = opp["rate_8h"] * 3 * 365 * 100
        print(
            f"  {opp['coin']:<12} "
            f"{opp['rate_8h']*100:>+9.4f}% "
            f"{apy:>+7.1f}% "
            f"{opp['volume_24h']:>14,.0f} "
            f"${opp['mark_price']:>9.4f}"
        )

    print(f"\n  Total opportunities: {len(opportunities)}")
    print(f"  Config: min_rate={config['gate_arb']['min_rate_8h']*100:.2f}%/8h, "
          f"min_vol=${config['gate_arb']['min_volume_24h']:,.0f}")
    print()


def run_bot(client: GateClient, config: dict, paper: bool) -> None:
    strategy = GateFundingArbStrategy(client, config, paper=paper)
    alerts = AlertManager(config)

    mode = "PAPER" if paper else "LIVE"
    log.info("Gate funding arb bot starting", extra={"mode": mode})
    alerts.send(f"Gate Funding Arb started ({mode})")

    running = True
    last_summary = 0.0
    tick_interval = config["gate_arb"].get("tick_interval_sec", 60)
    summary_interval = config.get("monitor", {}).get("summary_interval_sec", 3600)

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
                    amount = action.get("amount_usd", 0)
                    log.info("Entering position", extra={"coin": coin, "amount_usd": amount})

                    success = strategy.execute_enter(coin, amount)
                    if success:
                        alerts.send(
                            f"Opened {coin} arb: ${amount:.0f}, "
                            f"rate={action.get('rate_8h', 0)*100:.4f}%/8h"
                        )

                elif action["action"] == "exit":
                    coin = action["coin"]
                    reason = action.get("reason", "")
                    log.info("Exiting position", extra={"coin": coin, "reason": reason})

                    success = strategy.execute_exit(coin)
                    if success:
                        alerts.send(f"Closed {coin} arb: {reason}")

            now = time.time()
            if now - last_summary >= summary_interval:
                summary = strategy.get_position_summary()
                if summary:
                    log.info("Position summary", extra={"summary": summary})
                    alerts.send(summary)
                last_summary = now

        except KeyboardInterrupt:
            break
        except Exception:
            log.exception("Error in gate arb main loop")
            time.sleep(30)
            continue

        time.sleep(tick_interval)

    log.info("Shutting down, closing all positions...")
    strategy.close_all()
    alerts.send("Gate Funding Arb stopped")


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(description="Gate.io Funding Arb Bot")
    parser.add_argument("--paper", action="store_true", help="Paper trading mode (no real orders)")
    parser.add_argument("--scan-only", action="store_true", help="Just scan and display opportunities")
    args = parser.parse_args()

    config = load_config()

    api_key = os.environ.get("GATE_API_KEY", "")
    api_secret = os.environ.get("GATE_API_SECRET", "")
    if not api_key or not api_secret:
        print("Error: set GATE_API_KEY and GATE_API_SECRET environment variables")
        print("  export GATE_API_KEY=your_key")
        print("  export GATE_API_SECRET=your_secret")
        sys.exit(1)

    client = GateClient(api_key, api_secret)

    if args.scan_only:
        scan_only(client, config)
    else:
        run_bot(client, config, paper=args.paper)


if __name__ == "__main__":
    main()
