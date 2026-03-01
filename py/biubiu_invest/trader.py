"""Main trading orchestrator."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .broker import Broker, Order
from .data_providers import FetchRequest, Provider
from .policy import Policy
from .risk import RiskManager
from .storage import DailyBar, connect, init_schema, load_daily_bars, upsert_daily_bars
from .strategy import Strategy, TradeSignal

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TradeRecord:
    ts_utc: str
    symbol: str
    side: str
    qty: float
    order_type: str
    status: str
    filled_price: Optional[float]
    reason: str
    risk_check: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class Trader:
    """Orchestrates: data fetch -> signal generation -> risk check -> order execution."""

    def __init__(
        self,
        broker: Broker,
        provider: Provider,
        strategy: Strategy,
        risk_manager: RiskManager,
        policy: Policy,
        symbols: list[str],
        db_path: Path,
        trade_log_path: Optional[Path] = None,
        dry_run: bool = True,
    ):
        self.broker = broker
        self.provider = provider
        self.strategy = strategy
        self.risk = risk_manager
        self.policy = policy
        self.symbols = symbols
        self.db_path = db_path
        self.trade_log_path = trade_log_path
        self.dry_run = dry_run
        self._latest_prices: dict[str, float] = {}

    def run(self) -> list[TradeRecord]:
        """Execute one trading cycle."""
        log.info("=== Trading cycle start ===")
        log.info(
            "Strategy: %s | Symbols: %d | Dry run: %s",
            self.strategy.name,
            len(self.symbols),
            self.dry_run,
        )

        # 1. Policy gate
        if not self.policy.allow_broker_api_connection:
            log.warning(
                "Policy '%s' blocks broker API. Forcing dry_run=True.", self.policy.id
            )
            self.dry_run = True
        if not self.policy.allow_live_trading:
            log.warning(
                "Policy '%s' blocks live trading. Forcing dry_run=True.", self.policy.id
            )
            self.dry_run = True

        # 2. Fetch market data
        log.info("Fetching market data for %d symbols...", len(self.symbols))
        req = FetchRequest(ts_codes=self.symbols)
        bars = self.provider.fetch_daily_bars(req)
        log.info("Fetched %d bars", len(bars))

        # 3. Store in SQLite
        conn = connect(self.db_path)
        init_schema(conn)
        upsert_daily_bars(conn, bars)

        # 4. Load full history from storage
        stored = load_daily_bars(conn, ts_codes=self.symbols)
        conn.close()

        bars_by_symbol: dict[str, list[DailyBar]] = {}
        for b in stored:
            bars_by_symbol.setdefault(b.ts_code, []).append(b)

        # Cache latest prices for position sizing
        for symbol, sym_bars in bars_by_symbol.items():
            if sym_bars:
                latest = max(sym_bars, key=lambda b: b.trade_date)
                self._latest_prices[symbol] = latest.close

        # 5. Account & positions
        account = self.broker.get_account()
        positions = self.broker.get_positions()
        log.info(
            "Account: equity=$%.2f cash=$%.2f buying_power=$%.2f",
            account.equity,
            account.cash,
            account.buying_power,
        )
        log.info("Open positions: %d", len(positions))

        records: list[TradeRecord] = []

        # 6. Stop-loss check
        stop_symbols = self.risk.check_stop_losses(positions)
        for symbol in stop_symbols:
            records.append(self._execute_sell(symbol, "Stop-loss triggered"))

        # 7. Generate signals
        signals = self.strategy.generate_signals(bars_by_symbol, account, positions)
        log.info("Generated %d signals", len(signals))

        # Refresh state after stop-loss sells
        if stop_symbols and not self.dry_run:
            positions = self.broker.get_positions()
            account = self.broker.get_account()

        # 8. Execute: sells first (free up cash), then buys
        sell_signals = [s for s in signals if s.action == "sell"]
        buy_signals = [s for s in signals if s.action == "buy"]

        for sig in sell_signals:
            records.append(self._execute_sell(sig.symbol, sig.reason))

        if sell_signals and not self.dry_run:
            positions = self.broker.get_positions()
            account = self.broker.get_account()

        for sig in buy_signals:
            records.append(self._execute_buy(sig, account, positions))
            if not self.dry_run:
                positions = self.broker.get_positions()
                account = self.broker.get_account()

        # 9. Persist trade log
        if self.trade_log_path and records:
            self._write_trade_log(records)

        log.info("=== Trading cycle done: %d actions ===", len(records))
        return records

    def _execute_sell(self, symbol: str, reason: str) -> TradeRecord:
        ts = _now_iso()
        if self.dry_run:
            log.info("[DRY RUN] SELL %s - %s", symbol, reason)
            return TradeRecord(
                ts_utc=ts, symbol=symbol, side="sell", qty=0,
                order_type="market", status="dry_run",
                filled_price=None, reason=reason, risk_check="dry_run",
            )
        log.info("SELL %s - %s", symbol, reason)
        try:
            result = self.broker.close_position(symbol)
            self.risk.record_trade()
            return TradeRecord(
                ts_utc=ts, symbol=symbol, side="sell", qty=result.qty,
                order_type="market", status=result.status,
                filled_price=result.filled_price, reason=reason, risk_check="OK",
            )
        except Exception as e:
            log.error("Failed to sell %s: %s", symbol, e)
            return TradeRecord(
                ts_utc=ts, symbol=symbol, side="sell", qty=0,
                order_type="market", status="error",
                filled_price=None, reason=reason, risk_check=str(e),
            )

    def _execute_buy(self, signal: TradeSignal, account, positions) -> TradeRecord:
        ts = _now_iso()
        symbol = signal.symbol

        price = self._latest_prices.get(symbol, 0)
        if price <= 0:
            return TradeRecord(
                ts_utc=ts, symbol=symbol, side="buy", qty=0,
                order_type="market", status="skipped",
                filled_price=None, reason="No price data", risk_check="no_data",
            )

        qty = self.risk.compute_position_size(symbol, price, account, positions)
        if qty <= 0:
            log.info("SKIP BUY %s - position size = 0", symbol)
            return TradeRecord(
                ts_utc=ts, symbol=symbol, side="buy", qty=0,
                order_type="market", status="skipped",
                filled_price=None, reason=signal.reason,
                risk_check="position_size_zero",
            )

        order = Order(symbol=symbol, side="buy", qty=qty, order_type="market")
        check = self.risk.check_order(order, account, positions)
        if not check.allowed:
            log.info("BLOCKED BUY %s - %s", symbol, check.reason)
            return TradeRecord(
                ts_utc=ts, symbol=symbol, side="buy", qty=qty,
                order_type="market", status="blocked",
                filled_price=None, reason=signal.reason, risk_check=check.reason,
            )

        if self.dry_run:
            log.info("[DRY RUN] BUY %d x %s @ ~$%.2f - %s", qty, symbol, price, signal.reason)
            return TradeRecord(
                ts_utc=ts, symbol=symbol, side="buy", qty=qty,
                order_type="market", status="dry_run",
                filled_price=price, reason=signal.reason, risk_check="dry_run",
            )

        log.info("BUY %d x %s @ ~$%.2f - %s", qty, symbol, price, signal.reason)
        try:
            result = self.broker.submit_order(order)
            self.risk.record_trade()
            return TradeRecord(
                ts_utc=ts, symbol=symbol, side="buy", qty=qty,
                order_type="market", status=result.status,
                filled_price=result.filled_price, reason=signal.reason,
                risk_check="OK",
            )
        except Exception as e:
            log.error("Failed to buy %s: %s", symbol, e)
            return TradeRecord(
                ts_utc=ts, symbol=symbol, side="buy", qty=qty,
                order_type="market", status="error",
                filled_price=None, reason=signal.reason, risk_check=str(e),
            )

    def _write_trade_log(self, records: list[TradeRecord]) -> None:
        if not self.trade_log_path:
            return
        self.trade_log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.trade_log_path.open("a", encoding="utf-8") as f:
            for r in records:
                f.write(
                    json.dumps(
                        {
                            "ts_utc": r.ts_utc,
                            "symbol": r.symbol,
                            "side": r.side,
                            "qty": r.qty,
                            "order_type": r.order_type,
                            "status": r.status,
                            "filled_price": r.filled_price,
                            "reason": r.reason,
                            "risk_check": r.risk_check,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
