"""Run the Polymarket trading bot.

Usage:
  python scripts/run_polymarket.py                    # paper mode (default)
  python scripts/run_polymarket.py --live              # live mode (requires API keys)
  python scripts/run_polymarket.py --scan              # scan only, no trading
  python scripts/run_polymarket.py --config config/polymarket_live.yaml
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# ensure src is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml

from src.logger import setup_logging
from src.polymarket.bot import PolymarketBot
from src.polymarket.client import PolymarketClient
from src.polymarket.scanner import MarketScanner


def load_config(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def cmd_run(cfg: dict) -> None:
    setup_logging(cfg)
    bot = PolymarketBot(cfg)
    bot.run()


def cmd_scan(cfg: dict) -> None:
    setup_logging(cfg)
    client = PolymarketClient(cfg)
    scanner = MarketScanner(client, cfg)
    opps = scanner.scan()
    print(scanner.format_scan_report(opps))


def cmd_status(cfg: dict) -> None:
    setup_logging(cfg)
    client = PolymarketClient(cfg)
    markets = client.get_markets(active=True, limit=10)
    print(f"Connected to Polymarket. Active markets fetched: {len(markets)}")
    for m in markets[:5]:
        print(f"  {m.question[:60]} | YES={m.yes_price:.0%} | vol=${m.volume:,.0f} | liq=${m.liquidity:,.0f}")


def main() -> None:
    p = argparse.ArgumentParser(description="Polymarket Trading Bot")
    p.add_argument("--config", default="config/polymarket.yaml", help="Config file path")
    p.add_argument("--live", action="store_true", help="Enable live trading (default: paper)")
    p.add_argument("--scan", action="store_true", help="Scan markets only, no trading")
    p.add_argument("--status", action="store_true", help="Check connection and show top markets")
    args = p.parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print(f"Config not found: {cfg_path}", file=sys.stderr)
        sys.exit(1)

    cfg = load_config(str(cfg_path))

    if args.live:
        cfg.setdefault("polymarket", {})["paper_mode"] = False
        print("WARNING: LIVE MODE — real money at risk!")

    if args.status:
        cmd_status(cfg)
    elif args.scan:
        cmd_scan(cfg)
    else:
        cmd_run(cfg)


if __name__ == "__main__":
    main()
