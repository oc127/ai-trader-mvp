"""Hyperliquid perpetual market maker bot.

Places tiered limit orders on both sides, manages inventory with skew,
and adapts to volatility. Designed for small capital on less liquid pairs.

Usage:
    python3 scripts/run_hl_mm.py               # paper mode (default)
    python3 scripts/run_hl_mm.py --live         # real trades (requires confirmation)
    python3 scripts/run_hl_mm.py --status       # show current orderbook state
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

from src.hl_client.rest import HLRestClient
from src.hl_client.types import OrderRequest, OrderType, Side
from src.logger import get_logger, setup_logging
from src.monitor.alerts import AlertManager

log = get_logger(__name__)


def load_config() -> dict:
    config_path = Path(__file__).parent.parent / "config" / "hl_mm.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


class HLMMAgent:
    """Self-contained HL market making agent."""

    def __init__(self, client: HLRestClient, config: dict, paper: bool = True) -> None:
        self._client = client
        self._paper = paper
        self._tag = "[PAPER] " if paper else ""

        cfg = config.get("hl_mm", {})
        self._coins: list[str] = cfg.get("coins", ["PURR", "HFUN"])
        self._spread_bps: float = cfg.get("spread_bps", 15)
        self._num_tiers: int = cfg.get("num_tiers", 3)
        self._tier_spacing_bps: float = cfg.get("tier_spacing_bps", 5)
        self._tier_multiplier: float = cfg.get("tier_size_multiplier", 1.5)
        self._base_order_usd: float = cfg.get("base_order_usd", 10)
        self._max_inv_usd: float = cfg.get("max_inventory_usd", 200)
        self._max_total_usd: float = cfg.get("max_total_exposure_usd", 500)
        self._skew_intensity: float = cfg.get("skew_intensity", 1.0)
        self._vol_pause_pct: float = cfg.get("volatility_pause_pct", 0.02)
        self._vol_widen: float = cfg.get("volatility_widen_multiplier", 2.5)
        self._vol_cooldown: float = cfg.get("volatility_cooldown_sec", 60)
        self._tick_interval: float = cfg.get("tick_interval_sec", 5)

        risk = config.get("risk", {})
        self._max_daily_loss: float = risk.get("max_daily_loss_usd", 20)

        self._inventory: dict[str, float] = {c: 0.0 for c in self._coins}
        self._prev_mids: dict[str, float] = {}
        self._vol_pause_until: dict[str, float] = {}
        self._open_oids: dict[str, list[int]] = {c: [] for c in self._coins}
        self._daily_pnl = 0.0
        self._day_key = _today()
        self._halted = False
        self._fills_count = 0

    @property
    def coins(self) -> list[str]:
        return list(self._coins)

    def tick(self, coin: str) -> dict:
        if self._halted:
            return {"action": "halted", "coin": coin}

        self._check_day_reset()

        try:
            book = self._client.get_l2_snapshot(coin)
        except Exception:
            log.warning("Failed to get book for %s", coin)
            return {"action": "error", "coin": coin}

        levels = book.get("levels", [[], []])
        bids = levels[0] if len(levels) > 0 else []
        asks = levels[1] if len(levels) > 1 else []

        if not bids or not asks:
            return {"action": "no_book", "coin": coin}

        best_bid = float(bids[0]["px"])
        best_ask = float(asks[0]["px"])
        mid = (best_bid + best_ask) / 2
        market_spread_bps = (best_ask - best_bid) / mid * 10000

        if mid <= 0:
            return {"action": "invalid_mid", "coin": coin}

        now = time.time()
        spread_mult = 1.0
        if coin in self._prev_mids and self._prev_mids[coin] > 0:
            change = abs(mid - self._prev_mids[coin]) / self._prev_mids[coin]
            if change > self._vol_pause_pct:
                spread_mult = self._vol_widen
                self._vol_pause_until[coin] = now + self._vol_cooldown
                log.info("%sVolatility on %s (%.2f%%), widening", self._tag, coin, change * 100)

        self._prev_mids[coin] = mid

        if now < self._vol_pause_until.get(coin, 0):
            spread_mult = max(spread_mult, self._vol_widen)

        self._cancel_orders(coin)

        half_spread_bps = (self._spread_bps / 2) * spread_mult

        inv = self._inventory.get(coin, 0.0)
        skew_frac = (inv / self._max_inv_usd) * (self._spread_bps / 10000) * 0.5 * self._skew_intensity if self._max_inv_usd > 0 else 0
        skewed_mid = mid * (1 - skew_frac)

        can_buy = inv < self._max_inv_usd and self._get_total_exposure() < self._max_total_usd
        can_sell = inv > -self._max_inv_usd

        new_oids = []
        for tier in range(self._num_tiers):
            offset_bps = half_spread_bps + tier * self._tier_spacing_bps
            offset_frac = offset_bps / 10000
            size_usd = self._base_order_usd * (self._tier_multiplier ** tier)
            size = round(size_usd / mid, 6)

            if can_buy:
                bid_px = round(skewed_mid * (1 - offset_frac), 6)
                oid = self._place_order(coin, "buy", size, bid_px)
                if oid:
                    new_oids.append(oid)

            if can_sell:
                ask_px = round(skewed_mid * (1 + offset_frac), 6)
                oid = self._place_order(coin, "sell", size, ask_px)
                if oid:
                    new_oids.append(oid)

        self._open_oids[coin] = new_oids

        return {
            "action": "refreshed",
            "coin": coin,
            "mid": mid,
            "spread_bps": self._spread_bps * spread_mult,
            "market_spread_bps": market_spread_bps,
            "inventory_usd": inv,
            "orders_placed": len(new_oids),
        }

    def sync_inventory(self) -> None:
        try:
            account = self._client.get_account_state()
        except Exception:
            log.warning("Failed to sync inventory")
            return

        for pos in account.positions:
            if pos.coin in self._inventory:
                old = self._inventory[pos.coin]
                new = pos.size * pos.mark_price
                if abs(new - old) > 0.01:
                    log.info(
                        "%sInventory sync %s: $%.2f → $%.2f",
                        self._tag, pos.coin, old, new,
                    )
                self._inventory[pos.coin] = new

    def cancel_all(self) -> None:
        for coin in self._coins:
            self._cancel_orders(coin)

    def get_summary(self) -> str:
        lines = [f"{self._tag}HL MM Summary | daily PnL: ${self._daily_pnl:+.2f} | fills: {self._fills_count}"]
        for coin in self._coins:
            inv = self._inventory.get(coin, 0.0)
            orders = len(self._open_oids.get(coin, []))
            mid = self._prev_mids.get(coin, 0)
            lines.append(f"  {coin}: inv=${inv:+.2f} | orders={orders} | mid=${mid:.4f}")
        lines.append(f"  Total exposure: ${self._get_total_exposure():.2f} / ${self._max_total_usd:.0f}")
        return "\n".join(lines)

    def _place_order(self, coin: str, side: str, size: float, price: float) -> int | None:
        if self._paper:
            log.debug("%s%s %s %.6f @ %.6f", self._tag, side.upper(), coin, size, price)
            return hash((coin, side, price, time.time())) % 1_000_000

        try:
            req = OrderRequest(
                coin=coin,
                side=Side.BUY if side == "buy" else Side.SELL,
                size=size,
                price=price,
                order_type=OrderType.LIMIT,
                reduce_only=False,
            )
            result = self._client.place_order(req)
            if result.order_id:
                return int(result.order_id)
        except Exception:
            log.warning("Failed to place %s order for %s", side, coin)
        return None

    def _cancel_orders(self, coin: str) -> None:
        oids = self._open_oids.get(coin, [])
        if not oids:
            return

        if self._paper:
            self._open_oids[coin] = []
            return

        for oid in oids:
            try:
                self._client.cancel_order(coin, oid)
            except Exception:
                pass
        self._open_oids[coin] = []

    def _get_total_exposure(self) -> float:
        return sum(abs(v) for v in self._inventory.values())

    def _check_day_reset(self) -> None:
        today = _today()
        if today != self._day_key:
            self._daily_pnl = 0.0
            self._fills_count = 0
            self._day_key = today
            self._halted = False


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def show_status(client: HLRestClient, config: dict) -> None:
    coins = config.get("hl_mm", {}).get("coins", [])
    mids = client.get_all_mids()

    for coin in coins:
        mid = mids.get(coin, 0)
        print(f"\n{coin}:")
        print(f"  Mid: ${mid:.6f}")

        try:
            book = client.get_l2_snapshot(coin)
            levels = book.get("levels", [[], []])
            bids = levels[0] if len(levels) > 0 else []
            asks = levels[1] if len(levels) > 1 else []

            if bids and asks:
                bb = float(bids[0]["px"])
                ba = float(asks[0]["px"])
                spread = (ba - bb) / mid * 10000 if mid > 0 else 0
                print(f"  Best bid: ${bb:.6f}")
                print(f"  Best ask: ${ba:.6f}")
                print(f"  Spread: {spread:.1f} bps")

                bid_depth = sum(float(b["px"]) * float(b["sz"]) for b in bids[:5])
                ask_depth = sum(float(a["px"]) * float(a["sz"]) for a in asks[:5])
                print(f"  Bid depth (5): ${bid_depth:,.0f}")
                print(f"  Ask depth (5): ${ask_depth:,.0f}")
        except Exception as e:
            print(f"  Error: {e}")

    try:
        account = client.get_account_state()
        print(f"\nAccount:")
        print(f"  Equity: ${account.equity:,.2f}")
        print(f"  Available: ${account.available_balance:,.2f}")
        for pos in account.positions:
            if abs(pos.size) > 1e-8:
                print(f"  Position {pos.coin}: {pos.size:.4f} @ ${pos.entry_price:.4f} (PnL: ${pos.unrealized_pnl:+.2f})")
    except Exception as e:
        print(f"\nAccount: Error — {e}")

    print()


def run_bot(client: HLRestClient, config: dict, paper: bool) -> None:
    agent = HLMMAgent(client, config, paper=paper)
    alerts = AlertManager(config)

    mode = "PAPER" if paper else "LIVE"
    log.info("HL Market Maker starting", extra={"mode": mode, "coins": agent.coins})
    alerts.send(f"HL MM started ({mode}) — coins: {', '.join(agent.coins)}")

    running = True
    last_summary = 0.0
    last_sync = 0.0
    tick_interval = config.get("hl_mm", {}).get("tick_interval_sec", 5)
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
        for coin in agent.coins:
            try:
                result = agent.tick(coin)
                if result["action"] == "refreshed":
                    log.info(
                        "%sTick %s: mid=$%.6f spread=%.1fbps mkt_spread=%.1fbps inv=$%.2f orders=%d",
                        "[PAPER] " if paper else "",
                        coin, result["mid"], result["spread_bps"],
                        result["market_spread_bps"], result["inventory_usd"],
                        result["orders_placed"],
                    )
                    error_count = 0
                elif result["action"] == "error":
                    error_count += 1
                    if error_count >= max_errors:
                        log.warning("Too many errors (%d), pausing 60s", error_count)
                        alerts.send(f"HL MM paused: {error_count} consecutive errors")
                        time.sleep(60)
                        error_count = 0
            except Exception:
                log.exception("Error in MM tick for %s", coin)
                error_count += 1

        now = time.time()
        if not paper and now - last_sync >= 30:
            agent.sync_inventory()
            last_sync = now

        if now - last_summary >= summary_interval:
            summary = agent.get_summary()
            log.info(summary)
            alerts.send(summary)
            last_summary = now

        time.sleep(tick_interval)

    log.info("Shutting down, cancelling all orders...")
    agent.cancel_all()
    alerts.send("HL MM stopped — all orders cancelled")


def main() -> None:
    parser = argparse.ArgumentParser(description="Hyperliquid Market Maker Bot")
    parser.add_argument("--live", action="store_true", help="LIVE trading (real orders)")
    parser.add_argument("--status", action="store_true", help="Show orderbook status")
    args = parser.parse_args()

    config = load_config()
    setup_logging(config)

    if not os.environ.get("HL_PRIVATE_KEY"):
        print("Error: set HL_PRIVATE_KEY environment variable")
        print("  export HL_PRIVATE_KEY=0x...")
        sys.exit(1)

    client = HLRestClient({"exchange": {"use_testnet": False}})

    if args.status:
        show_status(client, config)
    elif args.live:
        print("\n  *** WARNING: LIVE TRADING MODE ***")
        print("  This will place REAL orders on Hyperliquid mainnet.")
        confirm = input("  Type 'YES' to confirm: ")
        if confirm.strip() != "YES":
            print("  Aborted.")
            sys.exit(0)
        run_bot(client, config, paper=False)
    else:
        run_bot(client, config, paper=True)


if __name__ == "__main__":
    main()
