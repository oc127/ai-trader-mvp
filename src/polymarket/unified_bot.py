"""Unified Polymarket bot — market making + directional edge, two legs walking.

Combines two strategy layers:
Layer 1 (做市 — steady income): Quote both sides, earn spread, auto-flatten stale inventory
Layer 2 (找 edge — alpha capture): AI + mean reversion find mispriced markets, directional bets

The maker runs every 5 seconds (fast cycle). Edge evaluation runs every 30 seconds (slow cycle).
Both layers share the same risk budget and paper/live executor.
"""

from __future__ import annotations

import time
import traceback
from datetime import datetime, timezone
from typing import Optional

from src.logger import get_logger
from src.monitor.alerts import AlertManager
from src.polymarket.arbitrage import ArbitrageEngine
from src.polymarket.client import PolymarketClient
from src.polymarket.copy_trader import CopyTrader
from src.polymarket.market_maker import HighFreqMarketMaker, QuotePair
from src.polymarket.paper import PaperExecutor
from src.polymarket.risk import PolymarketRiskManager
from src.polymarket.strategy import EdgeStrategy, MeanReversionStrategy
from src.polymarket.types import BotState, Market, Opportunity, Outcome, Side

log = get_logger(__name__)


class UnifiedPolymarketBot:
    """Four-layer bot:
    Layer 1 (maker, 5s): Quote both sides, earn spread, auto-flatten stale inventory
    Layer 2 (edge, 30s): AI + mean reversion find mispriced markets, directional bets
    Layer 3 (arb, 30s): Complete-set arbitrage + resolution sniping
    Layer 4 (copy, 120s): Smart money copy trading — mirror top performers
    """

    def __init__(self, cfg: dict, ai_analyzer=None) -> None:
        self._cfg = cfg
        pm_cfg = cfg.get("polymarket", {})

        self._paper_mode = pm_cfg.get("paper_mode", True)

        # fast cycle (market making)
        self._fast_interval = pm_cfg.get("cycle_interval_seconds", 5)
        self._scan_interval = pm_cfg.get("scan_interval_seconds", 60)
        self._report_interval = pm_cfg.get("report_interval_seconds", 300)

        # slow cycle (edge detection)
        smart_cfg = cfg.get("smart_bot", {})
        self._slow_interval = smart_cfg.get("cycle_interval_seconds", 30)
        self._max_edge_trades = smart_cfg.get("max_trades_per_cycle", 2)
        self._max_hold_seconds = smart_cfg.get("position_timeout_seconds", 3600)
        self._stop_loss_mult = smart_cfg.get("stop_loss_multiplier", 2.0)

        # enable/disable layers
        self._maker_enabled = pm_cfg.get("maker_enabled", True)
        self._edge_enabled = pm_cfg.get("edge_enabled", True)
        self._arb_enabled = pm_cfg.get("arb_enabled", True)
        self._copy_enabled = pm_cfg.get("copy_enabled", False)

        # shared components
        self._client = PolymarketClient(cfg)
        self._paper = PaperExecutor(cfg) if self._paper_mode else None
        self._alerts = AlertManager(cfg)

        # layer 1: market maker
        self._maker = HighFreqMarketMaker(cfg)
        self._active_quotes: dict[str, QuotePair] = {}

        # layer 2: edge strategies
        self._risk = PolymarketRiskManager(cfg)
        self._mean_reversion = MeanReversionStrategy(cfg)
        self._edge_strategy = EdgeStrategy(cfg)
        self._ai_analyzer = ai_analyzer
        self._edge_positions: dict[str, _EdgePosition] = {}

        # layer 3: arbitrage
        self._arb_engine = ArbitrageEngine(cfg)
        self._last_arb_ts = 0.0

        # layer 4: copy trading
        self._copy_trader = CopyTrader(cfg)
        self._last_copy_scan_ts = 0.0

        # shared state
        self._state = BotState()
        self._active_markets: list[Market] = []  # filtered for maker
        self._all_markets: list[Market] = []  # unfiltered for arb/snipe/edge
        self._start_time = 0.0
        self._last_scan_ts = 0.0
        self._last_report_ts = 0.0
        self._last_slow_ts = 0.0
        self._last_reset_day = ""
        self._last_fill_check_ts = 0.0
        self._known_orders: dict[str, dict] = {}  # order_id -> {token_id, side, price, size, cid}
        self._committed_usd = 0.0  # USDC locked in resting orders

        # position management
        self._held_positions: dict[str, _HeldPosition] = {}  # token_id -> position
        self._last_position_scan_ts = 0.0
        self._position_scan_interval = 300.0  # check every 5 min
        self._positions_discovered = False

        # PnL tracking
        self._maker_pnl = 0.0
        self._edge_pnl = 0.0
        self._arb_pnl = 0.0
        self._copy_pnl = 0.0

        # alert rate limiting
        self._alert_limits = {
            "trade": 30.0, "flatten": 0.0, "circuit_breaker": 0.0,
            "report": 0.0, "error": 60.0, "edge": 0.0,
            "arb": 0.0, "copy": 0.0,
        }
        self._last_alert_ts: dict[str, float] = {}

        # initialize risk manager with capital
        if self._paper_mode and self._paper:
            self._risk.set_initial_capital(self._paper.get_balance())

        layers = []
        if self._maker_enabled:
            layers.append("maker")
        if self._edge_enabled:
            layers.append("edge")
        if self._arb_enabled:
            layers.append("arb")
        if self._copy_enabled:
            layers.append("copy")
        log.info(
            f"UnifiedBot initialized: mode={'PAPER' if self._paper_mode else 'LIVE'}, "
            f"layers=[{', '.join(layers)}], fast={self._fast_interval}s, slow={self._slow_interval}s"
        )

    def run(self) -> None:
        self._start_time = time.monotonic()

        log.info("=" * 60)
        log.info("Unified Polymarket Bot Starting")
        log.info(f"Mode: {'PAPER' if self._paper_mode else 'LIVE'}")
        log.info(f"Layer 1 (Maker):  {'ON' if self._maker_enabled else 'OFF'} — {self._fast_interval}s cycle")
        log.info(f"Layer 2 (Edge):   {'ON' if self._edge_enabled else 'OFF'} — {self._slow_interval}s cycle")
        log.info(f"Layer 3 (Arb):    {'ON' if self._arb_enabled else 'OFF'} — {self._slow_interval}s cycle")
        copy_int = self._copy_trader.config.scan_interval
        log.info(f"Layer 4 (Copy):   {'ON' if self._copy_enabled else 'OFF'} — {copy_int}s cycle")
        log.info(f"Max exposure: ${self._maker.config.max_total_exposure}")
        log.info(f"Max loss/day: ${self._maker.config.max_daily_loss}")
        log.info("=" * 60)

        if self._paper_mode and self._paper:
            log.info(f"Paper balance: ${self._paper.get_balance():.2f}")

        mode = "PAPER" if self._paper_mode else "LIVE"
        self._alert(
            f"Unified Bot Started [{mode}]\n"
            f"Maker: {'ON' if self._maker_enabled else 'OFF'} | Edge: {'ON' if self._edge_enabled else 'OFF'} "
            f"| Arb: {'ON' if self._arb_enabled else 'OFF'} | Copy: {'ON' if self._copy_enabled else 'OFF'}\n"
            f"Max exposure: ${self._maker.config.max_total_exposure}",
            alert_type="report",
        )

        if not self._paper_mode:
            self._client.start_heartbeat()
            self._discover_positions()

        try:
            while not self._state.halted:
                self._fast_cycle()
                time.sleep(self._fast_interval)
        except KeyboardInterrupt:
            log.info("Stopped by user (Ctrl+C)")
        except Exception:
            log.exception("Bot crashed")
            raise
        finally:
            self._shutdown()

    def stop(self) -> None:
        self._state.halted = True
        self._state.halt_reason = "Manual stop"

    # ── Fast Cycle (every 5s) — market making ──

    def _fast_cycle(self) -> None:
        self._state.cycle_count += 1
        now = time.monotonic()

        # midnight UTC reset
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._last_reset_day:
            prev_maker = self._maker_pnl
            prev_edge = self._edge_pnl
            prev_arb = self._arb_pnl
            prev_copy = self._copy_pnl
            prev_trades = self._state.trades_today
            self._maker.reset_daily()
            self._arb_engine.reset_daily()
            self._copy_trader.reset_daily()
            self._risk.reset_daily()
            self._maker_pnl = 0.0
            self._edge_pnl = 0.0
            self._arb_pnl = 0.0
            self._copy_pnl = 0.0
            self._state.trades_today = 0
            self._state.errors_today = 0
            self._last_reset_day = today
            log.info(f"Daily reset: {today}")
            self._alert(
                f"Daily Reset: {today}\n"
                f"Yesterday — Maker: ${prev_maker:+.4f} | Edge: ${prev_edge:+.4f} "
                f"| Arb: ${prev_arb:+.4f} | Copy: ${prev_copy:+.4f} | Trades: {prev_trades}",
                alert_type="report",
            )

        # pause check (maker layer)
        if self._maker.is_paused:
            if not getattr(self, "_pause_alerted", False):
                self._pause_alerted = True
                log.warning(f"PAUSED — maker PnL: ${self._maker.daily_pnl:+.2f}")
                self._alert(
                    f"CIRCUIT BREAKER: Maker paused\nDaily PnL: ${self._maker.daily_pnl:+.2f}",
                    alert_type="circuit_breaker",
                    level="warning",
                )
        else:
            self._pause_alerted = False

        # periodic market scan
        if now - self._last_scan_ts >= self._scan_interval:
            self._scan_markets()
            self._last_scan_ts = now

        # Layer 1: market making quotes (fast)
        if self._maker_enabled and not self._maker.is_paused:
            cycle_balance = self._client.get_balance() if not self._paper_mode else (self._paper.get_balance() if self._paper else 0)
            for market in self._active_markets:
                try:
                    self._quote_market(market, cycle_balance)
                except Exception as e:
                    log.error(f"Quote failed for {market.question[:40]}: {e}")
                    self._state.errors_today += 1

            self._flatten_stale()
            self._merge_positions()

        # Check for deferred fills on resting orders (every 10s)
        if not self._paper_mode and now - self._last_fill_check_ts >= 10:
            try:
                self._check_maker_fills()
            except Exception as e:
                log.debug(f"Fill check error: {e}")
            self._last_fill_check_ts = now

        # Layer 2: edge detection (slow — only every slow_interval)
        if self._edge_enabled and now - self._last_slow_ts >= self._slow_interval:
            try:
                self._slow_cycle()
            except Exception as e:
                log.error(f"Edge cycle error: {e}")
                log.debug(traceback.format_exc())
                self._state.errors_today += 1
            self._last_slow_ts = now

        # Layer 3: arbitrage scanning (runs on slow_interval)
        if self._arb_enabled and now - self._last_arb_ts >= self._slow_interval:
            try:
                self._arb_cycle()
            except Exception as e:
                log.error(f"Arb cycle error: {e}")
                self._state.errors_today += 1
            self._last_arb_ts = now

        # Layer 4: copy trading (runs on its own interval)
        copy_interval = self._copy_trader.config.scan_interval
        if self._copy_enabled and now - self._last_copy_scan_ts >= copy_interval:
            try:
                self._copy_cycle()
            except Exception as e:
                log.error(f"Copy cycle error: {e}")
                self._state.errors_today += 1
            self._last_copy_scan_ts = now

        # Layer 5: position management (runs every 5 min)
        if not self._paper_mode and now - self._last_position_scan_ts >= self._position_scan_interval:
            try:
                self._manage_positions()
            except Exception as e:
                log.error(f"Position mgmt error: {e}")
                self._state.errors_today += 1
            self._last_position_scan_ts = now

        # update equity for risk tracking
        if self._paper_mode and self._paper:
            self._risk.update_equity(self._paper.get_equity())

        # error alert
        if self._state.errors_today > 5:
            self._alert(f"Error count high: {self._state.errors_today}", alert_type="error", level="error")

        # periodic report
        if now - self._last_report_ts >= self._report_interval:
            self._report()
            self._last_report_ts = now

    # ── Slow Cycle (every 30s) — edge detection ──

    def _slow_cycle(self) -> None:
        if not self._active_markets and not self._all_markets:
            return

        # feed AI probabilities if analyzer is available
        if self._ai_analyzer:
            try:
                analyses = self._ai_analyzer.find_opportunities(self._active_markets)
                if analyses:
                    probs = {}
                    for opp in analyses:
                        probs[opp.market.condition_id] = opp.model_prob
                    self._edge_strategy.set_model_probs(probs)
                    log.info(f"AI analyzer fed {len(probs)} probability estimates")
            except Exception as e:
                log.error(f"AI analyzer error: {e}")

        # evaluate all edge strategies
        edge_markets = self._all_markets or self._active_markets
        opportunities = self._evaluate_edge(edge_markets)

        # execute top opportunities
        trades_this_cycle = 0
        for opp in opportunities:
            if trades_this_cycle >= self._max_edge_trades:
                break

            token_id = opp.market.yes_token_id if opp.outcome == Outcome.YES else opp.market.no_token_id
            if token_id in self._edge_positions:
                continue

            balance = self._paper.get_balance() if self._paper_mode and self._paper else self._client.get_balance()
            exposure = self._get_edge_exposure()
            check = self._risk.check_opportunity(opp, self._state, exposure, balance)
            if not check.passed:
                log.debug(f"Risk rejected: {opp.market.question[:40]} — {check.reason}")
                continue

            if self._execute_edge_trade(opp):
                trades_this_cycle += 1

        # check exits on existing edge positions
        self._check_edge_exits()

    def _evaluate_edge(self, markets: list[Market]) -> list[Opportunity]:
        all_opps: list[Opportunity] = []

        try:
            mr_opps = self._mean_reversion.evaluate(markets)
            all_opps.extend(mr_opps)
        except Exception as e:
            log.error(f"Mean reversion error: {e}")

        try:
            edge_opps = self._edge_strategy.evaluate(markets)
            all_opps.extend(edge_opps)
        except Exception as e:
            log.error(f"Edge strategy error: {e}")

        # deduplicate: keep highest edge per market
        best: dict[str, Opportunity] = {}
        for opp in all_opps:
            key = opp.market.condition_id
            if key not in best or opp.edge > best[key].edge:
                best[key] = opp

        ranked = sorted(best.values(), key=lambda o: o.edge * o.kelly_fraction, reverse=True)
        if ranked:
            log.info(f"Edge scan: {len(all_opps)} raw → {len(ranked)} unique | best edge={ranked[0].edge:.1%}")
        return ranked

    def _execute_edge_trade(self, opp: Opportunity) -> bool:
        balance = self._paper.get_balance() if self._paper_mode and self._paper else self._client.get_balance()
        exposure = self._get_edge_exposure()
        size = self._risk.size_position(opp, balance, exposure)
        if size <= 0:
            return False

        token_id = opp.market.yes_token_id if opp.outcome == Outcome.YES else opp.market.no_token_id
        price = opp.market_prob

        if price <= 0:
            return False
        shares = max(size / price, 5.0)
        shares = round(shares, 2)

        cost = shares * price
        if cost > balance * 0.95:
            log.debug(f"Edge skip: cost=${cost:.2f} > balance=${balance:.2f}")
            return False

        log.info(
            f"EDGE TRADE: {opp.outcome.value} {opp.market.question[:50]} | "
            f"price={price:.3f} size=${size:.2f} shares={shares:.2f} edge={opp.edge:.1%}"
        )

        result = self._place(token_id, Side.BUY, price, shares, opp.market)
        if result and result.success and result.filled_size > 0:
            self._state.trades_today += 1
            self._edge_positions[token_id] = _EdgePosition(
                market=opp.market, outcome=opp.outcome, token_id=token_id,
                entry_price=result.avg_fill_price or price,
                size=result.filled_size, edge=opp.edge, entered_at=time.monotonic(),
            )
            self._alert(
                f"EDGE FILL: {opp.outcome.value} {opp.market.question[:40]}\n"
                f"Price: {price:.3f} | Size: ${size:.2f} | Edge: {opp.edge:.1%}",
                alert_type="edge",
            )
            return True
        return False

    def _check_edge_exits(self) -> None:
        if not self._edge_positions:
            return

        now = time.monotonic()
        prices = self._get_current_prices()
        to_exit: list[tuple[str, str]] = []

        for token_id, pos in self._edge_positions.items():
            current = prices.get(pos.market.condition_id)
            if current is None:
                continue
            if pos.outcome == Outcome.NO:
                current = 1.0 - current

            hold = now - pos.entered_at

            if current >= pos.entry_price + pos.edge:
                to_exit.append((token_id, f"TARGET +{(current - pos.entry_price) / pos.entry_price:.1%}"))
            elif current <= pos.entry_price - pos.edge * self._stop_loss_mult:
                to_exit.append((token_id, f"STOP {(current - pos.entry_price) / pos.entry_price:+.1%}"))
            elif hold >= self._max_hold_seconds:
                to_exit.append((token_id, f"TIMEOUT {hold:.0f}s"))

        for token_id, reason in to_exit:
            pos = self._edge_positions.get(token_id)
            if not pos:
                continue
            sell_price = pos.entry_price * 0.99 if self._paper_mode else pos.entry_price
            result = self._place(token_id, Side.SELL, sell_price, pos.size, pos.market)
            if result and result.success and result.filled_size > 0:
                fill = result.avg_fill_price or sell_price
                pnl = (fill - pos.entry_price) * result.filled_size
                self._edge_pnl += pnl
                del self._edge_positions[token_id]
                log.info(f"EDGE EXIT [{reason}]: PnL=${pnl:+.4f}")
                self._alert(
                    f"EDGE EXIT [{reason}]\n{pos.outcome.value} {pos.market.question[:40]}\nPnL: ${pnl:+.4f}",
                    alert_type="edge",
                )

    # ── Arbitrage (Layer 3) ──

    def _arb_cycle(self) -> None:
        markets = self._all_markets or self._active_markets
        if not markets:
            return

        opps = self._arb_engine.scan_all(markets)
        if not opps:
            return

        balance = self._paper.get_balance() if self._paper_mode and self._paper else self._client.get_balance()

        for opp in opps[:3]:
            if opp.arb_type == "complete_set":
                usd_size = min(opp.net_profit * 100, self._arb_engine.config.max_arb_size_usd)
                if usd_size < 2.0:
                    continue
                total_cost_per_share = opp.yes_cost + opp.no_cost
                if total_cost_per_share <= 0:
                    continue
                shares = max(usd_size / total_cost_per_share, 5.0)
                shares = round(shares, 2)
                cost = shares * total_cost_per_share
                if cost > balance * 0.95:
                    log.debug(f"Arb skip: cost=${cost:.2f} > balance=${balance:.2f}")
                    continue
                log.info(
                    f"ARB: {opp.market.question[:40]} | cost={opp.total_cost:.3f} "
                    f"shares={shares:.2f} net=${opp.net_profit:.4f} roi={opp.roi_pct:.2f}%"
                )
                buy_yes = self._place(
                    opp.market.yes_token_id, Side.BUY, opp.yes_cost, shares, opp.market,
                )
                buy_no = self._place(
                    opp.market.no_token_id, Side.BUY, opp.no_cost, shares, opp.market,
                )
                if buy_yes and buy_yes.success and buy_no and buy_no.success:
                    filled = min(buy_yes.filled_size, buy_no.filled_size)
                    self._arb_engine.record_fill(opp, filled, opp.total_cost)
                    pnl = opp.net_profit * filled
                    self._arb_pnl += pnl
                    self._risk.record_trade_result(pnl)
                    self._state.trades_today += 2
                    self._alert(
                        f"ARB FILL: {opp.market.question[:40]}\n"
                        f"ROI: {opp.roi_pct:.2f}% | Net: ${pnl:+.4f}",
                        alert_type="arb",
                    )
            elif opp.arb_type == "resolution_snipe":
                usd_size = min(20.0, self._arb_engine.config.max_arb_size_usd)
                token_id = (
                    opp.market.yes_token_id if opp.snipe_side == "YES"
                    else opp.market.no_token_id
                )
                price = opp.yes_cost if opp.snipe_side == "YES" else opp.no_cost
                if price <= 0:
                    continue
                shares = max(usd_size / price, 5.0)
                shares = round(shares, 2)
                cost = shares * price
                if cost > balance * 0.95:
                    log.debug(f"Snipe skip: cost=${cost:.2f} > balance=${balance:.2f}")
                    continue
                log.info(
                    f"SNIPE: {opp.snipe_side} {opp.market.question[:40]} "
                    f"@ {price:.3f} size=${usd_size:.2f} shares={shares:.2f} net=${opp.net_profit:.4f}"
                )
                result = self._place(token_id, Side.BUY, price, shares, opp.market)
                if result and result.success and result.filled_size > 0:
                    self._arb_engine.record_fill(opp, result.filled_size, price)
                    pnl = opp.net_profit * result.filled_size
                    self._arb_pnl += pnl
                    self._risk.record_trade_result(pnl)
                    self._state.trades_today += 1
                    self._alert(
                        f"SNIPE FILL: {opp.snipe_side} {opp.market.question[:40]}\n"
                        f"@ {price:.3f} | Net: ${pnl:+.4f}",
                        alert_type="arb",
                    )

    # ── Copy Trading (Layer 4) ──

    def _copy_cycle(self) -> None:
        traders = self._copy_trader.fetch_leaderboard()
        if not traders:
            return

        qualified = self._copy_trader.filter_traders(traders)
        self._copy_trader.update_followed(qualified)

        balance = self._paper.get_balance() if self._paper_mode and self._paper else self._client.get_balance()

        valid_tokens: set[str] = set()
        for m in self._active_markets:
            valid_tokens.add(m.yes_token_id)
            valid_tokens.add(m.no_token_id)

        for t in qualified:
            if not self._copy_trader.should_copy(t.address):
                continue

            trades = self._copy_trader.fetch_trader_trades(t.address)
            new_trades = self._copy_trader.detect_new_trades(t.address, trades)

            for trade in new_trades[:2]:
                token_id = trade.get("asset", trade.get("asset_id", trade.get("tokenId", "")))
                if not token_id or token_id not in valid_tokens:
                    continue

                copy_size = self._copy_trader.calculate_copy_size(
                    float(trade.get("size", 0) or 0)
                )
                if copy_size < self._copy_trader.config.min_copy_size_usd:
                    continue

                raw_side = trade.get("side", "BUY").upper()
                side = Side.BUY if raw_side == "BUY" else Side.SELL
                price = float(trade.get("price", 0.50) or 0.50)

                if price <= 0:
                    continue
                copy_shares = max(copy_size / price, 5.0)
                copy_shares = round(copy_shares, 2)

                cost = copy_shares * price
                if cost > balance * 0.95:
                    log.debug(f"Copy skip: cost=${cost:.2f} > balance=${balance:.2f}")
                    continue

                time.sleep(self._copy_trader.config.trade_delay)

                result = self._place(token_id, side, price, copy_shares)
                if result and result.success and result.filled_size > 0:
                    self._copy_trader.record_copy(t.address)
                    self._state.trades_today += 1
                    self._alert(
                        f"COPY: {t.username or t.address[:8]} {raw_side}\n"
                        f"Size: ${copy_size:.2f} @ {price:.3f}",
                        alert_type="copy",
                    )

    # ── Position Management (Layer 5) ──

    def _discover_positions(self) -> None:
        """On startup, discover existing positions via trades history + balance checks."""
        log.info("Discovering existing positions...")
        markets = self._all_markets or []
        if not markets:
            try:
                markets = self._client.get_markets(active=True, limit=200, min_liquidity=0)
                self._all_markets = markets
            except Exception as e:
                log.error(f"Market fetch for position discovery failed: {e}")
                return

        market_by_token: dict[str, tuple[Market, Outcome]] = {}
        for m in markets:
            if m.yes_token_id:
                market_by_token[m.yes_token_id] = (m, Outcome.YES)
            if m.no_token_id:
                market_by_token[m.no_token_id] = (m, Outcome.NO)

        token_ids_to_check: set[str] = set()
        try:
            trades = self._client.get_trades(limit=500)
            for t in trades:
                tid = t.get("asset_id", t.get("token_id", ""))
                if tid and tid in market_by_token:
                    token_ids_to_check.add(tid)
            log.info(f"Found {len(token_ids_to_check)} tradeable tokens in history (from {len(trades)} trades)")
        except Exception as e:
            log.warning(f"Trade history fetch failed: {e}")

        checked = 0
        for token_id in token_ids_to_check:
            if token_id in self._held_positions:
                continue
            shares = self._client.get_token_balance(token_id)
            checked += 1
            if shares >= 1.0:
                info = market_by_token.get(token_id)
                if info:
                    market, outcome = info
                    price = market.yes_price if outcome == Outcome.YES else market.no_price
                    self._held_positions[token_id] = _HeldPosition(
                        market=market, outcome=outcome, token_id=token_id,
                        shares=shares, current_price=price,
                    )
                    log.info(
                        f"  Position: {outcome.value} {market.question[:50]} | "
                        f"{shares:.1f} shares @ {price:.3f} (${shares * price:.2f})"
                    )
            if checked % 10 == 0:
                log.info(f"  ... checked {checked}/{len(token_ids_to_check)} tokens")

        total_value = sum(p.shares * p.current_price for p in self._held_positions.values())
        log.info(f"Discovered {len(self._held_positions)} positions, est. value ${total_value:.2f}")
        self._positions_discovered = True

    def _manage_positions(self) -> None:
        """Evaluate held positions — sell when profitable or when cash is needed."""
        if not self._held_positions:
            if not self._positions_discovered:
                self._discover_positions()
            return

        balance = self._client.get_balance()
        markets = self._all_markets or []
        market_by_cid: dict[str, Market] = {m.condition_id: m for m in markets}

        sells_this_cycle = 0
        for token_id, pos in list(self._held_positions.items()):
            fresh_market = market_by_cid.get(pos.market.condition_id)
            if fresh_market:
                pos.market = fresh_market
                pos.current_price = (
                    fresh_market.yes_price if pos.outcome == Outcome.YES
                    else fresh_market.no_price
                )

            shares = self._client.get_token_balance(token_id)
            if shares < 1.0:
                log.info(f"Position closed: {pos.outcome.value} {pos.market.question[:40]}")
                del self._held_positions[token_id]
                continue
            pos.shares = shares

            action, reason = self._evaluate_position(pos, balance)

            if action == "sell" and sells_this_cycle < 3:
                sell_price = pos.current_price * 0.99
                sell_price = max(sell_price, 0.01)
                log.info(
                    f"SELL POSITION: {pos.outcome.value} {pos.market.question[:40]} | "
                    f"{pos.shares:.1f} shares @ {sell_price:.3f} | {reason}"
                )
                result = self._place(token_id, Side.SELL, sell_price, pos.shares, pos.market)
                if result and result.success:
                    self._alert(
                        f"SOLD: {pos.outcome.value} {pos.market.question[:40]}\n"
                        f"{pos.shares:.1f} shares @ {sell_price:.3f} | {reason}",
                        alert_type="edge",
                    )
                    del self._held_positions[token_id]
                    sells_this_cycle += 1
            elif action == "hold":
                log.debug(f"Hold: {pos.outcome.value} {pos.market.question[:40]} | {reason}")

    def _evaluate_position(self, pos: "_HeldPosition", balance: float) -> tuple[str, str]:
        """Decide whether to hold or sell a position.

        Returns (action, reason) where action is 'hold' or 'sell'.
        """
        price = pos.current_price

        # near-certain winner: hold — let it resolve for $1.00
        if price >= 0.95:
            return "hold", f"near-certain @ {price:.0%}, wait for resolution"

        # near-certain loser: sell to recover something
        if price <= 0.05:
            return "sell", f"near-zero @ {price:.0%}, cut loss"

        # strong position: take profit if price moved well above typical entry
        if price >= 0.85:
            return "hold", f"strong @ {price:.0%}, approaching resolution"

        # weak position losing value: sell if price is low and we need cash
        if price <= 0.15 and balance < 5.0:
            return "sell", f"weak @ {price:.0%}, freeing cash (bal=${balance:.2f})"

        # moderate position: sell if we're cash-starved
        if balance < 2.0 and price < 0.80:
            return "sell", f"need cash (bal=${balance:.2f}), price={price:.0%}"

        # market no longer active
        if not pos.market.active:
            return "sell", "market inactive"

        return "hold", f"price={price:.0%}"

    # ── Market Making (Layer 1) ──

    def _scan_markets(self) -> None:
        try:
            all_markets = self._client.get_markets(
                active=True, limit=200,
                min_liquidity=self._maker.config.min_market_liquidity,
            )
            self._all_markets = all_markets
            self._active_markets = self._maker.select_markets(all_markets)
            # sync order state — only cancel orders whose market is no longer selected
            if not self._paper_mode:
                try:
                    open_orders = self._client.get_open_orders()
                    self._committed_usd = sum(o.price * o.size for o in open_orders)
                    active_cids = {m.condition_id for m in self._active_markets}
                    stale_count = 0
                    for o in open_orders:
                        oid = o.order_id
                        info = self._known_orders.get(oid)
                        if info and info["cid"] not in active_cids:
                            try:
                                self._client.cancel_order(oid)
                                self._committed_usd -= info.get("cost", 0)
                                del self._known_orders[oid]
                                self._active_quotes.pop(info["cid"], None)
                                stale_count += 1
                            except Exception:
                                pass
                    if stale_count:
                        log.info(f"Cancelled {stale_count} orders on deselected markets")
                    if not open_orders:
                        self._active_quotes.clear()
                        self._known_orders.clear()
                except Exception:
                    pass
            top3 = ", ".join(
                f"{m.question[:25]}(v${m.volume_24h:,.0f})"
                for m in self._active_markets[:3]
            )
            log.info(
                f"Scan: {len(all_markets)} total → {len(self._active_markets)} eligible"
                f" | committed=${self._committed_usd:.1f} | quotes_cached={len(self._active_quotes)}"
            )
            if top3:
                log.info(f"  Top markets: {top3}")
        except Exception as e:
            log.error(f"Market scan failed: {e}")
            self._state.errors_today += 1

        if self._maker_enabled:
            try:
                bands = self._client.get_reward_markets()
                if bands:
                    self._maker.set_reward_bands(bands)
                    eligible = sum(
                        1 for m in self._active_markets
                        if m.yes_token_id in bands or m.no_token_id in bands
                    )
                    log.info(f"LP rewards: {eligible}/{len(self._active_markets)} active markets eligible")
            except Exception as e:
                log.debug(f"LP reward fetch failed: {e}")

    def _quote_market(self, market: Market, balance: float = 0) -> None:
        cid = market.condition_id
        try:
            book = self._client.get_orderbook(market.yes_token_id, market)
            book_spread = book.spread
            best_bid = book.bids[0][0] if book.bids else 0.0
            best_ask = book.asks[0][0] if book.asks else 0.0
            book_mid = book.mid_price
        except Exception:
            book_spread = 0.10
            best_bid = 0.0
            best_ask = 0.0
            book_mid = 0.0

        quote = self._maker.generate_quotes(
            market, book_spread,
            best_bid=best_bid, best_ask=best_ask, book_mid=book_mid,
        )
        if not quote:
            if self._state.cycle_count % 30 == 1:
                log.info(f"No quote: {market.question[:40]} (paused or at limit)")
            return

        prev = self._active_quotes.get(cid)
        if prev:
            bid_moved = abs(quote.bid_price - prev.bid_price) >= 0.01
            ask_moved = abs(quote.ask_price - prev.ask_price) >= 0.01
            if not bid_moved and not ask_moved:
                return

        # check available balance before placing (reserve 10% for flatten)
        free_balance = balance - self._committed_usd
        bid_cost = quote.bid_price * quote.bid_size
        ask_cost = (1.0 - quote.ask_price) * quote.ask_size
        total_cost = bid_cost + ask_cost
        reserve = balance * 0.10  # keep 10% for flatten/emergencies

        if free_balance - total_cost < reserve:
            if self._state.cycle_count % 30 == 1:
                log.info(f"Skip quote {market.question[:30]}: free=${free_balance:.1f} need=${total_cost:.1f} reserve=${reserve:.1f}")
            return

        # cancel old orders for this market before placing new ones
        if prev and not self._paper_mode:
            self._cancel_orders_for_market(cid)

        log.info(
            f"QUOTE: {market.question[:40]} | bid={quote.bid_price:.3f} ask={quote.ask_price:.3f} "
            f"spread={quote.spread:.4f} | {quote.reason}"
        )

        bid_result = self._place(
            quote.yes_token_id, Side.BUY, quote.bid_price, quote.bid_size, market,
        )
        if bid_result and bid_result.success:
            self._committed_usd += bid_cost
            if bid_result.order_id:
                self._known_orders[bid_result.order_id] = {
                    "token_id": quote.yes_token_id, "side": "BUY",
                    "price": quote.bid_price, "size": quote.bid_size,
                    "cid": cid, "question": market.question,
                    "yes_tid": market.yes_token_id, "no_tid": market.no_token_id,
                    "cost": bid_cost,
                }
            if bid_result.filled_size > 0:
                self._maker.on_fill(
                    cid, market.question, market.yes_token_id, market.no_token_id,
                    "BUY", market.yes_token_id, quote.bid_price, bid_result.filled_size,
                )
                self._state.trades_today += 1
                self._maker_pnl = self._maker.daily_pnl
                self._alert(f"MAKER BID: {market.question[:40]} @ {quote.bid_price:.3f}", alert_type="trade")

        no_price = 1.0 - quote.ask_price
        if no_price > 0.01:
            ask_result = self._place(
                quote.no_token_id, Side.BUY, no_price, quote.ask_size, market,
            )
            if ask_result and ask_result.success:
                self._committed_usd += ask_cost
                if ask_result.order_id:
                    self._known_orders[ask_result.order_id] = {
                        "token_id": quote.no_token_id, "side": "BUY",
                        "price": no_price, "size": quote.ask_size,
                        "cid": cid, "question": market.question,
                        "yes_tid": market.yes_token_id, "no_tid": market.no_token_id,
                        "cost": ask_cost,
                    }
                if ask_result.filled_size > 0:
                    self._maker.on_fill(
                        cid, market.question, market.yes_token_id, market.no_token_id,
                        "BUY", market.no_token_id, no_price, ask_result.filled_size,
                    )
                    self._state.trades_today += 1
                    self._maker_pnl = self._maker.daily_pnl
                    self._alert(f"MAKER ASK: {market.question[:40]} @ {no_price:.3f}", alert_type="trade")

        self._active_quotes[cid] = quote

    def _flatten_stale(self) -> None:
        for inv in self._maker.get_stale_positions():
            order = self._maker.flatten_inventory(inv.condition_id)
            if not order:
                continue

            # skip flatten if unrealized loss > $0.50 — wait for price recovery
            market = next((m for m in self._active_markets if m.condition_id == inv.condition_id), None)
            if market and not self._paper_mode:
                is_yes = (order["token_id"] == inv.yes_token_id)
                avg_price = inv.yes_avg_price if is_yes else inv.no_avg_price
                current = market.yes_price if is_yes else (1.0 - market.yes_price)
                unrealized = (current - avg_price) * order["size"]
                if unrealized < -0.50:
                    age = inv.seconds_since_trade
                    log.info(f"Defer flatten {inv.question[:30]}: loss=${unrealized:.2f} age={age:.0f}s")
                    continue

            log.warning(f"FLATTEN: {order['reason']}")
            self._alert(f"FLATTEN: {order['reason']}", alert_type="flatten", level="warning")
            side = Side.SELL if order["side"] == "SELL" else Side.BUY

            if self._paper_mode and self._paper:
                sell_price = market.yes_price * 0.99 if market else 0.50
                result = self._paper.place_order(
                    order["token_id"], side, sell_price, order["size"], market,
                )
                if result.success and result.filled_size > 0:
                    self._maker.on_fill(
                        inv.condition_id, inv.question, inv.yes_token_id,
                        inv.no_token_id, "SELL", order["token_id"],
                        sell_price, result.filled_size,
                    )
                    self._maker_pnl = self._maker.daily_pnl
            else:
                self._cancel_orders_for_market(inv.condition_id)
                result = self._client.place_market_order(
                    order["token_id"], side, order["size"],
                )
                if result.success:
                    self._maker.on_fill(
                        inv.condition_id, inv.question, inv.yes_token_id,
                        inv.no_token_id, "SELL", order["token_id"],
                        0, result.filled_size,
                    )
                elif "not enough balance" in str(result.error):
                    log.warning(f"Clearing phantom inventory for {inv.question[:40]}")
                    inv.yes_shares = 0.0
                    inv.no_shares = 0.0

    def _merge_positions(self) -> None:
        """Merge YES+NO positions back to USDC to free capital."""
        for inv, merge_size in self._maker.get_mergeable_positions():
            if self._paper_mode and self._paper:
                self._paper.add_balance(merge_size)
            profit = self._maker.record_merge(inv.condition_id, merge_size)
            self._maker_pnl = self._maker.daily_pnl
            log.info(
                f"MERGE: {inv.question[:40]} — {merge_size:.1f} shares "
                f"→ ${merge_size:.2f} freed, profit=${profit:+.4f}"
            )
            if abs(profit) > 0.001:
                self._alert(
                    f"MERGE PROFIT: {inv.question[:40]} ${profit:+.4f}",
                    alert_type="trade",
                )

    def _check_maker_fills(self) -> None:
        """Poll open orders for deferred fills on resting GTC orders."""
        if not self._known_orders:
            return

        try:
            open_orders = self._client.get_open_orders()
        except Exception:
            return

        open_ids = {o.order_id for o in open_orders}

        filled_ids = []
        for oid, info in list(self._known_orders.items()):
            if oid not in open_ids:
                # verify actual fill via order status API
                filled_size = 0.0
                if not self._paper_mode:
                    status = self._client.get_order_status(oid)
                    if status:
                        filled_size = float(status.get("size_matched", 0))
                    if filled_size < 0.01:
                        log.debug(f"Order {oid[:16]}... cancelled (not filled)")
                        self._committed_usd -= info.get("cost", 0)
                        self._active_quotes.pop(info.get("cid", ""), None)
                        filled_ids.append(oid)
                        continue
                else:
                    filled_size = info["size"]

                self._maker.on_fill(
                    info["cid"], info["question"], info["yes_tid"], info["no_tid"],
                    info["side"], info["token_id"], info["price"], filled_size,
                )
                self._state.trades_today += 1
                self._maker_pnl = self._maker.daily_pnl
                self._committed_usd -= info.get("cost", 0)
                log.info(
                    f"FILL: {info['side']} {info['question'][:40]} "
                    f"@ {info['price']:.3f} filled={filled_size:.1f}"
                )
                self._alert(
                    f"MAKER FILL: {info['question'][:40]} @ {info['price']:.3f}",
                    alert_type="trade",
                )
                filled_ids.append(oid)

        for oid in filled_ids:
            del self._known_orders[oid]

        if len(self._known_orders) > 100:
            oldest = sorted(self._known_orders.keys())[:50]
            for oid in oldest:
                self._committed_usd -= oid_info.get("cost", 0) if (oid_info := self._known_orders.get(oid)) else 0
                del self._known_orders[oid]

        self._committed_usd = max(0, self._committed_usd)

    def _cancel_orders_for_market(self, condition_id: str) -> None:
        """Cancel all resting orders for a market to free balance."""
        to_cancel = [
            oid for oid, info in self._known_orders.items()
            if info["cid"] == condition_id
        ]
        for oid in to_cancel:
            try:
                self._client.cancel_order(oid)
                cost = self._known_orders[oid].get("cost", 0)
                self._committed_usd = max(0, self._committed_usd - cost)
                del self._known_orders[oid]
                log.info(f"Cancelled order {oid[:16]}... freed ${cost:.2f}")
            except Exception as e:
                log.debug(f"Cancel failed: {e}")

    # ── Shared ──

    def _place(self, token_id: str, side: Side, price: float, size: float, market: Optional[Market] = None):
        if self._paper_mode and self._paper:
            return self._paper.place_order(token_id, side, price, size, market)
        result = self._client.place_order(token_id, side, price, size)
        if result and not result.success and result.error and "geoblock" in result.error.lower():
            log.error("GEOBLOCK detected — halting bot. Fix your VPN/network and restart.")
            self._state.halted = True
            self._state.halt_reason = "Geoblock: trading restricted in your region"
            self._alert("GEOBLOCK: Bot halted — trading restricted. Check VPN.", alert_type="error", level="error")
        return result

    def _get_current_prices(self) -> dict[str, float]:
        return {m.condition_id: m.yes_price for m in self._active_markets}

    def _get_edge_exposure(self) -> float:
        return sum(p.entry_price * p.size for p in self._edge_positions.values())

    def set_model_probs(self, probs: dict[str, float]) -> None:
        self._edge_strategy.set_model_probs(probs)

    def _alert(self, msg: str, alert_type: str = "trade", level: str = "info") -> None:
        now = time.monotonic()
        limit = self._alert_limits.get(alert_type, 30.0)
        last = self._last_alert_ts.get(alert_type, 0.0)
        if limit > 0 and (now - last) < limit:
            return
        self._last_alert_ts[alert_type] = now
        self._alerts.send(msg, level=level)

    def _report(self) -> None:
        maker_status = self._maker.status()
        arb_status = self._arb_engine.status()
        copy_status = self._copy_trader.status()
        risk_status = self._risk.status()
        uptime = (time.monotonic() - self._start_time) / 3600
        if self._paper_mode and self._paper:
            bal = self._paper.get_balance()
            equity = self._paper.get_equity()
            position_value = equity - bal
        else:
            bal = self._client.get_balance()
            prices = self._get_current_prices()
            position_value = self._maker.estimate_position_value(prices)
            equity = bal + position_value
        total_pnl = self._maker_pnl + self._edge_pnl + self._arb_pnl + self._copy_pnl

        fill_bal = maker_status.get('fill_balance', '0/0')

        log.info("─" * 55)
        log.info(f"STATUS REPORT (uptime {uptime:.1f}h)")
        log.info(f"  Mode:        {'PAPER' if self._paper_mode else 'LIVE'}")
        log.info(f"  Cash:        ${bal:.2f}")
        log.info(f"  Positions:   ${position_value:.2f}")
        log.info(f"  Portfolio:   ${equity:.2f}")
        log.info(f"  Total PnL:   ${total_pnl:+.4f}")
        log.info(f"    Maker PnL: ${self._maker_pnl:+.4f}")
        log.info(f"    Edge PnL:  ${self._edge_pnl:+.4f}")
        arb_c, snipe_c = arb_status['arb_count'], arb_status['snipe_count']
        log.info(f"    Arb PnL:   ${self._arb_pnl:+.4f} ({arb_c} arbs, {snipe_c} snipes)")
        log.info(f"    Copy PnL:  ${self._copy_pnl:+.4f} ({copy_status['copy_count']} copies)")
        mk_trades = maker_status['total_trades']
        mk_wr = maker_status['win_rate']
        log.info(f"  Maker:       {maker_status['active_markets']} mkts, {mk_trades} trades ({mk_wr:.0%})")
        log.info(f"  Fills:       {fill_bal} (bid/ask)")
        log.info(f"  Edge:        {len(self._edge_positions)} open positions")
        dd = risk_status['drawdown_pct']
        sm = risk_status['size_multiplier']
        log.info(f"  Risk:        DD={dd:.1f}% | size_mult={sm:.2f}")
        log.info(f"  Cycles:      {self._state.cycle_count}")
        log.info(f"  Errors:      {self._state.errors_today}")
        log.info("─" * 55)

        self._alert(
            f"Status ({uptime:.1f}h) Portfolio: ${equity:.2f}\n"
            f"Total PnL: ${total_pnl:+.4f} (maker=${self._maker_pnl:+.4f} edge=${self._edge_pnl:+.4f} "
            f"arb=${self._arb_pnl:+.4f} copy=${self._copy_pnl:+.4f})\n"
            f"Fills: {fill_bal} | Maker: {mk_trades} trades | Edge: {len(self._edge_positions)} pos | "
            f"Arb: {arb_status['arb_count']}+{arb_status['snipe_count']} | Copy: {copy_status['copy_count']}",
            alert_type="report",
        )

    def _shutdown(self) -> None:
        log.info("Shutting down...")
        if not self._paper_mode:
            self._client.stop_heartbeat()
            self._client.cancel_all()
        self._report()
        if self._paper_mode and self._paper:
            log.info(self._paper.summary())
        total_pnl = self._maker_pnl + self._edge_pnl + self._arb_pnl + self._copy_pnl
        uptime = (time.monotonic() - self._start_time) / 3600 if self._start_time else 0
        self._alert(
            f"Bot Shutdown\nUptime: {uptime:.1f}h | PnL: ${total_pnl:+.4f} "
            f"(maker=${self._maker_pnl:+.4f} edge=${self._edge_pnl:+.4f} "
            f"arb=${self._arb_pnl:+.4f} copy=${self._copy_pnl:+.4f})",
            alert_type="report",
        )

    def status(self) -> dict:
        maker_status = self._maker.status()
        total_pnl = self._maker_pnl + self._edge_pnl + self._arb_pnl + self._copy_pnl
        return {
            "mode": "paper" if self._paper_mode else "live",
            "uptime_hours": round((time.monotonic() - self._start_time) / 3600, 2) if self._start_time else 0,
            "cycle_count": self._state.cycle_count,
            "trades_today": self._state.trades_today,
            "errors_today": self._state.errors_today,
            "halted": self._state.halted,
            "maker_pnl": round(self._maker_pnl, 4),
            "edge_pnl": round(self._edge_pnl, 4),
            "arb_pnl": round(self._arb_pnl, 4),
            "copy_pnl": round(self._copy_pnl, 4),
            "total_pnl": round(total_pnl, 4),
            "edge_positions": len(self._edge_positions),
            "risk": self._risk.status(),
            **maker_status,
            **self._arb_engine.status(),
            **self._copy_trader.status(),
        }


class _EdgePosition:
    __slots__ = ("market", "outcome", "token_id", "entry_price", "size", "edge", "entered_at")

    def __init__(self, market: Market, outcome: Outcome, token_id: str,
                 entry_price: float, size: float, edge: float, entered_at: float):
        self.market = market
        self.outcome = outcome
        self.token_id = token_id
        self.entry_price = entry_price
        self.size = size
        self.edge = edge
        self.entered_at = entered_at


class _HeldPosition:
    __slots__ = ("market", "outcome", "token_id", "shares", "current_price")

    def __init__(self, market: Market, outcome: Outcome, token_id: str,
                 shares: float, current_price: float):
        self.market = market
        self.outcome = outcome
        self.token_id = token_id
        self.shares = shares
        self.current_price = current_price
