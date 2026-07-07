"""Polymarket 24/7 automated trading bot."""

from __future__ import annotations

import time
import traceback
from datetime import datetime, timezone
from typing import Optional

from src.logger import get_logger
from src.polymarket.client import PolymarketClient
from src.polymarket.paper import PaperExecutor
from src.polymarket.risk import PolymarketRiskManager
from src.polymarket.scanner import MarketScanner
from src.polymarket.strategy import EdgeStrategy, MarketMakerStrategy, MeanReversionStrategy, PMStrategy
from src.polymarket.types import BotState, Opportunity, Outcome, Side

log = get_logger(__name__)


class PolymarketBot:
    """Main bot loop — scans markets, evaluates opportunities, executes trades 24/7."""

    def __init__(self, cfg: dict) -> None:
        self._cfg = cfg
        pm_cfg = cfg.get("polymarket", {})

        self._paper_mode = pm_cfg.get("paper_mode", True)
        self._cycle_interval = pm_cfg.get("cycle_interval_seconds", 60)
        self._scan_interval = pm_cfg.get("scan_interval_seconds", 300)
        self._max_trades_per_cycle = pm_cfg.get("max_trades_per_cycle", 3)

        # components
        self._client = PolymarketClient(cfg)
        self._scanner = MarketScanner(self._client, cfg)
        self._risk = PolymarketRiskManager(cfg)
        self._paper = PaperExecutor(cfg) if self._paper_mode else None

        # strategies
        self._strategies: list[PMStrategy] = self._init_strategies(cfg)

        # state
        self._state = BotState()
        self._last_scan_time = 0.0
        self._cached_opportunities: list[Opportunity] = []

        log.info(
            f"PolymarketBot initialized: "
            f"paper={self._paper_mode}, "
            f"strategies={[s.name() for s in self._strategies]}, "
            f"cycle={self._cycle_interval}s, scan={self._scan_interval}s"
        )

    def _init_strategies(self, cfg: dict) -> list[PMStrategy]:
        pm_cfg = cfg.get("polymarket", {})
        strategy_names = pm_cfg.get("strategies", ["mean_reversion"])
        strats: list[PMStrategy] = []

        for name in strategy_names:
            if name == "edge":
                strats.append(EdgeStrategy(cfg))
            elif name == "mean_reversion":
                strats.append(MeanReversionStrategy(cfg))
            elif name == "market_maker":
                strats.append(MarketMakerStrategy(cfg))
            else:
                log.warning(f"Unknown strategy: {name}")

        if not strats:
            strats.append(MeanReversionStrategy(cfg))

        return strats

    def run(self) -> None:
        """Main loop — runs until halted or interrupted."""
        log.info("=" * 60)
        log.info("Polymarket Bot starting")
        log.info(f"Mode: {'PAPER' if self._paper_mode else 'LIVE'}")
        log.info("=" * 60)

        if not self._paper_mode:
            balance = self._client.get_balance()
            log.info(f"Live balance: ${balance:.2f}")
        else:
            log.info(f"Paper balance: ${self._paper.get_balance():.2f}")

        try:
            while not self._state.halted:
                self._run_cycle()
                time.sleep(self._cycle_interval)
        except KeyboardInterrupt:
            log.info("Bot stopped by user (Ctrl+C)")
        except Exception:
            log.exception("Bot crashed")
            raise
        finally:
            self._shutdown()

    def _run_cycle(self) -> None:
        """One bot cycle: scan → evaluate → trade → monitor."""
        self._state.cycle_count += 1
        now = time.monotonic()

        # scan for opportunities periodically
        if now - self._last_scan_time >= self._scan_interval or not self._cached_opportunities:
            try:
                self._scan()
                self._last_scan_time = now
            except Exception as e:
                log.error(f"Scan failed: {e}")
                self._state.errors_today += 1
                return

        # check circuit breakers
        current_pnl = self._get_pnl()
        cb = self._risk.check_circuit_breakers(self._state, current_pnl)
        if not cb.passed:
            log.warning(f"Circuit breaker triggered: {cb.reason}")
            return

        # evaluate and trade
        trades_this_cycle = 0
        for opp in self._cached_opportunities[:]:
            if trades_this_cycle >= self._max_trades_per_cycle:
                break

            balance = self._get_balance()
            exposure = self._get_total_exposure()

            rc = self._risk.check_opportunity(opp, self._state, exposure, balance)
            if not rc.passed:
                continue

            size_usd = self._risk.size_position(opp, balance, exposure)
            if size_usd <= 0:
                continue

            # convert USDC to shares: shares = usd / price
            price = opp.market_prob
            if price <= 0 or price >= 1:
                continue
            shares = size_usd / price

            success = self._execute_trade(opp, price, shares)
            if success:
                trades_this_cycle += 1
                self._state.trades_today += 1
                self._cached_opportunities.remove(opp)

        # periodic status log
        if self._state.cycle_count % 10 == 0:
            self._log_status()

    def _scan(self) -> None:
        """Scan markets and merge opportunities from all strategies."""
        markets = self._client.get_markets(
            active=True,
            limit=200,
            min_volume=self._cfg.get("polymarket", {}).get("scanner", {}).get("min_volume", 10000),
            min_liquidity=self._cfg.get("polymarket", {}).get("scanner", {}).get("min_liquidity", 5000),
        )

        all_opps: list[Opportunity] = []
        for strat in self._strategies:
            opps = strat.evaluate(markets)
            log.info(f"Strategy '{strat.name()}' found {len(opps)} opportunities")
            all_opps.extend(opps)

        # deduplicate by market+outcome, keep highest EV
        seen: dict[str, Opportunity] = {}
        for opp in all_opps:
            key = f"{opp.market.condition_id}:{opp.outcome.value}"
            if key not in seen or opp.ev > seen[key].ev:
                seen[key] = opp

        self._cached_opportunities = sorted(seen.values(), key=lambda x: x.ev, reverse=True)
        self._state.last_scan_ts = datetime.now(timezone.utc).isoformat()
        log.info(f"Scan complete: {len(self._cached_opportunities)} unique opportunities")

    def _execute_trade(self, opp: Opportunity, price: float, shares: float) -> bool:
        """Execute a trade via paper or live executor."""
        token_id = (
            opp.market.yes_token_id if opp.outcome == Outcome.YES else opp.market.no_token_id
        )

        q = opp.market.question[:60]
        log.info(
            f"TRADE: {opp.side.value} {shares:.2f} shares of {opp.outcome.value} "
            f"@{price:.4f} | {q} | edge={opp.edge:.1%} EV={opp.ev:+.4f}"
        )

        if self._paper_mode and self._paper:
            result = self._paper.place_order(
                token_id=token_id,
                side=opp.side,
                price=price,
                size=shares,
                market=opp.market,
            )
        else:
            result = self._client.place_order(
                token_id=token_id,
                side=opp.side,
                price=price,
                size=shares,
            )

        if result.success:
            log.info(f"Trade executed: {result.order_id} filled={result.filled_size}")
            return True
        else:
            log.error(f"Trade failed: {result.error}")
            self._state.errors_today += 1
            return False

    def _get_balance(self) -> float:
        if self._paper_mode and self._paper:
            return self._paper.get_balance()
        return self._client.get_balance()

    def _get_total_exposure(self) -> float:
        if self._paper_mode and self._paper:
            return sum(
                p.size * p.avg_price
                for p in self._paper.account.positions.values()
            )
        return 0.0  # TODO: compute from live positions

    def _get_pnl(self) -> float:
        if self._paper_mode and self._paper:
            return self._paper.account.total_pnl
        return 0.0  # TODO: compute from live

    def _log_status(self) -> None:
        bal = self._get_balance()
        pnl = self._get_pnl()
        n_pos = len(self._paper.account.positions) if self._paper_mode and self._paper else 0
        mode = "PAPER" if self._paper_mode else "LIVE"
        log.info(
            f"[{mode}] cycle={self._state.cycle_count} "
            f"bal=${bal:.2f} pnl=${pnl:+.2f} "
            f"positions={n_pos} trades_today={self._state.trades_today} "
            f"pending_opps={len(self._cached_opportunities)} "
            f"errors={self._state.errors_today}"
        )

    def _shutdown(self) -> None:
        """Clean shutdown: cancel orders, log final state."""
        log.info("Bot shutting down...")
        if self._paper_mode and self._paper:
            log.info(self._paper.summary())
        else:
            cancelled = self._client.cancel_all()
            log.info(f"Cancelled {cancelled} open orders")
        log.info(f"Final state: cycles={self._state.cycle_count}, trades={self._state.trades_today}")

    def status(self) -> dict:
        """Return current bot status as a dict."""
        return {
            "mode": "paper" if self._paper_mode else "live",
            "balance": self._get_balance(),
            "pnl": self._get_pnl(),
            "positions": len(self._paper.account.positions) if self._paper_mode and self._paper else 0,
            "trades_today": self._state.trades_today,
            "cycle_count": self._state.cycle_count,
            "pending_opportunities": len(self._cached_opportunities),
            "errors_today": self._state.errors_today,
            "halted": self._state.halted,
            "halt_reason": self._state.halt_reason,
            "strategies": [s.name() for s in self._strategies],
        }
