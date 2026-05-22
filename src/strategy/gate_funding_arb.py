from __future__ import annotations

import time
from typing import TYPE_CHECKING

from src.logger import get_logger

if TYPE_CHECKING:
    from src.gate_client.rest import GateClient

log = get_logger(__name__)


class GateFundingArbStrategy:
    def __init__(self, client: GateClient, config: dict, paper: bool = False) -> None:
        self._client = client
        self._paper = paper
        self._tag = "[PAPER] " if paper else ""

        cfg = config.get("gate_arb", {})
        self._min_rate_8h: float = cfg.get("min_rate_8h", 0.0005)
        self._exit_rate_8h: float = cfg.get("exit_rate_8h", -0.0001)
        self._min_volume_24h: float = cfg.get("min_volume_24h", 50_000_000)
        self._max_positions: int = cfg.get("max_positions", 5)
        self._per_position_pct: float = cfg.get("per_position_pct", 0.15)
        self._max_position_usd: float = cfg.get("max_position_usd", 5000)
        self._scan_interval_sec: float = cfg.get("scan_interval_sec", 300)
        self._excluded_coins: set[str] = set(cfg.get("excluded_coins", ["BTC", "ETH"]))
        self._blacklist: set[str] = set(cfg.get("blacklist", []))
        self._rate_history_periods: int = cfg.get("rate_history_periods", 3)

        self._positions: dict[str, dict] = {}
        self._rate_history: dict[str, list[float]] = {}
        self._last_scan: float = 0.0
        self._cached_opportunities: list[dict] = []

    def scan_opportunities(self) -> list[dict]:
        try:
            contracts = self._client.get_all_contracts()
        except Exception:
            log.warning("Failed to fetch futures contracts")
            return []

        opportunities = []
        for contract in contracts:
            name = contract.get("name", "")
            coin = name.split("_")[0].upper()
            if coin in self._excluded_coins or coin in self._blacklist:
                continue
            if coin in self._positions:
                continue

            rate_8h = float(contract.get("funding_rate", 0))
            if rate_8h < self._min_rate_8h:
                continue

            volume_24h = float(contract.get("trade_size", 0))
            if volume_24h < self._min_volume_24h:
                continue

            mark_price = float(contract.get("mark_price", 0))
            apy = rate_8h * 3 * 365

            opportunities.append({
                "coin": coin,
                "contract": name,
                "rate_8h": rate_8h,
                "apy": apy,
                "volume_24h": volume_24h,
                "mark_price": mark_price,
            })

        opportunities.sort(key=lambda x: x["rate_8h"], reverse=True)
        self._last_scan = time.time()
        self._cached_opportunities = opportunities
        return opportunities

    def evaluate(self) -> list[dict]:
        actions: list[dict] = []

        try:
            self._client.get_all_contracts()
        except Exception:
            log.warning("Failed to refresh contracts cache")

        for coin, pos in list(self._positions.items()):
            try:
                contract_info = self._client.get_contract_info(pos["contract"])
                current_rate = float(contract_info.get("funding_rate", 0))
            except Exception:
                log.warning("Failed to fetch rate for %s, skipping exit check", coin)
                continue

            if current_rate < self._exit_rate_8h:
                actions.append({
                    "action": "exit",
                    "coin": coin,
                    "reason": f"rate {current_rate:.6f} below exit threshold {self._exit_rate_8h:.6f}",
                    "current_rate": current_rate,
                })

        if len(self._positions) < self._max_positions:
            now = time.time()
            if now - self._last_scan >= self._scan_interval_sec:
                opportunities = self.scan_opportunities()
            else:
                opportunities = self._cached_opportunities

            for opp in opportunities:
                if len(self._positions) + len(
                    [a for a in actions if a["action"] == "enter"]
                ) >= self._max_positions:
                    break

                coin = opp["coin"]
                if coin in self._positions:
                    continue

                history = self._rate_history.setdefault(coin, [])
                history.append(opp["rate_8h"])
                if len(history) > self._rate_history_periods:
                    history[:] = history[-self._rate_history_periods:]

                if len(history) < self._rate_history_periods:
                    continue
                if not all(r >= self._min_rate_8h for r in history):
                    continue

                capital = self._get_available_capital()
                amount_usd = min(
                    capital * self._per_position_pct,
                    self._max_position_usd,
                )
                if amount_usd < 10:
                    continue

                actions.append({
                    "action": "enter",
                    "coin": coin,
                    "reason": (
                        f"rate {opp['rate_8h']:.6f} (APY {opp['apy']:.1%}) "
                        f"stable for {self._rate_history_periods} periods"
                    ),
                    "amount_usd": amount_usd,
                    "rate_8h": opp["rate_8h"],
                    "apy": opp["apy"],
                    "contract": opp["contract"],
                })

        return actions

    def execute_enter(self, coin: str, amount_usd: float) -> bool:
        contract_name = f"{coin}_USDT"
        try:
            contract_info = self._client.get_contract_info(contract_name)
        except Exception:
            log.warning("%sNo futures contract found for %s", self._tag, coin)
            return False

        mark_price = float(contract_info.get("mark_price", 0))
        if mark_price <= 0:
            log.warning("%sInvalid mark price for %s: %s", self._tag, coin, mark_price)
            return False

        quanto_multiplier = float(contract_info.get("quanto_multiplier", 1))
        if quanto_multiplier <= 0:
            quanto_multiplier = 1.0

        spot_pair = f"{coin}_USDT"
        coin_amount = amount_usd / mark_price
        perp_size = int(coin_amount / quanto_multiplier)

        if perp_size <= 0:
            log.warning("%sCalculated perp size is 0 for %s", self._tag, coin)
            return False

        if self._paper:
            log.info("%sSpot buy %s: $%.2f (%.4f coins)", self._tag, coin, amount_usd, coin_amount)
            log.info("%sShort perp %s: %d contracts", self._tag, coin, perp_size)
        else:
            # SAFETY: test short first with 1 contract before buying spot
            try:
                self._client.futures_open_short(contract_name, 1)
            except Exception:
                log.warning("Contract %s not shortable (margin mode?), skipping", coin)
                self._blacklist.add(coin)
                return False

            # Close test contract
            try:
                self._client.futures_close_short(contract_name, 1)
            except Exception:
                pass

            # Now safe to buy spot
            try:
                self._client.spot_market_buy(spot_pair, amount_usd)
                log.info("Spot buy filled for %s: $%.2f", coin, amount_usd)
            except Exception:
                log.warning("Spot buy failed for %s", coin)
                return False

            # Open the real short
            try:
                self._client.futures_open_short(contract_name, perp_size)
                log.info("Short perp opened for %s: %d contracts", coin, perp_size)
            except Exception:
                log.warning("Perp short failed for %s, selling back spot", coin)
                try:
                    self._client.spot_market_sell(spot_pair, coin_amount)
                except Exception:
                    log.warning("Failed to sell back spot for %s", coin)
                return False

        rate_8h = float(contract_info.get("funding_rate", 0))
        self._positions[coin] = {
            "spot_amount": coin_amount,
            "perp_size": perp_size,
            "entry_rate": rate_8h,
            "entry_time": time.time(),
            "entry_price": mark_price,
            "contract": contract_name,
            "amount_usd": amount_usd,
        }
        log.info(
            "%sEntered %s: %.4f spot / %d perp @ $%.4f",
            self._tag, coin, coin_amount, perp_size, mark_price,
        )
        return True

    def execute_exit(self, coin: str) -> bool:
        pos = self._positions.get(coin)
        if pos is None:
            log.warning("No position found for %s", coin)
            return False

        contract_name = pos["contract"]
        spot_pair = f"{coin}_USDT"

        if self._paper:
            log.info("%sClose perp %s: %d contracts", self._tag, coin, pos["perp_size"])
            log.info("%sSell spot %s: %.4f coins", self._tag, coin, pos["spot_amount"])
        else:
            try:
                self._client.futures_close_short(contract_name, pos["perp_size"])
                log.info("Closed perp short for %s: %d contracts", coin, pos["perp_size"])
            except Exception:
                log.warning("Failed to close perp for %s", coin)
                return False

            try:
                self._client.spot_market_sell(spot_pair, pos["spot_amount"])
                log.info("Sold spot for %s: %.4f coins", coin, pos["spot_amount"])
            except Exception:
                log.warning("Failed to sell spot for %s, perp already closed", coin)
                return False

        del self._positions[coin]
        self._rate_history.pop(coin, None)
        log.info("%sExited position for %s", self._tag, coin)
        return True

    def close_all(self) -> None:
        for coin in list(self._positions):
            self.execute_exit(coin)

    def get_position_summary(self) -> str:
        if not self._positions:
            return "No active positions"

        lines = [f"{self._tag}Active positions ({len(self._positions)}/{self._max_positions}):"]
        for coin, pos in sorted(self._positions.items()):
            hold_hours = (time.time() - pos["entry_time"]) / 3600
            lines.append(
                f"  {coin}: {pos['spot_amount']:.4f} spot / {pos['perp_size']} perp "
                f"| entry ${pos['entry_price']:.4f} @ rate {pos['entry_rate']:.6f} "
                f"| held {hold_hours:.1f}h"
            )
        return "\n".join(lines)

    def _get_available_capital(self) -> float:
        try:
            balances = self._client.get_spot_balances()
            usdt_balance = balances.get("USDT", 0.0)
        except Exception:
            log.warning("Failed to fetch account balance")
            return 0.0

        allocated = sum(pos.get("amount_usd", 0) for pos in self._positions.values())
        return max(usdt_balance - allocated, 0.0)
