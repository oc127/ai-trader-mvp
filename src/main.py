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

log = get_logger(__name__)

TOP_COINS = ["BTC", "ETH", "SOL", "DOGE", "ARB", "OP", "AVAX", "MATIC", "LINK", "SUI"]


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

        if paper:
            self._executor = PaperExecutor(self._client, self._store)
            log.info("Running in PAPER mode")
        else:
            self._executor = LiveExecutor(self._client, self._store)
            log.info("Running in LIVE mode")

        self._funding_interval = cfg.get("data", {}).get("funding_poll_interval_sec", 300)
        self._pnl_interval = cfg.get("monitor", {}).get("pnl_log_interval_sec", 3600)
        self._last_funding_poll = 0.0
        self._last_pnl_log = 0.0

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
                self._strategy.on_fill(r.coin, r.side.value, r.filled_size, r.price)

        if now - self._last_pnl_log >= self._pnl_interval:
            self._pnl.snapshot()
            self._last_pnl_log = now

    def _handle_shutdown(self, signum: int, frame) -> None:
        log.info("Shutdown signal received")
        self._running = False

    def _shutdown(self) -> None:
        log.info("Shutting down...")
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
