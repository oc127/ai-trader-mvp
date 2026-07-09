"""Premium Seller Bot — TradingWarz theta harvest execution engine.

Strategy: Sell far OTM options (10-20 delta), collect premium, manage risk.
- Sell Puts at Fib support (willing to hold underlying at that price)
- Sell Calls at Fib resistance (or covered against spot holdings)
- Strangles when both sides look good
- Close at 50% profit or 2x loss
- Roll expiring positions before last week

$1000 capital → Deribit BTC/ETH options, conservative sizing.
"""

from __future__ import annotations

import time
import traceback
from dataclasses import dataclass

from src.logger import get_logger
from src.options.deribit_client import DeribitClient
from src.options.scanner import OptionsScanner, ScanResult
from src.options.types import (
    OptionType,
    Position,
    SellerState,
)

log = get_logger(__name__)


@dataclass
class SellerConfig:
    # sizing
    max_capital_usd: float = 1000.0
    max_single_trade_pct: float = 0.15     # 15% of capital per trade
    max_total_exposure_pct: float = 0.50   # 50% of capital at risk
    max_positions: int = 5

    # profit / loss management
    take_profit_pct: float = 0.50          # close when 50% of premium collected
    stop_loss_multiplier: float = 2.0      # close when option price doubles
    roll_before_dte: float = 5.0           # roll positions with < 5 DTE

    # timing
    scan_interval_seconds: float = 900     # scan every 15 min
    check_interval_seconds: float = 60     # check positions every 60s
    report_interval_seconds: float = 3600  # report every hour

    # safety
    max_daily_loss_usd: float = 100.0
    max_portfolio_delta: float = 0.30      # max absolute portfolio delta
    max_errors: int = 10


def load_seller_config(cfg: dict) -> SellerConfig:
    opts = cfg.get("options", {}).get("seller", {})
    return SellerConfig(
        max_capital_usd=opts.get("max_capital_usd", 1000.0),
        max_single_trade_pct=opts.get("max_single_trade_pct", 0.15),
        max_total_exposure_pct=opts.get("max_total_exposure_pct", 0.50),
        max_positions=opts.get("max_positions", 5),
        take_profit_pct=opts.get("take_profit_pct", 0.50),
        stop_loss_multiplier=opts.get("stop_loss_multiplier", 2.0),
        roll_before_dte=opts.get("roll_before_dte", 5.0),
        scan_interval_seconds=opts.get("scan_interval_seconds", 900),
        check_interval_seconds=opts.get("check_interval_seconds", 60),
        report_interval_seconds=opts.get("report_interval_seconds", 3600),
        max_daily_loss_usd=opts.get("max_daily_loss_usd", 100.0),
        max_portfolio_delta=opts.get("max_portfolio_delta", 0.30),
        max_errors=opts.get("max_errors", 10),
    )


@dataclass
class SoldOption:
    """Tracking info for a sold option."""
    instrument_name: str
    entry_price: float        # price we sold at (in underlying)
    entry_premium_usd: float  # premium collected in USD
    size: float
    underlying_price_at_entry: float
    sold_at: float            # monotonic timestamp
    option_type: OptionType = OptionType.PUT
    strike: float = 0.0
    expiry_ts: int = 0


