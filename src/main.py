from __future__ import annotations

import signal
import time

from src.config import load_config
from src.data.collector import FundingCollector
from src.data.store import DataStore
from src.execution.live import LiveExecutor
from src.execution.paper import PaperExecutor
from src.hl_client.rest import HLRestClient
from src.logger import get_logger, setup_logging
from src.monitor.alerts import AlertManager
from src.monitor.pnl import PnLTracker
from src.risk.manager import RiskManager
from src.strategy.funding_arb import FundingArbStrategy
from src.strategy.market_maker import HLMarketMaker

log = get_logger(__name__)

TOP_COINS = [
    "BTC",
    "ETH",
    "SOL",
    "DOGE",
    "ARB",
    "OP",
    "AVAX",
    "SUI",
    "LINK",
    "WIF",
    "PEPE",
    "NEAR",
    "FTM",
    "INJ",
    "TIA",
    "JUP",
    "RENDER",
    "SEI",
    "APT",
    "STRK",
]


class Orchestrator:
    def __init__(self, cfg: dict, paper: bool = True) -> None:
        self._cfg = cfg
        self._paper = paper
        self._running = False

        self._client = HLRestClient(cfg)
        self._store = DataStore(cfg.get("data", {}).get("db_path", "data/trader.db"))
        self._risk = RiskManager(self._client, cfg)
        self._alerts = AlertManager(cfg)
        self._pnl = PnLTracker(self._client, self._store)
        self._collector = FundingCollector(self._client, self._store, TOP_COINS)

        self._strategy = FundingArbStrategy(self._client, self._store, cfg)
        self._strategy.set_candidate_coins(TOP_COINS)

        # Market maker strategy
        self._mm_strategy = HLMarketMaker(cfg)

        if paper:
            self._executor = PaperExecutor(self._client, self._store)
            self._strategy.set_paper_equity(self._executor.balance)
            self._pnl.set_paper_executor(self._executor)
            log.info("Running in PAPER mode")
        else:
            self._executor = LiveExecutor(self._client, self._store)
            log.info("Running in LIVE mode")

        self._funding_interval = cfg.get("data", {}).get("funding_poll_interval_sec", 300)
        self._pnl_interval = cfg.get("monitor", {}).get("pnl_log_interval_sec", 3600)
        self._mm_interval = cfg.get("strategy", {}).get("market_maker", {}).get("tick_interval_sec", 10)
        self._last_funding_poll = 0.0
        self._last_pnl_log = 0.0
        self._last_mm_tick = 0.0

        # WebSocket mid-price cache (populated if WS is running)
        self._ws_mids: dict[str, float] = {}
        self._ws_books: dict[str, dict] = {}

    def run(self) -> None:
        self._running = True
        self._alerts.send("Trader started" + (" (PAPER)" if self._paper else " (LIVE)"))

        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

        log.info("Orchestrator starting main loop")

        while self._running:
            try:
                self._tick()
                time.sleep(10)
            except KeyboardInterrupt:
                break
            except Exception:
                log.exception("Error in main loop")
                self._alerts.send("Error in main loop — check logs", level="error")
                time.sleep(30)

        self._shutdown()

    def _tick(self) -> None:
        now = time.time()

        if now - self._last_funding_poll >= self._funding_interval:
            self._collector.collect_current()
            self._last_funding_poll = now

        if self._risk.is_halted:
            return

        account = self._client.get_account_state()
        alerts = self._risk.update(account)
        for alert in alerts:
            self._alerts.send(alert, level="error")

        if self._risk.is_halted:
            log.error("Risk halt — flattening all positions")
            self._executor.flatten_all()
            self._alerts.send("KILL SWITCH: All positions flattened", level="critical")
            return

        # Funding arb tick
        signals = self._strategy.evaluate()
        for sig in signals:
            risk_check = self._risk.check_signal(sig, account)
            if not risk_check.passed:
                log.warning("Signal blocked by risk", extra={"coin": sig.coin, "reason": risk_check.reason})
                continue

            log.info(
                "Executing signal",
                extra={"coin": sig.coin, "action": sig.action, "reason": sig.reason},
            )
            results = self._executor.execute_signal(sig)

            for r in results:
                self._strategy.on_fill(r.coin, r.side.value, r.filled_size, r.price, r.is_spot)

            if self._paper and isinstance(self._executor, PaperExecutor):
                self._strategy.set_paper_equity(self._executor.get_equity())

        # Market maker tick (runs on its own interval)
        if now - self._last_mm_tick >= self._mm_interval:
            self._tick_market_maker()
            self._last_mm_tick = now

        if now - self._last_pnl_log >= self._pnl_interval:
            self._pnl.snapshot()
            summary = self._pnl.build_daily_summary()
            self._alerts.send(summary)
            self._last_pnl_log = now

    def _tick_market_maker(self) -> None:
        """Run one market making tick for all configured coins."""
        if not self._mm_strategy.enabled:
            return

        # Check MM-specific risk: drawdown pause
        if self._risk.is_mm_paused:
            return

        for coin in self._mm_strategy.coins:
            mid = self._get_mid_price(coin)
            if mid is None or mid <= 0:
                continue

            best_bid, best_ask = self._get_bbo(coin)
            book_imbalance = self._get_book_imbalance(coin)
            inventory = self._mm_strategy.get_inventory(coin)

            # Check per-coin and total exposure limits
            if not self._risk.check_mm_exposure(coin, self._mm_strategy):
                log.warning("MM exposure limit hit", extra={"coin": coin})
                continue

            mm_signal = self._mm_strategy.generate_quotes(
                coin, mid, best_bid, best_ask, book_imbalance, inventory
            )

            if mm_signal.action == "quote_refresh":
                self._execute_mm_quotes(coin, mm_signal.quotes)
            elif mm_signal.action == "cancel":
                self._cancel_mm_quotes(coin)

        # Check for fills on existing limit orders (paper mode)
        if self._paper and isinstance(self._executor, PaperExecutor):
            current_prices = self._get_current_prices()
            fills = self._executor.check_limit_fills(current_prices)
            for fill in fills:
                if fill.get("strategy_tag") == "market_maker":
                    self._mm_strategy.on_fill(
                        fill["coin"], fill["side"], fill["size"], fill["price"]
                    )

    def _execute_mm_quotes(self, coin: str, quotes: list) -> None:
        """Place MM quotes via the executor."""
        if self._paper and isinstance(self._executor, PaperExecutor):
            # Cancel existing MM orders for this coin, then place new ones
            self._executor.cancel_orders(coin, strategy_tag="market_maker")
            self._executor.place_limit_orders(quotes, coin, strategy_tag="market_maker")
        else:
            # For live mode: use REST client to place/modify limit orders
            # Cancel existing then place new (or use modify for queue priority)
            log.info("Live MM quote refresh", extra={"coin": coin, "num_quotes": len(quotes)})

    def _cancel_mm_quotes(self, coin: str) -> None:
        """Cancel all MM quotes for a coin."""
        if self._paper and isinstance(self._executor, PaperExecutor):
            self._executor.cancel_orders(coin, strategy_tag="market_maker")
        else:
            log.info("Live MM cancel all", extra={"coin": coin})

    def _get_mid_price(self, coin: str) -> float | None:
        """Get mid price from WebSocket cache or fall back to REST."""
        if coin in self._ws_mids and self._ws_mids[coin] > 0:
            return self._ws_mids[coin]
        try:
            mids = self._client.get_all_mids()
            return mids.get(coin)
        except Exception:
            return None

    def _get_bbo(self, coin: str) -> tuple[float, float]:
        """Get best bid/ask from WebSocket cache or REST."""
        if coin in self._ws_books:
            book = self._ws_books[coin]
            levels = book.get("levels", [[], []])
            if len(levels) >= 2 and levels[0] and levels[1]:
                return float(levels[0][0]["px"]), float(levels[1][0]["px"])
        try:
            book = self._client.get_l2_snapshot(coin)
            levels = book.get("levels", [[], []])
            if len(levels) >= 2 and levels[0] and levels[1]:
                return float(levels[0][0]["px"]), float(levels[1][0]["px"])
        except Exception:
            pass
        # Fall back to mid-based estimate
        mid = self._get_mid_price(coin) or 0
        return mid * 0.9999, mid * 1.0001

    def _get_book_imbalance(self, coin: str) -> float:
        """Calculate book imbalance ratio from L2 data.

        Returns value in [-1, 1]: positive = bid-heavy, negative = ask-heavy.
        """
        try:
            if coin in self._ws_books:
                book = self._ws_books[coin]
            else:
                book = self._client.get_l2_snapshot(coin)

            levels = book.get("levels", [[], []])
            if len(levels) < 2:
                return 0.0

            bid_depth = sum(float(lv["sz"]) * float(lv["px"]) for lv in levels[0][:5])
            ask_depth = sum(float(lv["sz"]) * float(lv["px"]) for lv in levels[1][:5])
            total = bid_depth + ask_depth
            if total == 0:
                return 0.0
            return (bid_depth - ask_depth) / total
        except Exception:
            return 0.0

    def _get_current_prices(self) -> dict[str, float]:
        """Get current prices for all MM coins."""
        prices: dict[str, float] = {}
        for coin in self._mm_strategy.coins:
            mid = self._get_mid_price(coin)
            if mid is not None:
                prices[coin] = mid
        return prices

    def _handle_shutdown(self, signum: int, frame) -> None:
        log.info("Shutdown signal received")
        self._running = False

    def _shutdown(self) -> None:
        log.info("Shutting down...")
        # Cancel all MM orders on shutdown
        if self._mm_strategy.enabled:
            for coin in self._mm_strategy.coins:
                self._cancel_mm_quotes(coin)
        self._alerts.send("Trader shutting down")
        self._store.close()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="AI Trader MVP")
    parser.add_argument("--env", default=None, help="Environment (testnet/mainnet)")
    parser.add_argument("--live", action="store_true", help="Run in live mode (default: paper)")
    args = parser.parse_args()

    cfg = load_config(args.env)
    setup_logging(cfg)

    paper = not args.live
    if not paper:
        log.warning("LIVE TRADING MODE — real money at risk")

    orchestrator = Orchestrator(cfg, paper=paper)
    orchestrator.run()


if __name__ == "__main__":
    main()
