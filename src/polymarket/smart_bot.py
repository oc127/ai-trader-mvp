"""Smart Polymarket trading bot — green/yellow-light directional strategies.

Philosophy: Only trade when we have an edge. No market making, no HFT.
- Mean reversion on thin/extreme markets (green light — always on)
- Model-based edge detection (yellow light — needs external probability feed)
- Conservative sizing (half-Kelly), strict risk limits
- 30-second cycles — we're not trying to be fast, we're trying to be right
"""

from __future__ import annotations

import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from src.logger import get_logger
from src.monitor.alerts import AlertManager
from src.polymarket.client import PolymarketClient
from src.polymarket.paper import PaperExecutor
from src.polymarket.risk import PolymarketRiskManager
from src.polymarket.strategy import EdgeStrategy, MeanReversionStrategy
from src.polymarket.types import BotState, Market, Opportunity, Outcome, Side

log = get_logger(__name__)


@dataclass
class TrackedPosition:
    """An open position with entry metadata for exit logic."""
    market: Market
    outcome: Outcome
    token_id: str
    entry_price: float
    size: float
    edge_at_entry: float
    entered_at: float  # monotonic timestamp
    order_id: str = ""


class SmartPolymarketBot:
    """Directional trading bot using green/yellow-light strategies.

    Cycle: scan markets -> evaluate strategies -> risk-check -> execute best -> monitor exits.
    Runs on 30-second cycles — conservative, edge-driven, not high-frequency.
    """

    def __init__(self, cfg: dict) -> None:
        self._cfg = cfg
        pm_cfg = cfg.get("polymarket", {})

        self._paper_mode = pm_cfg.get("paper_mode", True)
        self._cycle_interval = pm_cfg.get("cycle_interval_seconds", 30)
        self._scan_interval = pm_cfg.get("scan_interval_seconds", 300)
        self._report_interval = pm_cfg.get("report_interval_seconds", 300)
        self._max_trades_per_cycle = pm_cfg.get("max_trades_per_cycle", 2)

        # exit parameters
        exit_cfg = pm_cfg.get("exits", {})
        self._max_hold_seconds = exit_cfg.get("max_hold_seconds", 3600)  # 1 hour
        self._stop_loss_multiplier = exit_cfg.get("stop_loss_multiplier", 2.0)  # 2x edge

        # components
        self._client = PolymarketClient(cfg)
        self._risk = PolymarketRiskManager(cfg)
        self._paper = PaperExecutor(cfg) if self._paper_mode else None
        self._alerts = AlertManager(cfg)

        # strategies
        self._mean_reversion = MeanReversionStrategy(cfg)
        self._edge_strategy = EdgeStrategy(cfg)

        # state
        self._state = BotState()
        self._last_scan_ts = 0.0
        self._last_report_ts = 0.0
        self._active_markets: list[Market] = []
        self._positions: dict[str, TrackedPosition] = {}  # token_id -> position
        self._start_time = 0.0
        self._daily_pnl = 0.0
        self._total_trades = 0
        self._winning_trades = 0

        # daily reset tracking
        self._last_reset_day = ""

        # alert rate limiting
        self._alert_rate_limits: dict[str, float] = {
            "trade": 30.0,
            "exit": 0.0,
            "circuit_breaker": 0.0,
            "report": 0.0,
            "error": 60.0,
        }
        self._last_alert_ts: dict[str, float] = {}

        log.info(
            f"SmartPolymarketBot initialized: mode={'PAPER' if self._paper_mode else 'LIVE'}, "
            f"cycle={self._cycle_interval}s, max_trades/cycle={self._max_trades_per_cycle}"
        )

    def run(self) -> None:
        """Main loop — scan, evaluate, trade, monitor."""
        self._start_time = time.monotonic()

        log.info("=" * 60)
        log.info("Smart Polymarket Bot Starting")
        log.info(f"Mode: {'PAPER' if self._paper_mode else 'LIVE'}")
        log.info("Strategy: directional edge (mean reversion + model edge)")
        log.info(f"Cycle interval: {self._cycle_interval}s")
        log.info(f"Max trades/cycle: {self._max_trades_per_cycle}")
        log.info(f"Max hold time: {self._max_hold_seconds}s")
        log.info(f"Stop loss: {self._stop_loss_multiplier}x edge")
        log.info("=" * 60)

        if self._paper_mode and self._paper:
            log.info(f"Paper balance: ${self._paper.get_balance():.2f}")

        mode = "PAPER" if self._paper_mode else "LIVE"
        self._alert(
            f"Smart Bot Started [{mode}]\n"
            f"Strategies: mean_reversion + edge\n"
            f"Cycle: {self._cycle_interval}s | Max trades/cycle: {self._max_trades_per_cycle}",
            alert_type="report",
        )

        if not self._paper_mode:
            self._client.start_heartbeat()

        try:
            while not self._state.halted:
                try:
                    self._cycle()
                except Exception as e:
                    log.error(f"Cycle error: {e}")
                    log.debug(traceback.format_exc())
                    self._state.errors_today += 1
                    if self._state.errors_today > 10:
                        self._alert(
                            f"Error count critical: {self._state.errors_today}\n{e}",
                            alert_type="error",
                            level="error",
                        )
                time.sleep(self._cycle_interval)
        except KeyboardInterrupt:
            log.info("Stopped by user (Ctrl+C)")
        finally:
            self._shutdown()

    def _cycle(self) -> None:
        """One cycle: scan -> evaluate -> risk-check -> execute -> monitor exits -> report."""
        self._state.cycle_count += 1
        now = time.monotonic()

        # midnight UTC reset
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._last_reset_day:
            prev_pnl = self._daily_pnl
            prev_trades = self._state.trades_today
            self._daily_pnl = 0.0
            self._state.trades_today = 0
            self._state.errors_today = 0
            self._last_reset_day = today
            log.info(f"Daily reset: {today}")
            self._alert(
                f"Daily Reset: {today}\n"
                f"Yesterday PnL: ${prev_pnl:+.4f} | Trades: {prev_trades}",
                alert_type="report",
            )

        # circuit breaker check
        cb = self._risk.check_circuit_breakers(self._state, self._daily_pnl)
        if not cb.passed:
            self._alert(
                f"CIRCUIT BREAKER: {cb.reason}\nDaily PnL: ${self._daily_pnl:+.2f}",
                alert_type="circuit_breaker",
                level="warning",
            )
            return

        # periodic market scan
        if now - self._last_scan_ts >= self._scan_interval:
            self._scan_markets()
            self._last_scan_ts = now

        # evaluate strategies across all markets
        if self._active_markets:
            opportunities = self._evaluate_strategies(self._active_markets)

            # risk-check and execute top opportunities
            trades_this_cycle = 0
            for opp in opportunities:
                if trades_this_cycle >= self._max_trades_per_cycle:
                    break

                # skip markets we already have a position in
                token_id = (
                    opp.market.yes_token_id
                    if opp.outcome == Outcome.YES
                    else opp.market.no_token_id
                )
                if token_id in self._positions:
                    continue

                balance = self._paper.get_balance() if self._paper_mode and self._paper else 0
                exposure = self._get_total_exposure()

                check = self._risk.check_opportunity(opp, self._state, exposure, balance)
                if not check.passed:
                    log.debug(f"Risk rejected: {opp.market.question[:40]} — {check.reason}")
                    continue

                if self._execute_opportunity(opp):
                    trades_this_cycle += 1

        # monitor exits for open positions
        self._check_exits()

        # periodic status report
        if now - self._last_report_ts >= self._report_interval:
            self._report()
            self._last_report_ts = now

    def _scan_markets(self) -> None:
        """Fetch active markets from Polymarket."""
        try:
            self._active_markets = self._client.get_markets(
                active=True,
                limit=200,
                min_liquidity=5000,
            )
            log.info(f"Scan: {len(self._active_markets)} active markets")
        except Exception as e:
            log.error(f"Market scan failed: {e}")
            self._state.errors_today += 1

    def _evaluate_strategies(self, markets: list[Market]) -> list[Opportunity]:
        """Run all active strategies and merge results.

        Returns opportunities sorted by edge * kelly (expected value), deduplicated
        so each market appears at most once (keeping the highest-edge opportunity).
        """
        all_opps: list[Opportunity] = []

        # green light: mean reversion (always on)
        try:
            mr_opps = self._mean_reversion.evaluate(markets)
            all_opps.extend(mr_opps)
            if mr_opps:
                log.info(f"Mean reversion: {len(mr_opps)} opportunities")
        except Exception as e:
            log.error(f"Mean reversion strategy error: {e}")

        # yellow light: edge strategy (needs model_probs)
        try:
            edge_opps = self._edge_strategy.evaluate(markets)
            all_opps.extend(edge_opps)
            if edge_opps:
                log.info(f"Edge strategy: {len(edge_opps)} opportunities")
        except Exception as e:
            log.error(f"Edge strategy error: {e}")

        # deduplicate: keep highest edge per market condition_id
        best_by_market: dict[str, Opportunity] = {}
        for opp in all_opps:
            key = opp.market.condition_id
            if key not in best_by_market or opp.edge > best_by_market[key].edge:
                best_by_market[key] = opp

        # sort by edge * kelly (expected value ranking)
        ranked = sorted(
            best_by_market.values(),
            key=lambda o: o.edge * o.kelly_fraction,
            reverse=True,
        )

        if ranked:
            log.info(
                f"Evaluated: {len(all_opps)} raw -> {len(ranked)} unique opportunities | "
                f"best edge={ranked[0].edge:.1%}"
            )

        return ranked

    def _execute_opportunity(self, opp: Opportunity) -> bool:
        """Size, place, and track a trade for one opportunity.

        Returns True if a fill occurred, False otherwise.
        """
        balance = self._paper.get_balance() if self._paper_mode and self._paper else 0
        exposure = self._get_total_exposure()

        size = self._risk.size_position(opp, balance, exposure)
        if size <= 0:
            log.debug(f"Size zero for {opp.market.question[:40]}")
            return False

        token_id = (
            opp.market.yes_token_id
            if opp.outcome == Outcome.YES
            else opp.market.no_token_id
        )
        price = opp.market_prob  # buy at current market price

        log.info(
            f"TRADE: {opp.outcome.value} {opp.market.question[:50]} | "
            f"price={price:.3f} size=${size:.2f} edge={opp.edge:.1%} "
            f"kelly={opp.kelly_fraction:.1%}"
        )

        result = self._place(token_id, Side.BUY, price, size, opp.market)

        if result and result.success and result.filled_size > 0:
            self._state.trades_today += 1
            self._total_trades += 1

            # track position for exit monitoring
            self._positions[token_id] = TrackedPosition(
                market=opp.market,
                outcome=opp.outcome,
                token_id=token_id,
                entry_price=result.avg_fill_price or price,
                size=result.filled_size,
                edge_at_entry=opp.edge,
                entered_at=time.monotonic(),
                order_id=result.order_id,
            )

            self._alert(
                f"FILL: {opp.outcome.value} {opp.market.question[:40]}\n"
                f"Price: {price:.3f} | Size: ${size:.2f} | Edge: {opp.edge:.1%}\n"
                f"Reason: {opp.reason}",
            )
            return True

        if result and not result.success:
            log.warning(f"Order failed: {result.error}")
            self._state.errors_today += 1

        return False

    def _check_exits(self) -> None:
        """Check all open positions for exit conditions.

        Exit triggers:
        1. Price moved to target (edge captured)
        2. Price moved against us past stop loss (2x edge by default)
        3. Position held too long (time-based exit)
        """
        if not self._positions:
            return

        now = time.monotonic()
        to_exit: list[tuple[str, str]] = []  # (token_id, reason)

        # refresh market prices for position checking
        market_prices = self._get_current_prices()

        for token_id, pos in self._positions.items():
            current_price = market_prices.get(pos.market.condition_id)
            if current_price is None:
                continue

            # adjust price for YES vs NO
            if pos.outcome == Outcome.NO:
                current_price = 1.0 - current_price

            pnl_pct = (current_price - pos.entry_price) / pos.entry_price if pos.entry_price > 0 else 0
            hold_time = now - pos.entered_at

            # 1. target hit: price moved in our favor by at least the edge
            if current_price >= pos.entry_price + pos.edge_at_entry:
                to_exit.append((token_id, f"TARGET: +{pnl_pct:.1%} ({hold_time:.0f}s)"))
                continue

            # 2. stop loss: price moved against us by 2x edge
            stop_distance = pos.edge_at_entry * self._stop_loss_multiplier
            if current_price <= pos.entry_price - stop_distance:
                to_exit.append((token_id, f"STOP: {pnl_pct:+.1%} ({hold_time:.0f}s)"))
                continue

            # 3. time-based exit
            if hold_time >= self._max_hold_seconds:
                to_exit.append((token_id, f"TIMEOUT: {pnl_pct:+.1%} ({hold_time:.0f}s)"))
                continue

        # execute exits
        for token_id, reason in to_exit:
            self._execute_exit(token_id, reason)

    def _execute_exit(self, token_id: str, reason: str) -> None:
        """Close a position."""
        pos = self._positions.get(token_id)
        if not pos:
            return

        log.info(f"EXIT [{reason}]: {pos.outcome.value} {pos.market.question[:40]}")

        # sell at current price (slight discount for paper to simulate slippage)
        sell_price = pos.entry_price * 0.99 if self._paper_mode else pos.entry_price

        result = self._place(token_id, Side.SELL, sell_price, pos.size, pos.market)

        if result and result.success and result.filled_size > 0:
            fill_price = result.avg_fill_price or sell_price
            pnl = (fill_price - pos.entry_price) * result.filled_size
            self._daily_pnl += pnl

            if pnl > 0:
                self._winning_trades += 1

            del self._positions[token_id]

            self._alert(
                f"EXIT [{reason}]\n"
                f"{pos.outcome.value} {pos.market.question[:40]}\n"
                f"PnL: ${pnl:+.4f} | Entry: {pos.entry_price:.3f} -> {fill_price:.3f}",
                alert_type="exit",
            )
        else:
            log.warning(f"Exit failed for {token_id}: {result.error if result else 'no result'}")

    def _get_current_prices(self) -> dict[str, float]:
        """Get current YES prices for markets we hold positions in.

        Returns condition_id -> yes_price mapping.
        """
        prices: dict[str, float] = {}
        condition_ids = {pos.market.condition_id for pos in self._positions.values()}

        for market in self._active_markets:
            if market.condition_id in condition_ids:
                prices[market.condition_id] = market.yes_price

        return prices

    def _place(
        self,
        token_id: str,
        side: Side,
        price: float,
        size: float,
        market: Optional[Market] = None,
    ):
        """Place an order through paper or live executor."""
        if self._paper_mode and self._paper:
            return self._paper.place_order(token_id, side, price, size, market)
        else:
            return self._client.place_order(token_id, side, price, size)

    def _get_total_exposure(self) -> float:
        """Sum of all open position costs."""
        return sum(p.entry_price * p.size for p in self._positions.values())

    def _report(self) -> None:
        """Log a status report."""
        uptime = time.monotonic() - self._start_time
        hours = uptime / 3600
        win_rate = self._winning_trades / self._total_trades if self._total_trades > 0 else 0

        bal = self._paper.get_balance() if self._paper_mode and self._paper else 0
        equity = self._paper.get_equity() if self._paper_mode and self._paper else 0
        exposure = self._get_total_exposure()

        log.info("-" * 50)
        log.info(f"STATUS REPORT (uptime {hours:.1f}h)")
        log.info(f"  Mode:       {'PAPER' if self._paper_mode else 'LIVE'}")
        log.info(f"  Balance:    ${bal:.2f}")
        log.info(f"  Equity:     ${equity:.2f}")
        log.info(f"  Daily PnL:  ${self._daily_pnl:+.4f}")
        log.info(f"  Exposure:   ${exposure:.2f}")
        log.info(f"  Positions:  {len(self._positions)}")
        log.info(f"  Trades:     {self._total_trades} (win rate: {win_rate:.0%})")
        log.info(f"  Cycles:     {self._state.cycle_count}")
        log.info(f"  Errors:     {self._state.errors_today}")

        for token_id, pos in self._positions.items():
            hold_time = time.monotonic() - pos.entered_at
            log.info(
                f"    {pos.outcome.value} {pos.market.question[:40]} | "
                f"entry={pos.entry_price:.3f} size={pos.size:.1f} "
                f"edge={pos.edge_at_entry:.1%} hold={hold_time:.0f}s"
            )
        log.info("-" * 50)

        self._alert(
            f"Status ({hours:.1f}h)\n"
            f"PnL: ${self._daily_pnl:+.4f} | Trades: {self._total_trades} ({win_rate:.0%})\n"
            f"Positions: {len(self._positions)} | Exposure: ${exposure:.2f} | "
            f"Errors: {self._state.errors_today}",
            alert_type="report",
        )

    def _alert(self, msg: str, alert_type: str = "trade", level: str = "info") -> None:
        """Send a Telegram alert with rate limiting per alert type."""
        now = time.monotonic()
        limit = self._alert_rate_limits.get(alert_type, 30.0)
        last = self._last_alert_ts.get(alert_type, 0.0)
        if limit > 0 and (now - last) < limit:
            return
        self._last_alert_ts[alert_type] = now
        self._alerts.send(msg, level=level)

    def _shutdown(self) -> None:
        """Clean shutdown with final report."""
        log.info("Shutting down...")

        if not self._paper_mode:
            self._client.stop_heartbeat()
            self._client.cancel_all()

        self._report()

        if self._paper_mode and self._paper:
            log.info(self._paper.summary())

        uptime = (time.monotonic() - self._start_time) / 3600 if self._start_time else 0
        self._alert(
            f"Smart Bot Shutdown\n"
            f"Uptime: {uptime:.1f}h | Trades: {self._total_trades} | "
            f"PnL: ${self._daily_pnl:+.4f} | Positions: {len(self._positions)}",
            alert_type="report",
        )

    def set_model_probs(self, probs: dict[str, float]) -> None:
        """Feed in external probability estimates for the edge strategy.

        Args:
            probs: Mapping of condition_id -> model's estimated YES probability.
                   This enables/updates the EdgeStrategy (yellow light).
        """
        self._edge_strategy.set_model_probs(probs)
        log.info(f"Model probs updated: {len(probs)} markets")

    def status(self) -> dict:
        """Return current bot status as a dict."""
        win_rate = self._winning_trades / self._total_trades if self._total_trades > 0 else 0
        return {
            "mode": "paper" if self._paper_mode else "live",
            "uptime_hours": round((time.monotonic() - self._start_time) / 3600, 2) if self._start_time else 0,
            "cycle_count": self._state.cycle_count,
            "trades_today": self._state.trades_today,
            "total_trades": self._total_trades,
            "win_rate": round(win_rate, 4),
            "daily_pnl": round(self._daily_pnl, 4),
            "total_exposure": round(self._get_total_exposure(), 2),
            "open_positions": len(self._positions),
            "active_markets": len(self._active_markets),
            "errors_today": self._state.errors_today,
            "halted": self._state.halted,
        }