class PremiumSellerBot:
    """Automated theta harvesting bot for Deribit options.

    Core loop:
      1. Scan for attractive options to sell (far OTM, good premium)
      2. Sell with conservative sizing
      3. Monitor positions:
         - Take profit at 50% premium collected
         - Stop loss if option price doubles
         - Roll before expiry (< 5 DTE)
      4. Track P&L, respect daily loss limits
    """

    def __init__(self, cfg: dict) -> None:
        self._cfg = cfg
        self._config = load_seller_config(cfg)
        self._client = DeribitClient(cfg)
        self._scanner = OptionsScanner(self._client, cfg)

        self._state = SellerState()
        self._sold: dict[str, SoldOption] = {}

        self._start_time = 0.0
        self._last_scan_ts = 0.0
        self._last_check_ts = 0.0
        self._last_report_ts = 0.0
        self._daily_pnl = 0.0
        self._total_pnl = 0.0

        self._testnet = cfg.get("options", {}).get("testnet", True)

    def run(self) -> None:
        self._start_time = time.monotonic()

        log.info("=" * 60)
        log.info("Premium Seller Bot (TradingWarz Theta Harvest)")
        log.info(f"Mode: {'TESTNET' if self._testnet else 'PRODUCTION'}")
        log.info(f"Capital: ${self._config.max_capital_usd:,.0f}")
        log.info(f"Max positions: {self._config.max_positions}")
        tp = self._config.take_profit_pct
        sl = self._config.stop_loss_multiplier
        log.info(f"Take profit: {tp:.0%} | Stop loss: {sl:.0f}x")
        log.info(f"Scan interval: {self._config.scan_interval_seconds}s")
        log.info("=" * 60)

        if not self._client.authenticate():
            log.error("Failed to authenticate with Deribit. Check your API credentials.")
            return

        # initial account check
        self._print_account_summary()

        try:
            while not self._state.halted:
                self._cycle()
                time.sleep(self._config.check_interval_seconds)
        except KeyboardInterrupt:
            log.info("Stopped by user")
        except Exception:
            log.exception("Bot crashed")
            raise
        finally:
            self._shutdown()

    def stop(self) -> None:
        self._state.halted = True
        self._state.halt_reason = "Manual stop"

    def _cycle(self) -> None:
        self._state.cycle_count += 1
        now = time.monotonic()

        # daily loss check
        if self._daily_pnl <= -self._config.max_daily_loss_usd:
            if not self._state.halted:
                log.warning(f"DAILY LOSS LIMIT: ${self._daily_pnl:.2f} — halting")
                self._state.halted = True
                self._state.halt_reason = f"Daily loss limit ${self._daily_pnl:.2f}"
            return

        # error limit
        if self._state.errors_today >= self._config.max_errors:
            log.warning(f"Error limit reached: {self._state.errors_today}")
            self._state.halted = True
            self._state.halt_reason = "Too many errors"
            return

        # check existing positions (every cycle)
        try:
            self._check_positions()
        except Exception as e:
            log.error(f"Position check failed: {e}")
            self._state.errors_today += 1

        # scan for new opportunities (every scan_interval)
        if now - self._last_scan_ts >= self._config.scan_interval_seconds:
            try:
                self._scan_and_sell()
            except Exception as e:
                log.error(f"Scan failed: {e}")
                log.debug(traceback.format_exc())
                self._state.errors_today += 1
            self._last_scan_ts = now

        # periodic report
        if now - self._last_report_ts >= self._config.report_interval_seconds:
            self._report()
            self._last_report_ts = now

    # ── Scan & Sell ──

    def _scan_and_sell(self) -> None:
        if len(self._sold) >= self._config.max_positions:
            log.info(f"Max positions ({self._config.max_positions}) reached, skipping scan")
            return

        results = self._scanner.scan()
        if not results:
            log.info("No attractive options found")
            return

        log.info(self._scanner.format_report(results, top_n=5))

        # try to sell top candidates
        slots = self._config.max_positions - len(self._sold)
        sold_count = 0

        for result in results:
            if sold_count >= slots:
                break
            if result.instrument.instrument_name in self._sold:
                continue
            if not self._check_risk(result):
                continue

            if self._execute_sell(result):
                sold_count += 1

    def _check_risk(self, result: ScanResult) -> bool:
        inst = result.instrument
        cfg = self._config

        # capital check
        used = sum(s.entry_premium_usd * s.size for s in self._sold.values())
        max_exposure = cfg.max_capital_usd * cfg.max_total_exposure_pct
        if used >= max_exposure:
            log.debug("Max exposure reached")
            return False

        # single trade size check
        max_single = cfg.max_capital_usd * cfg.max_single_trade_pct
        if inst.premium_usd > max_single:
            log.debug(f"Premium ${inst.premium_usd:.1f} exceeds single trade max ${max_single:.1f}")
            return False

        # portfolio delta check
        current_delta = self._portfolio_delta()
        new_delta = current_delta + inst.greeks.delta * (-1)  # selling reverses delta
        if abs(new_delta) > cfg.max_portfolio_delta:
            log.debug(f"Portfolio delta {new_delta:.3f} would exceed limit {cfg.max_portfolio_delta}")
            return False

        return True

    def _execute_sell(self, result: ScanResult) -> bool:
        inst = result.instrument

        # determine size (in contracts)
        max_usd = self._config.max_capital_usd * self._config.max_single_trade_pct
        size = inst.min_trade_amount  # start with minimum

        # for BTC options, 0.1 BTC is typical minimum
        notional = size * inst.underlying_price
        if notional > max_usd:
            log.info(f"Min trade ${notional:.0f} exceeds budget ${max_usd:.0f}, skipping {inst.instrument_name}")
            return False

        # sell at bid (conservative — ensures fill)
        price = inst.bid
        if price <= 0:
            return False

        log.info(
            f"SELLING: {inst.instrument_name} | "
            f"size={size} price={price:.4f} ({inst.option_type.value}) | "
            f"delta={inst.greeks.delta:+.3f} DTE={inst.days_to_expiry:.0f} | "
            f"premium≈${price * inst.underlying_price * size:.1f}"
        )

        trade = self._client.sell_option(
            instrument_name=inst.instrument_name,
            amount=size,
            price=price,
            post_only=True,
        )

        if trade.success:
            fill_price = trade.price if trade.price > 0 else price
            self._sold[inst.instrument_name] = SoldOption(
                instrument_name=inst.instrument_name,
                entry_price=fill_price,
                entry_premium_usd=fill_price * inst.underlying_price * trade.amount,
                size=trade.amount,
                underlying_price_at_entry=inst.underlying_price,
                sold_at=time.monotonic(),
                option_type=inst.option_type,
                strike=inst.strike,
                expiry_ts=inst.expiry_ts,
            )
            self._state.trades_today += 1
            self._state.positions_opened += 1
            self._state.premium_collected_today += trade.premium_usd
            self._state.total_premium_collected += trade.premium_usd
            log.info(f"SOLD: {inst.instrument_name} — premium ${trade.premium_usd:.2f}")
            return True
        else:
            log.warning(f"Sell failed: {inst.instrument_name} — {trade.error}")
            return False

    # ── Position Management ──

    def _check_positions(self) -> None:
        if not self._sold:
            return

        # fetch live positions from Deribit
        for currency in ["BTC", "ETH"]:
            positions = self._client.get_positions(currency=currency)
            for pos in positions:
                sold = self._sold.get(pos.instrument_name)
                if not sold:
                    continue
                self._evaluate_exit(sold, pos)

    def _evaluate_exit(self, sold: SoldOption, pos: Position) -> None:
        current_price = abs(pos.mark_price)
        entry_price = sold.entry_price

        if entry_price <= 0:
            return

        # profit ratio: how much of premium we've captured
        # (sold at entry_price, now worth current_price — profit = entry - current)
        profit_ratio = (entry_price - current_price) / entry_price if entry_price > 0 else 0

        # DTE check
        dte = max(0, (sold.expiry_ts - time.time()) / 86400) if sold.expiry_ts > 0 else 999

        reason = ""

        # take profit at 50% (option price dropped to half what we sold it for)
        if profit_ratio >= self._config.take_profit_pct:
            reason = f"TAKE PROFIT: {profit_ratio:.0%} captured"

        # stop loss (option price doubled)
        elif current_price >= entry_price * self._config.stop_loss_multiplier:
            mult = self._config.stop_loss_multiplier
            reason = f"STOP LOSS: price {current_price:.4f} >= {mult}x entry {entry_price:.4f}"

        # roll before expiry
        elif dte <= self._config.roll_before_dte:
            reason = f"ROLL: {dte:.1f} DTE remaining"

        if reason:
            self._close_position(sold, pos, reason)

    def _close_position(self, sold: SoldOption, pos: Position, reason: str) -> None:
        log.info(f"CLOSING: {sold.instrument_name} — {reason}")

        # buy back the option we sold
        trade = self._client.buy_option(
            instrument_name=sold.instrument_name,
            amount=abs(pos.size),
        )

        if trade.success:
            # PnL = premium collected - cost to close
            close_cost = trade.price * sold.underlying_price_at_entry * trade.amount
            pnl = sold.entry_premium_usd - close_cost

            self._daily_pnl += pnl
            self._total_pnl += pnl
            self._state.positions_closed += 1

            del self._sold[sold.instrument_name]
            log.info(f"CLOSED: {sold.instrument_name} — PnL ${pnl:+.2f} ({reason})")
        else:
            log.error(f"Failed to close {sold.instrument_name}: {trade.error}")
            self._state.errors_today += 1

    # ── Helpers ──

    def _portfolio_delta(self) -> float:
        total_delta = 0.0
        for currency in ["BTC", "ETH"]:
            positions = self._client.get_positions(currency=currency)
            for pos in positions:
                total_delta += pos.delta
        return total_delta

    def _print_account_summary(self) -> None:
        for currency in ["BTC", "ETH"]:
            summary = self._client.get_account_summary(currency=currency)
            if not summary:
                continue
            equity = summary.get("equity", 0)
            balance = summary.get("balance", 0)
            margin = summary.get("initial_margin", 0)
            avail = summary.get("available_funds", 0)
            index = self._client.get_index_price(currency)
            log.info(
                f"Account [{currency}]: equity={equity:.4f} ({equity * index:,.0f} USD) "
                f"balance={balance:.4f} margin={margin:.4f} available={avail:.4f}"
            )

    def _report(self) -> None:
        uptime = (time.monotonic() - self._start_time) / 3600 if self._start_time else 0

        log.info("─" * 55)
        log.info(f"THETA HARVEST REPORT (uptime {uptime:.1f}h)")
        log.info(f"  Mode:        {'TESTNET' if self._testnet else 'PRODUCTION'}")
        log.info(f"  Positions:   {len(self._sold)} open / {self._config.max_positions} max")
        log.info(f"  Daily PnL:   ${self._daily_pnl:+.2f}")
        log.info(f"  Total PnL:   ${self._total_pnl:+.2f}")
        log.info(f"  Premium:     ${self._state.total_premium_collected:.2f} collected")
        log.info(f"  Trades:      {self._state.trades_today} today")
        log.info(f"  Errors:      {self._state.errors_today}")

        if self._sold:
            log.info("  Open positions:")
            for name, sold in self._sold.items():
                age_h = (time.monotonic() - sold.sold_at) / 3600
                dte = max(0, (sold.expiry_ts - time.time()) / 86400) if sold.expiry_ts > 0 else 0
                log.info(
                    f"    {name}: ${sold.entry_premium_usd:.1f} prem, "
                    f"{dte:.0f} DTE, {age_h:.1f}h held"
                )

        log.info("─" * 55)

    def _shutdown(self) -> None:
        log.info("Shutting down Premium Seller...")
        self._report()
        log.info(
            f"Session: PnL ${self._total_pnl:+.2f} | "
            f"Premium collected ${self._state.total_premium_collected:.2f} | "
            f"Positions opened: {self._state.positions_opened} closed: {self._state.positions_closed}"
        )

    def status(self) -> dict:
        return {
            "mode": "testnet" if self._testnet else "production",
            "uptime_hours": round((time.monotonic() - self._start_time) / 3600, 2) if self._start_time else 0,
            "open_positions": len(self._sold),
            "daily_pnl": round(self._daily_pnl, 2),
            "total_pnl": round(self._total_pnl, 2),
            "premium_collected": round(self._state.total_premium_collected, 2),
            "trades_today": self._state.trades_today,
            "halted": self._state.halted,
        }
