"""Run the Polymarket Unified Trading Bot.

Usage:
  python scripts/run_polymarket.py                           # paper mode (default)
  python scripts/run_polymarket.py --live                    # live mode (requires API keys)
  python scripts/run_polymarket.py --live --preflight        # check credentials + connection first
  python scripts/run_polymarket.py --scan                    # scan only, no trading
  python scripts/run_polymarket.py --config config/polymarket_live.yaml --live
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

import yaml  # noqa: E402

from src.logger import get_logger, setup_logging  # noqa: E402
from src.polymarket.client import PolymarketClient  # noqa: E402
from src.polymarket.scanner import MarketScanner  # noqa: E402
from src.polymarket.unified_bot import UnifiedPolymarketBot  # noqa: E402

log = get_logger(__name__)


def load_config(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def preflight_check(cfg: dict) -> bool:
    """Validate credentials and connection before live trading."""
    print("=" * 50)
    print("PREFLIGHT CHECK — Polymarket Live Trading")
    print("=" * 50)

    errors = []

    # 1. Check credentials
    pk = cfg.get("polymarket", {}).get("private_key") or os.getenv("POLYMARKET_PRIVATE_KEY", "")
    if not pk:
        errors.append("POLYMARKET_PRIVATE_KEY not set")
    else:
        print(f"  [OK] Private key: ...{pk[-8:]}")

    api_key = cfg.get("polymarket", {}).get("api_key") or os.getenv("POLYMARKET_API_KEY", "")
    api_secret = cfg.get("polymarket", {}).get("api_secret") or os.getenv("POLYMARKET_API_SECRET", "")
    api_pass = cfg.get("polymarket", {}).get("api_passphrase") or os.getenv("POLYMARKET_API_PASSPHRASE", "")

    if api_key and api_secret and api_pass:
        print(f"  [OK] API creds: key={api_key[:8]}...")
    else:
        print("  [..] API creds not set (will derive from private key)")

    funder = cfg.get("polymarket", {}).get("funder_address") or os.getenv("POLYMARKET_FUNDER_ADDRESS", "")
    if funder:
        print(f"  [OK] Funder: {funder[:10]}...")

    if errors:
        for e in errors:
            print(f"  [FAIL] {e}")
        return False

    # 2. Test Gamma API (public, no auth)
    print("\n  Testing Gamma API (market data)...")
    try:
        client = PolymarketClient(cfg)
        markets = client.get_markets(active=True, limit=5)
        print(f"  [OK] Gamma API: {len(markets)} markets fetched")
        if markets:
            m = markets[0]
            print(f"       Top: {m.question[:50]} YES={m.yes_price:.0%}")
    except Exception as e:
        errors.append(f"Gamma API failed: {e}")

    # 3. Test CLOB connection (auth required)
    print("\n  Testing CLOB V2 (authenticated)...")
    try:
        balance = client.get_balance()
        print(f"  [OK] CLOB V2 connected — balance: ${balance:.2f} USDC")
        if balance < 5.0:
            print(f"  [WARN] Balance very low (${balance:.2f}). Need at least $5 to trade.")
    except Exception as e:
        errors.append(f"CLOB V2 auth failed: {e}")

    # 4. Config summary
    pm = cfg.get("polymarket", {})
    hf = pm.get("hf_market_maker", {})
    risk = pm.get("risk", {})
    print("\n  Config summary:")
    print(f"    Max exposure:  ${hf.get('max_total_exposure', 150)}")
    print(f"    Max daily loss: ${risk.get('max_daily_loss_usd', 100)}")
    print(f"    Quote size:    ${hf.get('quote_size_usd', 10)}/side")
    print(f"    Max markets:   {hf.get('max_markets', 3)}")
    print(f"    Layers:        maker={'ON' if pm.get('maker_enabled', True) else 'OFF'} "
          f"edge={'ON' if pm.get('edge_enabled', True) else 'OFF'} "
          f"arb={'ON' if pm.get('arb_enabled', True) else 'OFF'} "
          f"copy={'ON' if pm.get('copy_enabled', False) else 'OFF'}")

    if errors:
        print("\n  PREFLIGHT FAILED:")
        for e in errors:
            print(f"    [FAIL] {e}")
        return False

    print("\n  ALL CHECKS PASSED")
    print("=" * 50)
    return True


def cmd_run(cfg: dict) -> None:
    setup_logging(cfg)
    bot = UnifiedPolymarketBot(cfg)
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
    p = argparse.ArgumentParser(description="Polymarket Unified Trading Bot")
    p.add_argument("--config", default="config/polymarket.yaml", help="Config file path")
    p.add_argument("--live", action="store_true", help="Enable live trading (default: paper)")
    p.add_argument("--scan", action="store_true", help="Scan markets only, no trading")
    p.add_argument("--status", action="store_true", help="Check connection and show top markets")
    p.add_argument("--preflight", action="store_true", help="Run preflight checks before live trading")
    args = p.parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print(f"Config not found: {cfg_path}", file=sys.stderr)
        sys.exit(1)

    cfg = load_config(str(cfg_path))

    if args.live:
        cfg.setdefault("polymarket", {})["paper_mode"] = False
        if args.config == "config/polymarket.yaml":
            live_path = Path("config/polymarket_live.yaml")
            if live_path.exists():
                print(f"[INFO] Live mode: auto-loading {live_path}")
                cfg = load_config(str(live_path))
                cfg["polymarket"]["paper_mode"] = False

    if args.preflight or (args.live and not args.scan and not args.status):
        if not preflight_check(cfg):
            print("\nFix the issues above before running live.", file=sys.stderr)
            sys.exit(1)
        if args.preflight and not args.live:
            return

    if args.live and not args.scan:
        print("\n" + "!" * 50)
        print("  LIVE MODE — REAL MONEY AT RISK")
        print("  Press Ctrl+C within 5 seconds to abort...")
        print("!" * 50 + "\n")
        import time
        try:
            time.sleep(5)
        except KeyboardInterrupt:
            print("\nAborted.")
            sys.exit(0)

    if args.status:
        cmd_status(cfg)
    elif args.scan:
        cmd_scan(cfg)
    else:
        cmd_run(cfg)


if __name__ == "__main__":
    main()
