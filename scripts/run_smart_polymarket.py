"""Run the Smart Polymarket bot (mean reversion + edge detection).

Usage:
  python scripts/run_smart_polymarket.py                    # paper mode (default)
  python scripts/run_smart_polymarket.py --live              # live mode (requires API keys)
  python scripts/run_smart_polymarket.py --scan              # scan only, no trading
  python scripts/run_smart_polymarket.py --backtest          # run market maker backtest
  python scripts/run_smart_polymarket.py --status            # check connection
  python scripts/run_smart_polymarket.py --ai                # enable AI edge analysis
  python scripts/run_smart_polymarket.py --config config/polymarket_live.yaml
"""

from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path

# ensure src is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml

from src.logger import setup_logging
from src.polymarket.client import PolymarketClient
from src.polymarket.scanner import MarketScanner


def load_config(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def cmd_run(cfg: dict, *, enable_ai: bool = False) -> None:
    setup_logging(cfg)

    from src.polymarket.smart_bot import SmartPolymarketBot

    ai_thread: threading.Thread | None = None

    if enable_ai:
        ai_cfg = cfg.get("ai_edge", {})
        if not ai_cfg.get("enabled", False):
            # --ai flag overrides config
            cfg.setdefault("ai_edge", {})["enabled"] = True

        from src.polymarket.ai_edge import AIEdgeAnalyzer

        analyzer = AIEdgeAnalyzer(cfg)
        ai_thread = threading.Thread(
            target=analyzer.run,
            name="ai-edge-analyzer",
            daemon=True,
        )
        ai_thread.start()
        print("AI edge analyzer started on background thread")

        bot = SmartPolymarketBot(cfg, ai_analyzer=analyzer)
    else:
        bot = SmartPolymarketBot(cfg)

    try:
        bot.run()
    except KeyboardInterrupt:
        print("\nShutting down smart bot...")
        bot.stop()


def cmd_scan(cfg: dict) -> None:
    setup_logging(cfg)
    client = PolymarketClient(cfg)
    scanner = MarketScanner(client, cfg)
    opps = scanner.scan()
    print(scanner.format_scan_report(opps))


def cmd_backtest(cfg: dict) -> None:
    setup_logging(cfg)

    from src.polymarket.backtest import MarketMakerBacktest, format_report

    bt_cfg = cfg.get("polymarket", {}).get("hf_market_maker", {})
    bt = MarketMakerBacktest(cfg)

    # Configurable backtest parameters with sensible defaults
    n_ticks = bt_cfg.get("backtest_ticks", 2000)
    mid_price = bt_cfg.get("backtest_mid_price", 0.50)
    volatility = bt_cfg.get("backtest_volatility", 0.005)
    book_spread = bt_cfg.get("backtest_book_spread", 0.04)
    fill_prob = bt_cfg.get("backtest_fill_probability", 0.3)
    seed = bt_cfg.get("backtest_seed", 42)

    print(f"Running market maker backtest ({n_ticks} ticks, mid={mid_price}, "
          f"vol={volatility}, spread={book_spread}, fill_p={fill_prob})...")

    result = bt.simulate(
        n_ticks=n_ticks,
        mid_price=mid_price,
        volatility=volatility,
        book_spread=book_spread,
        fill_probability=fill_prob,
        seed=seed,
    )
    print(format_report(result))


def cmd_status(cfg: dict) -> None:
    setup_logging(cfg)
    client = PolymarketClient(cfg)
    markets = client.get_markets(active=True, limit=10)
    print(f"Connected to Polymarket. Active markets fetched: {len(markets)}")
    for m in markets[:5]:
        print(f"  {m.question[:60]} | YES={m.yes_price:.0%} "
              f"| vol=${m.volume:,.0f} | liq=${m.liquidity:,.0f}")


def main() -> None:
    p = argparse.ArgumentParser(description="Smart Polymarket Bot (mean reversion + edge)")
    p.add_argument("--config", default="config/polymarket.yaml", help="Config file path")
    p.add_argument("--live", action="store_true", help="Enable live trading (default: paper)")
    p.add_argument("--scan", action="store_true", help="Scan markets only, no trading")
    p.add_argument("--backtest", action="store_true", help="Run market maker backtest")
    p.add_argument("--status", action="store_true", help="Check connection and show top markets")
    p.add_argument("--ai", action="store_true", help="Enable AI edge analysis (background thread)")
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
    elif args.backtest:
        cmd_backtest(cfg)
    else:
        cmd_run(cfg, enable_ai=args.ai)


if __name__ == "__main__":
    main()
