"""Gate.io spot market maker bot.

Places tiered limit orders on both sides of the book,
manages inventory with skew, and widens spread on volatility.

Usage:
    python scripts/run_gate_mm.py
    python scripts/run_gate_mm.py --paper     # dry run, log only
    python scripts/run_gate_mm.py --status     # show current state
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
from src.strategy.gate_market_maker import GateMarketMaker

log = get_logger(__name__)


def load_config() -> dict:
    config_path = Path(__file__).parent.parent / "config" / "gate_mm.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


class PaperGateClient:
    """Wraps GateClient, intercepts write calls for paper trading."""

    def __init__(self, real_client: GateClient) -> None:
        self._client = real_client
        self._next_id = 1000
        self._open_orders: dict[str, dict] = {}

    def get_order_book(self, pair: str, limit: int = 20) -> dict:
        return self._client.get_order_book(pair, limit)

    def get_spot_balances(self) -> dict[str, float]:
        return self._client.get_spot_balances()

    def spot_limit_buy(self, pair: str, price: float, amount: float) -> dict:
        oid = str(self._next_id)
        self._next_id += 1
        self._open_orders[oid] = {
            "id": oid, "side": "buy", "pair": pair,
            "price": price, "amount": amount,
        }
        log.info("[PAPER] Limit BUY %s %.4f @ %.6f", pair, amount, price)
        return {"id": oid}

    def spot_limit_sell(self, pair: str, price: float, amount: float) -> dict:
        oid = str(self._next_id)
        self._next_id += 1
        self._open_orders[oid] = {
            "id": oid, "side": "sell", "pair": pair,
            "price": price, "amount": amount,
        }
        log.info("[PAPER] Limit SELL %s %.4f @ %.6f", pair, amount, price)
        return {"id": oid}

    def cancel_order(self, pair: str, order_id: str) -> dict:
        self._open_orders.pop(order_id, None)
        return {}

    def cancel_all_orders(self, pair: str) -> list:
        to_remove = [k for k, v in self._open_orders.items() if v["pair"] == pair]
        for k in to_remove:
            del self._open_orders[k]
        log.info("[PAPER] Cancelled %d orders for %s", len(to_remove), pair)
        return []

    def list_open_orders(self, pair: str) -> list:
        return [v for v in self._open_orders.values() if v["pair"] == pair]

    def get_spot_trades(self, pair: str, limit: int = 50) -> list:
        return self._client.get_spot_trades(pair, limit)

    def get_my_trades(self, pair: str, limit: int = 50) -> list:
        return []


def run_bot(client, config: dict, paper: bool) -> None:
    mm = GateMarketMaker(client, config)
    alerts = AlertManager(config)

    mode = "PAPER" if paper else "LIVE"
    log.info("Gate.io Market Maker starting", extra={"mode": mode, "pairs": mm.pairs})
    alerts.send(f"Gate MM started ({mode}) — pairs: {', '.join(mm.pairs)}")

    if not paper:
        mm.sync_balances()
        mm.seed_inventory()

    running = True
    last_summary = 0.0
    summary_interval = config.get("monitor", {}).get("summary_interval_sec", 1800)
    error_count = 0
    max_errors = config.get("risk", {}).get("halt_on_error_count", 5)

    def handle_signal(signum, frame):
        nonlocal running
        log.info("Shutdown signal received")
        running = False

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    while running:
        for pair in mm.pairs:
            try:
                result = mm.tick(pair)
                if result["action"] == "refreshed":
                    log.info(
                        "Tick %s: mid=$%.6f spread=%.1f/%.1f bps inv=$%.2f quotes=%d",
                        pair, result["mid"], result["spread_bps"],
                        result["market_spread_bps"], result["inventory_usd"],
                        result["n_quotes"],
                    )
                    error_count = 0
                elif result["action"] == "error":
                    error_count += 1
                    if error_count >= max_errors:
                        log.warning("Too many errors (%d), pausing 60s", error_count)
                        alerts.send(f"MM paused: {error_count} consecutive errors")
                        time.sleep(60)
                        error_count = 0
            except Exception:
                log.exception("Error in MM tick for %s", pair)
                error_count += 1

        now = time.time()
        if now - last_summary >= summary_interval:
            summary = mm.get_summary()
            log.info(summary)
            alerts.send(summary)
            last_summary = now

        time.sleep(1)

    log.info("Shutting down, cancelling all orders...")
    mm.cancel_all()
    alerts.send("Gate MM stopped — all orders cancelled")


def show_status(client: GateClient, config: dict) -> None:
    mm = GateMarketMaker(client, config)
    for pair in mm.pairs:
        try:
            book = client.get_order_book(pair, limit=5)
            bids = book.get("bids", [])
            asks = book.get("asks", [])

            if bids and asks:
                best_bid = float(bids[0][0])
                best_ask = float(asks[0][0])
                mid = (best_bid + best_ask) / 2
                spread_bps = (best_ask - best_bid) / mid * 10000

                print(f"\n{pair}:")
                print(f"  Best bid: ${best_bid:.6f}")
                print(f"  Best ask: ${best_ask:.6f}")
                print(f"  Mid:      ${mid:.6f}")
                print(f"  Spread:   {spread_bps:.1f} bps ({(best_ask-best_bid)/mid*100:.4f}%)")

                bid_depth = sum(float(b[0]) * float(b[1]) for b in bids[:5])
                ask_depth = sum(float(a[0]) * float(a[1]) for a in asks[:5])
                print(f"  Bid depth (5): ${bid_depth:,.0f}")
                print(f"  Ask depth (5): ${ask_depth:,.0f}")

            open_orders = client.list_open_orders(pair)
            print(f"  Open orders: {len(open_orders)}")

        except Exception as e:
            print(f"\n{pair}: Error — {e}")

    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Gate.io Market Maker Bot")
    parser.add_argument("--paper", action="store_true", help="Paper trading mode")
    parser.add_argument("--status", action="store_true", help="Show orderbook status")
    args = parser.parse_args()

    config = load_config()
    setup_logging(config)

    api_key = os.environ.get("GATE_API_KEY", "")
    api_secret = os.environ.get("GATE_API_SECRET", "")
    if not api_key or not api_secret:
        print("Error: set GATE_API_KEY and GATE_API_SECRET environment variables")
        sys.exit(1)

    client = GateClient(api_key, api_secret)

    if args.status:
        show_status(client, config)
    elif args.paper:
        paper_client = PaperGateClient(client)
        run_bot(paper_client, config, paper=True)
    else:
        run_bot(client, config, paper=False)


if __name__ == "__main__":
    main()
