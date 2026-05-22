from __future__ import annotations

import time
from typing import TYPE_CHECKING

from src.logger import get_logger

if TYPE_CHECKING:
    from src.gate_client.rest import GateClient

log = get_logger(__name__)


class GateFundingContrarianStrategy:
    def __init__(self, client: GateClient, config: dict, paper: bool = False) -> None:
        self._client = client
        self._paper = paper
        self._tag = "[PAPER] " if paper else ""

        cfg = config.get("contrarian", {})
        self._short_rate = cfg.get("short_rate_threshold", 0.001)
        self._long_rate = cfg.get("long_rate_threshold", -0.0005)
        self._min_volume = cfg.get("min_volume_24h", 50_000_000)
        self._leverage = cfg.get("leverage", 5)
        self._pos_size_usd = cfg.get("position_size_usd", 100)
        self._max_positions = cfg.get("max_positions", 3)
        self._stop_loss_pct = cfg.get("stop_loss_pct", 0.03)
        self._take_profit_pct = cfg.get("take_profit_pct", 0.08)
        self._max_hold_hours = cfg.get("max_hold_hours", 48)
        self._trailing_stop_pct = cfg.get("trailing_stop_pct", 0.02)
        self._scan_interval = cfg.get("scan_interval_sec", 300)
        self._confirm_periods = cfg.get("rate_confirm_periods", 2)
        self._max_rate = cfg.get("max_rate_8h", 0.005)
        self._excluded = set(cfg.get("excluded_coins", ["BTC", "ETH"]))
        self._blacklist = set(cfg.get("blacklist", []))

        risk = config.get("risk", {})
        self._max_daily_loss = risk.get("max_daily_loss_usd", 30)

        self._positions: dict[str, dict] = {}
        self._rate_history: dict[str, list[float]] = {}
        self._last_scan = 0.0
        self._cached_signals: list[dict] = []
        self._daily_pnl = 0.0
        self._day_start = _today_key()
        self._halted = False

    # -- Public API -----------------------------------------------------------

    def scan_signals(self) -> list[dict]:
        try:
            contracts = self._client.get_all_contracts()
        except Exception:
            log.warning("Failed to fetch contracts")
            return []

        signals = []
        for c in contracts:
            name = c.get("name", "")
            coin = name.split("_")[0].upper()
            if coin in self._excluded or coin in self._blacklist:
                continue
            if coin in self._positions:
                continue

            rate = float(c.get("funding_rate", 0))
            volume = float(c.get("trade_size", 0))
            mark = float(c.get("mark_price", 0))
            if volume < self._min_volume or mark <= 0:
                continue

            if abs(rate) > self._max_rate:
                continue

            direction = None
            if rate >= self._short_rate:
                direction = "short"
            elif rate <= self._long_rate:
                direction = "long"

            if direction is None:
                continue

            signals.append({
                "coin": coin,
                "contract": name,
                "direction": direction,
                "rate_8h": rate,
                "volume_24h": volume,
                "mark_price": mark,
                "apy": rate * 3 * 365,
            })

        signals.sort(key=lambda s: abs(s["rate_8h"]), reverse=True)
        self._last_scan = time.time()
        self._cached_signals = signals
        return signals

    def evaluate(self) -> list[dict]:
        self._check_day_reset()
        if self._halted:
            return []

        actions: list[dict] = []

        for coin, pos in list(self._positions.items()):
            action = self._check_exit(coin, pos)
            if action:
                actions.append(action)

        if len(self._positions) < self._max_positions:
            now = time.time()
            if now - self._last_scan >= self._scan_interval:
                signals = self.scan_signals()
            else:
                signals = self._cached_signals

            for sig in signals:
                if len(self._positions) + len(
                    [a for a in actions if a["action"] == "enter"]
                ) >= self._max_positions:
                    break

                coin = sig["coin"]
                if coin in self._positions:
                    continue

                history = self._rate_history.setdefault(coin, [])
                history.append(sig["rate_8h"])
                if len(history) > self._confirm_periods:
                    history[:] = history[-self._confirm_periods:]

                if len(history) < self._confirm_periods:
                    continue

                if sig["direction"] == "short" and not all(r >= self._short_rate for r in history):
                    continue
                if sig["direction"] == "long" and not all(r <= self._long_rate for r in history):
                    continue

                actions.append({
                    "action": "enter",
                    "coin": coin,
                    "contract": sig["contract"],
                    "direction": sig["direction"],
                    "rate_8h": sig["rate_8h"],
                    "mark_price": sig["mark_price"],
                })

        return actions

    def execute_enter(self, coin: str, contract: str, direction: str) -> bool:
        try:
            info = self._client.futures_get_contract(contract)
        except Exception:
            log.warning("%sContract %s not found", self._tag, contract)
            return False

        mark = float(info.get("mark_price", 0) or info.get("last_price", 0))
        quanto = float(info.get("quanto_multiplier", 1))
        if mark <= 0 or quanto <= 0:
            log.warning("%sInvalid price/quanto for %s", self._tag, coin)
            return False

        notional = self._pos_size_usd * self._leverage
        size = int(notional / mark / quanto)
        if size <= 0:
            log.warning("%sCalculated size 0 for %s", self._tag, coin)
            return False

        try:
            self._client.futures_set_leverage(contract, self._leverage)
        except Exception:
            log.warning("%sFailed to set leverage for %s, proceeding with default", self._tag, coin)

        try:
            if direction == "short":
                self._client.futures_open_short(contract, size)
            else:
                self._client.futures_open_long(contract, size)
        except Exception:
            log.warning("%sFailed to open %s %s", self._tag, direction, coin)
            self._blacklist.add(coin)
            return False

        stop = mark * (1 + self._stop_loss_pct) if direction == "short" else mark * (1 - self._stop_loss_pct)
        target = mark * (1 - self._take_profit_pct) if direction == "short" else mark * (1 + self._take_profit_pct)

        self._positions[coin] = {
            "contract": contract,
            "direction": direction,
            "size": size,
            "entry_price": mark,
            "entry_time": time.time(),
            "stop_loss": stop,
            "take_profit": target,
            "best_price": mark,
        }

        log.info(
            "%sOpened %s %s: %d contracts @ $%.4f (lev=%dx, stop=$%.4f, tp=$%.4f)",
            self._tag, direction.upper(), coin, size, mark,
            self._leverage, stop, target,
        )
        return True

    def execute_exit(self, coin: str, reason: str) -> bool:
        pos = self._positions.get(coin)
        if not pos:
            return False

        contract = pos["contract"]
        size = pos["size"]
        direction = pos["direction"]

        try:
            if direction == "short":
                self._client.futures_close_short(contract, size)
            else:
                self._client.futures_close_long(contract, size)
        except Exception:
            log.warning("%sFailed to close %s %s", self._tag, direction, coin)
            return False

        try:
            info = self._client.futures_get_contract(contract)
            exit_price = float(info.get("mark_price", 0))
        except Exception:
            exit_price = pos["entry_price"]

        pnl = self._calc_pnl(pos, exit_price)
        self._daily_pnl += pnl

        hold_hours = (time.time() - pos["entry_time"]) / 3600
        log.info(
            "%sClosed %s %s: PnL $%.2f (%s) held %.1fh",
            self._tag, direction.upper(), coin, pnl, reason, hold_hours,
        )

        del self._positions[coin]
        self._rate_history.pop(coin, None)

        if self._daily_pnl <= -self._max_daily_loss:
            self._halted = True
            log.warning("%sDaily loss limit hit ($%.2f), halting", self._tag, self._daily_pnl)

        return True

    def close_all(self) -> None:
        for coin in list(self._positions):
            self.execute_exit(coin, "shutdown")

    def get_status(self) -> str:
        if not self._positions:
            return f"{self._tag}No positions | daily PnL: ${self._daily_pnl:+.2f}"

        lines = [
            f"{self._tag}Positions ({len(self._positions)}/{self._max_positions}) "
            f"| daily PnL: ${self._daily_pnl:+.2f}"
        ]
        for coin, p in sorted(self._positions.items()):
            hold_h = (time.time() - p["entry_time"]) / 3600
            lines.append(
                f"  {p['direction'].upper():5} {coin}: {p['size']} contracts "
                f"@ ${p['entry_price']:.4f} | stop ${p['stop_loss']:.4f} "
                f"| tp ${p['take_profit']:.4f} | {hold_h:.1f}h"
            )
        return "\n".join(lines)

    # -- Internal -------------------------------------------------------------

    def _check_exit(self, coin: str, pos: dict) -> dict | None:
        contract = pos["contract"]
        try:
            info = self._client.futures_get_contract(contract)
            price = float(info.get("mark_price", 0))
        except Exception:
            return None

        if price <= 0:
            return None

        direction = pos["direction"]

        if direction == "short":
            if price <= pos["best_price"]:
                pos["best_price"] = price
            if price >= pos["stop_loss"]:
                return {"action": "exit", "coin": coin, "reason": "stop_loss"}
            if price <= pos["take_profit"]:
                return {"action": "exit", "coin": coin, "reason": "take_profit"}
            profit_pct = (pos["entry_price"] - price) / pos["entry_price"]
            if profit_pct >= 0.04:
                trailing = pos["best_price"] * (1 + self._trailing_stop_pct)
                if price >= trailing:
                    return {"action": "exit", "coin": coin, "reason": "trailing_stop"}
        else:
            if price >= pos["best_price"]:
                pos["best_price"] = price
            if price <= pos["stop_loss"]:
                return {"action": "exit", "coin": coin, "reason": "stop_loss"}
            if price >= pos["take_profit"]:
                return {"action": "exit", "coin": coin, "reason": "take_profit"}
            profit_pct = (price - pos["entry_price"]) / pos["entry_price"]
            if profit_pct >= 0.04:
                trailing = pos["best_price"] * (1 - self._trailing_stop_pct)
                if price <= trailing:
                    return {"action": "exit", "coin": coin, "reason": "trailing_stop"}

        hold_hours = (time.time() - pos["entry_time"]) / 3600
        if hold_hours >= self._max_hold_hours:
            return {"action": "exit", "coin": coin, "reason": "max_hold_time"}

        return None

    def _calc_pnl(self, pos: dict, exit_price: float) -> float:
        entry = pos["entry_price"]
        if pos["direction"] == "short":
            pct = (entry - exit_price) / entry
        else:
            pct = (exit_price - entry) / entry
        return pct * self._pos_size_usd * self._leverage

    def _check_day_reset(self) -> None:
        today = _today_key()
        if today != self._day_start:
            self._daily_pnl = 0.0
            self._day_start = today
            self._halted = False


def _today_key() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())
