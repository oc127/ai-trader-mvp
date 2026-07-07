"""Polymarket 24/7 high-frequency market-making bot.

Philosophy: 赚 spread，不赌方向，快进快出，低回撤，稳。
- Quote both sides in high-liquidity markets
- Earn bid-ask spread on round trips
- Auto-flatten stale inventory (never hold directional risk)
- Multiple layers of circuit breakers
- Paper mode by default — proves profitability before going live
"""

from __future__ import annotations

import time
import traceback
from datetime import datetime, timezone
from typing import Optional

from src.logger import get_logger
from src.polymarket.client import PolymarketClient
from src.polymarket.market_maker import HighFreqMarketMaker, QuotePair
from src.polymarket.paper import PaperExecutor
from src.polymarket.types import BotState, Market, Outcome, Side

log = get_logger(__name__)


class PolymarketBot:
    """High-frequency market-making bot for Polymarket.

    Cycle: scan markets → generate quotes → place orders → monitor fills → manage inventory.
    Runs every few seconds for high-frequency operation.
    """

    def __init__(self, cfg: dict) -> None:
        self._cfg = cfg
        pm_cfg = cfg.get("polymarket", {})

        self._paper_mode = pm_cfg.get("paper_mode", True)
        self._cycle_interval = pm_cfg.get("cycle_interval_seconds", 5)
        self._scan_interval = pm_cfg.get("scan_interval_seconds", 60)
        self._report_interval = pm_cfg.get("report_interval_seconds", 300)

        # components
        self._client = PolymarketClient(cfg)
        self._maker = HighFreqMarketMaker(cfg)
        self._paper = PaperExecutor(cfg) if self._paper_mode else None

        # state
        self._state = BotState()
        self._last_scan_ts = 0.0
        self._last_report_ts = 0.0
        self._active_markets: list[Market] = []
        self._active_quotes: dict[str, QuotePair] = {}  # condition_id -> quote
        self._start_time = 0.0

        # daily reset tracking
        self._last_reset_day = ""

        log.info(
            f"PolymarketBot initialized: mode={'PAPER' if self._paper_mode else 'LIVE'}, "
            f"cycle={self._cycle_interval}s, quote_size=${self._maker.config.quote_size_usd}"
        )

    def run(self) -> None:
        """Main 24/7 loop."""
        self._start_time = time.monotonic()

        log.info("=" * 60)
        log.info("Polymarket Market Maker Starting")
        log.info(f"Mode: {'PAPER' if self._paper_mode else 'LIVE'}")
        log.info(f"Strategy: high-freq spread capture, no directional bets")
        log.info(f"Max exposure: ${self._maker.config.max_total_exposure}")
        log.info(f"Max loss/day: ${self._maker.config.max_daily_loss}")
        log.info(f"Quote size: ${self._maker.config.quote_size_usd}/side")
        log.info(f"Inventory timeout: {self._maker.config.max_inventory_age_seconds}s")
        log.info("=" * 60)

        if self._paper_mode and self._paper:
            log.info(f"Paper balance: ${self._paper.get_balance():.2f}")

        # start heartbeat for live mode
        if not self._paper_mode:
            self._client.start_heartbeat()

        try:
            while not self._state.halted:
                self._cycle()
                time.sleep(self._cycle_interval)
        except KeyboardInterrupt:
            log.info("Stopped by user (Ctrl+C)")
        except Exception:
            log.exception("Bot crashed")
            raise
        finally:
            self._shutdown()

    def _cycle(self) -> None:
        """One bot cycle: scan → quote → flatten stale → report."""
        self._state.cycle_count += 1
        now = time.monotonic()

        # midnight UTC reset
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._last_reset_day:
            self._maker.reset_daily()
            self._state.trades_today = 0
            self._state.errors_today = 0
            self._last_reset_day = today
            log.info(f"Daily reset: {today}")

        # check if paused (consecutive losses cooldown or daily loss limit)
        if self._maker.is_paused:
            if self._state.cycle_count % 60 == 0:
                log.info(f"PAUSED — daily PnL: ${self._maker.daily_pnl:+.2f}")
            return

        # periodic market scan
        if now - self._last_scan_ts >= self._scan_interval:
            self._scan_markets()
            self._last_scan_ts = now

        # generate and place quotes for each active market
        for market in self._active_markets:
            try:
                self._quote_market(market)
            except Exception as e:
                log.error(f"Quote failed for {market.question[:40]}: {e}")
                self._state.errors_today += 1

        # auto-flatten stale inventory
        self._flatten_stale()

        # periodic status report
        if now - self._last_report_ts >= self._report_interval:
            self._report()
            self._last_report_ts = now

    def _scan_markets(self) -> None:
        """Fetch and filter markets for market making."""
        try:
            all_markets = self._client.get_markets(
                active=True,
                limit=200,
                min_liquidity=self._maker.config.min_market_liquidity,
            )
            self._active_markets = self._maker.select_markets(all_markets)
            log.info(
                f"Scan: {len(all_markets)} total → {len(self._active_markets)} eligible markets"
            )
            for m in self._active_markets:
                log.info(f"  {m.question[:50]} | mid={m.yes_price:.2f} | liq=${m.liquidity:,.0f}")
        except Exception as e:
            log.error(f"Market scan failed: {e}")
            self._state.errors_today += 1

    def _quote_market(self, market: Market) -> None:
        """Generate and place quotes for one market."""
        cid = market.condition_id

        # get current book spread
        try:
            book = self._client.get_orderbook(market.yes_token_id, market)
            book_spread = book.spread
        except Exception:
            book_spread = 0.10  # fallback wide spread

        # generate quotes
        quote = self._maker.generate_quotes(market, book_spread)
        if not quote:
            return

        prev_quote = self._active_quotes.get(cid)

        # skip if quotes haven't changed significantly (avoid unnecessary order churn)
        if prev_quote and abs(quote.bid_price - prev_quote.bid_price) < 0.005 and abs(quote.ask_price - prev_quote.ask_price) < 0.005:
            return

        # cancel previous quotes for this market
        # (in paper mode we just place new ones)

        # place bid (buy YES)
        bid_result = self._place(
            token_id=quote.yes_token_id,
            side=Side.BUY,
            price=quote.bid_price,
            size=quote.bid_size,
            market=market,
        )
        if bid_result and bid_result.filled_size > 0:
            self._maker.on_fill(
                cid, market.question, market.yes_token_id, market.no_token_id,
                "BUY", market.yes_token_id, quote.bid_price, bid_result.filled_size,
            )
            self._state.trades_today += 1

        # place ask (buy NO = effectively selling YES)
        no_price = 1.0 - quote.ask_price
        if no_price > 0.01:
            ask_size_no = quote.ask_size  # shares of NO
            ask_result = self._place(
                token_id=quote.no_token_id,
                side=Side.BUY,
                price=no_price,
                size=ask_size_no,
                market=market,
            )
            if ask_result and ask_result.filled_size > 0:
                self._maker.on_fill(
                    cid, market.question, market.yes_token_id, market.no_token_id,
                    "BUY", market.no_token_id, no_price, ask_result.filled_size,
                )
                self._state.trades_today += 1

        self._active_quotes[cid] = quote

    def _flatten_stale(self) -> None:
        """Auto-flatten positions that have been held too long."""
        stale = self._maker.get_stale_positions()
        for inv in stale:
            order = self._maker.flatten_inventory(inv.condition_id)
            if not order:
                continue
            log.warning(f"FLATTEN: {order['reason']}")
            side = Side.SELL if order["side"] == "SELL" else Side.BUY

            # for paper mode, sell at a slight discount (simulating market impact)
            # in live mode, use market order (FOK)
            if self._paper_mode and self._paper:
                # estimate current price
                market = None
                for m in self._active_markets:
                    if m.condition_id == inv.condition_id:
                        market = m
                        break
                sell_price = market.yes_price * 0.99 if market else 0.50
                result = self._paper.place_order(
                    token_id=order["token_id"],
                    side=side,
                    price=sell_price,
                    size=order["size"],
                    market=market,
                )
                if result.success and result.filled_size > 0:
                    self._maker.on_fill(
                        inv.condition_id, inv.question,
                        inv.yes_token_id, inv.no_token_id,
                        "SELL", order["token_id"], sell_price, result.filled_size,
                    )
            else:
                result = self._client.place_market_order(
                    token_id=order["token_id"],
                    side=side,
                    amount=order["size"],
                )
                if result.success:
                    self._maker.on_fill(
                        inv.condition_id, inv.question,
                        inv.yes_token_id, inv.no_token_id,
                        "SELL", order["token_id"], 0, result.filled_size,
                    )

    def _place(self, token_id: str, side: Side, price: float, size: float,
               market: Optional[Market] = None):
        """Place an order through paper or live executor."""
        if self._paper_mode and self._paper:
            return self._paper.place_order(token_id, side, price, size, market)
        else:
            return self._client.place_order(token_id, side, price, size)

    def _report(self) -> None:
        """Log a status report."""
        status = self._maker.status()
        uptime = time.monotonic() - self._start_time
        hours = uptime / 3600

        bal = self._paper.get_balance() if self._paper_mode and self._paper else 0
        equity = self._paper.get_equity() if self._paper_mode and self._paper else 0

        log.info("─" * 50)
        log.info(f"STATUS REPORT (uptime {hours:.1f}h)")
        log.info(f"  Mode:       {'PAPER' if self._paper_mode else 'LIVE'}")
        log.info(f"  Balance:    ${bal:.2f}")
        log.info(f"  Equity:     ${equity:.2f}")
        log.info(f"  Daily PnL:  ${status['daily_pnl']:+.4f}")
        log.info(f"  Exposure:   ${status['total_exposure']:.2f}")
        log.info(f"  Markets:    {status['active_markets']}")
        log.info(f"  Trades:     {status['total_trades']} (win rate: {status['win_rate']:.0%})")
        log.info(f"  Cycles:     {self._state.cycle_count}")
        log.info(f"  Errors:     {self._state.errors_today}")

        for pos in status.get("positions", []):
            log.info(
                f"    {pos['market']}: YES={pos['yes']:.1f} NO={pos['no']:.1f} "
                f"exp=${pos['exposure']:+.2f} pnl=${pos['pnl']:+.4f} age={pos['age_s']:.0f}s"
            )
        log.info("─" * 50)

    def _shutdown(self) -> None:
        """Clean shutdown."""
        log.info("Shutting down...")
        if not self._paper_mode:
            self._client.stop_heartbeat()
            self._client.cancel_all()

        self._report()
        if self._paper_mode and self._paper:
            log.info(self._paper.summary())

    def status(self) -> dict:
        """Return current bot status."""
        maker_status = self._maker.status()
        return {
            "mode": "paper" if self._paper_mode else "live",
            "uptime_hours": round((time.monotonic() - self._start_time) / 3600, 2) if self._start_time else 0,
            "cycle_count": self._state.cycle_count,
            "trades_today": self._state.trades_today,
            "errors_today": self._state.errors_today,
            "halted": self._state.halted,
            **maker_status,
        }
