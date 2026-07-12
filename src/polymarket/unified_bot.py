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
        self._active_markets: list[Market] = []
        self._start_time = 0.0
        self._last_scan_ts = 0.0
        self._last_report_ts = 0.0
        self._last_slow_ts = 0.0
        self._last_reset_day = ""

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
            if self._state.cycle_count % 60 == 0:
                log.info(f"PAUSED — maker PnL: ${self._maker.daily_pnl:+.2f}")
            self._alert(
                f"CIRCUIT BREAKER: Maker paused\nDaily PnL: ${self._maker.daily_pnl:+.2f}",
                alert_type="circuit_breaker",
                level="warning",
            )

        # periodic market scan
        if now - self._last_scan_ts >= self._scan_interval:
            self._scan_markets()
            self._last_scan_ts = now

        # Layer 1: market making quotes (fast)
        if self._maker_enabled and not self._maker.is_paused:
            for market in self._active_markets:
                try:
                    self._quote_market(market)
                except Exception as e:
                    log.error(f"Quote failed for {market.question[:40]}: {e}")
                    self._state.errors_today += 1

            self._flatten_stale()

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
        if not self._active_markets:
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
        opportunities = self._evaluate_edge(self._active_markets)

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

        log.info(
            f"EDGE TRADE: {opp.outcome.value} {opp.market.question[:50]} | "
            f"price={price:.3f} size=${size:.2f} edge={opp.edge:.1%}"
        )

        result = self._place(token_id, Side.BUY, price, size, opp.market)
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
        if not self._active_markets:
            return

        opps = self._arb_engine.scan_all(self._active_markets)
        if not opps:
            return

        for opp in opps[:3]:
            if opp.arb_type == "complete_set":
                size = min(opp.net_profit * 100, self._arb_engine.config.max_arb_size_usd)
                if size < 2.0:
                    continue
                log.info(
                    f"ARB: {opp.market.question[:40]} | cost={opp.total_cost:.3f} "
                    f"net=${opp.net_profit:.4f} roi={opp.roi_pct:.2f}%"
                )
                buy_yes = self._place(
                    opp.market.yes_token_id, Side.BUY, opp.yes_cost, size / 2, opp.market,
                )
                buy_no = self._place(
                    opp.market.no_token_id, Side.BUY, opp.no_cost, size / 2, opp.market,
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
                size = min(20.0, self._arb_engine.config.max_arb_size_usd)
                token_id = (
                    opp.market.yes_token_id if opp.snipe_side == "YES"
                    else opp.market.no_token_id
                )
                price = opp.yes_cost if opp.snipe_side == "YES" else opp.no_cost
                log.info(
                    f"SNIPE: {opp.snipe_side} {opp.market.question[:40]} "
                    f"@ {price:.3f} net=${opp.net_profit:.4f}"
                )
                result = self._place(token_id, Side.BUY, price, size, opp.market)
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

        for t in qualified:
            if not self._copy_trader.should_copy(t.address):
                continue

            trades = self._copy_trader.fetch_trader_trades(t.address)
            new_trades = self._copy_trader.detect_new_trades(t.address, trades)

            for trade in new_trades[:2]:
                copy_size = self._copy_trader.calculate_copy_size(
                    float(trade.get("size", 0) or 0)
                )
                if copy_size < self._copy_trader.config.min_copy_size_usd:
                    continue

                token_id = trade.get("asset_id", trade.get("tokenId", ""))
                raw_side = trade.get("side", "BUY").upper()
                side = Side.BUY if raw_side == "BUY" else Side.SELL
                price = float(trade.get("price", 0.50) or 0.50)

                time.sleep(self._copy_trader.config.trade_delay)

                result = self._place(token_id, side, price, copy_size)
                if result and result.success and result.filled_size > 0:
                    self._copy_trader.record_copy(t.address)
                    self._state.trades_today += 1
                    self._alert(
                        f"COPY: {t.username or t.address[:8]} {raw_side}\n"
                        f"Size: ${copy_size:.2f} @ {price:.3f}",
                        alert_type="copy",
                    )

    # ── Market Making (Layer 1) ──

    def _scan_markets(self) -> None:
        try:
            all_markets = self._client.get_markets(
                active=True, limit=200,
                min_liquidity=self._maker.config.min_market_liquidity,
            )
            self._active_markets = self._maker.select_markets(all_markets)
            log.info(f"Scan: {len(all_markets)} total → {len(self._active_markets)} eligible")
        except Exception as e:
            log.error(f"Market scan failed: {e}")
            self._state.errors_today += 1

    def _quote_market(self, market: Market) -> None:
        cid = market.condition_id
        try:
            book = self._client.get_orderbook(market.yes_token_id, market)
            book_spread = book.spread
        except Exception:
            book_spread = 0.10

        quote = self._maker.generate_quotes(market, book_spread)
        if not quote:
            if self._state.cycle_count <= 3:
                log.info(f"No quote: {market.question[:40]} (paused or at limit)")
            return

        prev = self._active_quotes.get(cid)
        if prev and abs(quote.bid_price - prev.bid_price) < 0.005 and abs(quote.ask_price - prev.ask_price) < 0.005:
            return

        bid_result = self._place(
            quote.yes_token_id, Side.BUY, quote.bid_price, quote.bid_size, market,
        )
        if bid_result and bid_result.filled_size > 0:
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
            if ask_result and ask_result.filled_size > 0:
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
            log.warning(f"FLATTEN: {order['reason']}")
            self._alert(f"FLATTEN: {order['reason']}", alert_type="flatten", level="warning")
            side = Side.SELL if order["side"] == "SELL" else Side.BUY

            if self._paper_mode and self._paper:
                market = next((m for m in self._active_markets if m.condition_id == inv.condition_id), None)
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
                result = self._client.place_market_order(
                    order["token_id"], side, order["size"],
                )
                if result.success:
                    self._maker.on_fill(
                        inv.condition_id, inv.question, inv.yes_token_id,
                        inv.no_token_id, "SELL", order["token_id"],
                        0, result.filled_size,
                    )

    # ── Shared ──

    def _place(self, token_id: str, side: Side, price: float, size: float, market: Optional[Market] = None):
        if self._paper_mode and self._paper:
            return self._paper.place_order(token_id, side, price, size, market)
        return self._client.place_order(token_id, side, price, size)

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
        bal = self._paper.get_balance() if self._paper_mode and self._paper else self._client.get_balance()
        equity = self._paper.get_equity() if self._paper_mode and self._paper else self._client.get_balance()
        total_pnl = self._maker_pnl + self._edge_pnl + self._arb_pnl + self._copy_pnl

        log.info("─" * 55)
        log.info(f"STATUS REPORT (uptime {uptime:.1f}h)")
        log.info(f"  Mode:        {'PAPER' if self._paper_mode else 'LIVE'}")
        log.info(f"  Balance:     ${bal:.2f}")
        log.info(f"  Equity:      ${equity:.2f}")
        log.info(f"  Total PnL:   ${total_pnl:+.4f}")
        log.info(f"    Maker PnL: ${self._maker_pnl:+.4f}")
        log.info(f"    Edge PnL:  ${self._edge_pnl:+.4f}")
        arb_c, snipe_c = arb_status['arb_count'], arb_status['snipe_count']
        log.info(f"    Arb PnL:   ${self._arb_pnl:+.4f} ({arb_c} arbs, {snipe_c} snipes)")
        log.info(f"    Copy PnL:  ${self._copy_pnl:+.4f} ({copy_status['copy_count']} copies)")
        mk_trades = maker_status['total_trades']
        mk_wr = maker_status['win_rate']
        log.info(f"  Maker:       {maker_status['active_markets']} mkts, {mk_trades} trades ({mk_wr:.0%})")
        log.info(f"  Edge:        {len(self._edge_positions)} open positions")
        dd = risk_status['drawdown_pct']
        sm = risk_status['size_multiplier']
        log.info(f"  Risk:        DD={dd:.1f}% | size_mult={sm:.2f}")
        log.info(f"  Cycles:      {self._state.cycle_count}")
        log.info(f"  Errors:      {self._state.errors_today}")
        log.info("─" * 55)

        self._alert(
            f"Status ({uptime:.1f}h)\n"
            f"Total PnL: ${total_pnl:+.4f} (maker=${self._maker_pnl:+.4f} edge=${self._edge_pnl:+.4f} "
            f"arb=${self._arb_pnl:+.4f} copy=${self._copy_pnl:+.4f})\n"
            f"Maker: {mk_trades} trades | Edge: {len(self._edge_positions)} pos | "
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
