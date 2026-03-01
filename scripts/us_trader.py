#!/usr/bin/env python3
"""US stock trading CLI — paper & live via Alpaca."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from biubiu_invest.alpaca_provider import AlpacaProvider
from biubiu_invest.broker import AlpacaBroker
from biubiu_invest.data_providers import load_watchlist
from biubiu_invest.paths import data_dir, default_db_path, repo_root
from biubiu_invest.policy import load_policy
from biubiu_invest.risk import RiskLimits, RiskManager
from biubiu_invest.strategy import MeanReversionStrategy, MomentumStrategy
from biubiu_invest.trader import Trader

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("us_trader")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="US stock trader (Alpaca)")
    p.add_argument(
        "--strategy",
        default="momentum",
        choices=["momentum", "mean_reversion"],
    )
    p.add_argument(
        "--watchlist",
        default=str(data_dir() / "us_watchlist.txt"),
    )
    p.add_argument(
        "--lookback",
        type=int,
        default=20,
        help="Momentum lookback in trading days",
    )
    p.add_argument(
        "--top",
        type=int,
        default=5,
        help="Number of top stocks to hold",
    )
    p.add_argument("--db", default=str(default_db_path()))
    p.add_argument(
        "--policy",
        default=str(repo_root() / "policies" / "us_paper.json"),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print signals without trading",
    )
    p.add_argument(
        "--live",
        action="store_true",
        default=False,
        help="Use Alpaca live trading (requires live policy)",
    )
    # Risk overrides
    p.add_argument("--max-position-pct", type=float, default=0.10)
    p.add_argument("--stop-loss-pct", type=float, default=0.05)
    p.add_argument("--max-daily-trades", type=int, default=3)
    return p.parse_args()


def main() -> None:
    args = parse_args()

    # Watchlist
    wl_path = Path(args.watchlist).expanduser().resolve()
    symbols = load_watchlist(wl_path)
    if not symbols:
        raise SystemExit(f"Empty watchlist: {wl_path}")
    log.info("Watchlist: %d symbols from %s", len(symbols), wl_path.name)

    # Policy
    policy = load_policy(args.policy)
    log.info(
        "Policy: %s (mode=%s, live=%s)",
        policy.id,
        policy.mode,
        policy.allow_live_trading,
    )

    # Paper vs live
    paper = not args.live
    if args.live and not policy.allow_live_trading:
        log.error(
            "Policy '%s' does not allow live trading. "
            "Use paper mode or change policy.",
            policy.id,
        )
        sys.exit(1)

    # Broker + Provider
    provider = AlpacaProvider()
    broker = AlpacaBroker(paper=paper)

    # Strategy
    if args.strategy == "momentum":
        strategy = MomentumStrategy(lookback=args.lookback, top_n=args.top)
    else:
        strategy = MeanReversionStrategy()

    # Risk
    limits = RiskLimits(
        max_position_pct=args.max_position_pct,
        stop_loss_pct=args.stop_loss_pct,
        max_daily_trades=args.max_daily_trades,
    )
    risk = RiskManager(limits=limits)

    # Trader
    db_path = Path(args.db).expanduser().resolve()
    trade_log = data_dir() / "trades.jsonl"

    trader = Trader(
        broker=broker,
        provider=provider,
        strategy=strategy,
        risk_manager=risk,
        policy=policy,
        symbols=symbols,
        db_path=db_path,
        trade_log_path=trade_log,
        dry_run=args.dry_run,
    )

    records = trader.run()

    # Summary
    buys = [r for r in records if r.side == "buy" and r.status not in ("skipped", "blocked")]
    sells = [r for r in records if r.side == "sell"]
    print(f"\n{'=' * 60}")
    print(f"Trading cycle complete: {len(buys)} buys, {len(sells)} sells")
    for r in records:
        tag = "[DRY]" if r.status == "dry_run" else f"[{r.status.upper()}]"
        price_str = f"@ ${r.filled_price:.2f}" if r.filled_price else ""
        print(f"  {tag} {r.side.upper():4s} {r.qty:>5g} x {r.symbol:<6s} {price_str} — {r.reason}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
