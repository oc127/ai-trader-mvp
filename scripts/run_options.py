"""Run the Options Premium Seller (TradingWarz theta harvest on Deribit).

Usage:
  python scripts/run_options.py                   # testnet (default)
  python scripts/run_options.py --live             # production (real money!)
  python scripts/run_options.py --scan             # scan only, show opportunities
  python scripts/run_options.py --status           # check Deribit connection
  python scripts/run_options.py --config config/options_custom.yaml
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml

from src.logger import setup_logging


def load_config(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def cmd_run(cfg: dict) -> None:
    setup_logging(cfg)

    from src.options.premium_seller import PremiumSellerBot

    bot = PremiumSellerBot(cfg)
    try:
        bot.run()
    except KeyboardInterrupt:
        print("\nShutting down...")
        bot.stop()


def cmd_scan(cfg: dict) -> None:
    setup_logging(cfg)

    from src.options.deribit_client import DeribitClient
    from src.options.scanner import OptionsScanner

    client = DeribitClient(cfg)
    if not client.authenticate():
        print("Failed to authenticate with Deribit", file=sys.stderr)
        sys.exit(1)

    scanner = OptionsScanner(client, cfg)
    results = scanner.scan()
    print(scanner.format_report(results))


def cmd_status(cfg: dict) -> None:
    setup_logging(cfg)

    from src.options.deribit_client import DeribitClient

    client = DeribitClient(cfg)

    # test public endpoint (no auth needed)
    btc_price = client.get_index_price("BTC")
    eth_price = client.get_index_price("ETH")
    print(f"Deribit connection OK ({'testnet' if cfg.get('options', {}).get('testnet', True) else 'PRODUCTION'})")
    print(f"  BTC: ${btc_price:,.0f}")
    print(f"  ETH: ${eth_price:,.0f}")

    # test auth
    if client.authenticate():
        print("  Auth: OK")
        for ccy in ["BTC", "ETH"]:
            summary = client.get_account_summary(ccy)
            if summary:
                equity = summary.get("equity", 0)
                balance = summary.get("balance", 0)
                avail = summary.get("available_funds", 0)
                price = btc_price if ccy == "BTC" else eth_price
                print(
                    f"  {ccy} account: equity={equity:.4f} (${equity * price:,.0f}) "
                    f"balance={balance:.4f} available={avail:.4f}"
                )
    else:
        print("  Auth: FAILED (check DERIBIT_CLIENT_ID / DERIBIT_CLIENT_SECRET)")

    # count available options
    for ccy in ["BTC", "ETH"]:
        instruments = client.get_instruments(currency=ccy)
        print(f"  {ccy} options: {len(instruments)} instruments")


def main() -> None:
    p = argparse.ArgumentParser(description="Options Premium Seller (TradingWarz Theta Harvest)")
    p.add_argument("--config", default="config/options.yaml", help="Config file path")
    p.add_argument("--live", action="store_true", help="Use production Deribit (real money!)")
    p.add_argument("--scan", action="store_true", help="Scan for options to sell, no trading")
    p.add_argument("--status", action="store_true", help="Check Deribit connection and account")
    args = p.parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print(f"Config not found: {cfg_path}", file=sys.stderr)
        sys.exit(1)

    cfg = load_config(str(cfg_path))

    if args.live:
        cfg.setdefault("options", {})["testnet"] = False
        print("⚠️  PRODUCTION MODE — real money at risk!")

    if args.status:
        cmd_status(cfg)
    elif args.scan:
        cmd_scan(cfg)
    else:
        cmd_run(cfg)


if __name__ == "__main__":
    main()
