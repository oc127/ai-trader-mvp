"""Options scanner — find profitable options to sell (TradingWarz theta harvest style).

Filters:
  - Far OTM (10-20 delta): market almost never touches these strikes
  - 7-45 DTE: sweet spot for theta decay acceleration
  - Minimum premium: don't sell garbage for pennies
  - Spread filter: skip illiquid options with wide bid-ask
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from src.logger import get_logger
from src.options.deribit_client import DeribitClient
from src.options.types import OptionInstrument, OptionType

log = get_logger(__name__)


@dataclass
class ScanConfig:
    currencies: list[str]
    min_delta: float       # absolute value, e.g. 0.05
    max_delta: float       # absolute value, e.g. 0.20
    min_dte: float         # days to expiry
    max_dte: float
    min_premium_usd: float
    max_spread_pct: float  # max bid-ask spread as % of mid
    min_open_interest: float
    min_iv: float          # minimum implied volatility
    option_types: list[str]  # ["put", "call", "both"]


def default_scan_config(cfg: dict) -> ScanConfig:
    opts = cfg.get("options", {}).get("scanner", {})
    return ScanConfig(
        currencies=opts.get("currencies", ["BTC", "ETH"]),
        min_delta=opts.get("min_delta", 0.05),
        max_delta=opts.get("max_delta", 0.20),
        min_dte=opts.get("min_dte", 7),
        max_dte=opts.get("max_dte", 45),
        min_premium_usd=opts.get("min_premium_usd", 5.0),
        max_spread_pct=opts.get("max_spread_pct", 0.50),
        min_open_interest=opts.get("min_open_interest", 10),
        min_iv=opts.get("min_iv", 0.30),
        option_types=opts.get("option_types", ["both"]),
    )


@dataclass
class ScanResult:
    instrument: OptionInstrument
    score: float          # composite attractiveness score
    annualized_yield: float  # annualized premium yield on collateral


class OptionsScanner:
    """Scan Deribit for options worth selling."""

    def __init__(self, client: DeribitClient, cfg: dict) -> None:
        self._client = client
        self._cfg = cfg
        self._scan_config = default_scan_config(cfg)

    def scan(self, currency: str | None = None) -> list[ScanResult]:
        currencies = [currency] if currency else self._scan_config.currencies
        all_results: list[ScanResult] = []

        for ccy in currencies:
            results = self._scan_currency(ccy)
            all_results.extend(results)

        all_results.sort(key=lambda r: r.score, reverse=True)
        log.info(f"Options scan: {len(all_results)} candidates across {currencies}")
        return all_results

    def _scan_currency(self, currency: str) -> list[ScanResult]:
        instruments = self._client.get_instruments(currency=currency, kind="option")
        if not instruments:
            log.warning(f"No instruments found for {currency}")
            return []

        sc = self._scan_config

        want_puts = "put" in sc.option_types or "both" in sc.option_types
        want_calls = "call" in sc.option_types or "both" in sc.option_types

        # pre-filter by expiry and type before fetching tickers (expensive)
        now = time.time()
        candidates: list[OptionInstrument] = []
        for inst in instruments:
            dte = (inst.expiry_ts - now) / 86400
            if dte < sc.min_dte or dte > sc.max_dte:
                continue
            if inst.option_type == OptionType.PUT and not want_puts:
                continue
            if inst.option_type == OptionType.CALL and not want_calls:
                continue
            candidates.append(inst)

        log.info(f"{currency}: {len(instruments)} total → {len(candidates)} in DTE range")

        results: list[ScanResult] = []
        for inst in candidates:
            inst = self._client.enrich_instrument(inst)
            if not self._passes_filters(inst):
                continue

            score = self._score(inst)
            ann_yield = self._annualized_yield(inst)
            results.append(ScanResult(
                instrument=inst,
                score=score,
                annualized_yield=ann_yield,
            ))

        log.info(f"{currency}: {len(results)} pass all filters")
        return results

    def _passes_filters(self, inst: OptionInstrument) -> bool:
        sc = self._scan_config

        if inst.underlying_price <= 0:
            return False

        # delta filter (absolute value)
        abs_delta = abs(inst.greeks.delta)
        if abs_delta < sc.min_delta or abs_delta > sc.max_delta:
            return False

        # premium filter
        if inst.premium_usd < sc.min_premium_usd:
            return False

        # bid-ask spread filter
        if inst.bid <= 0 or inst.ask <= 0:
            return False
        spread_pct = (inst.ask - inst.bid) / inst.mid if inst.mid > 0 else 1.0
        if spread_pct > sc.max_spread_pct:
            return False

        # IV filter
        if inst.iv < sc.min_iv:
            return False

        # open interest
        if inst.open_interest < sc.min_open_interest:
            return False

        return True

    def _score(self, inst: OptionInstrument) -> float:
        """Composite score — higher = more attractive to sell."""
        dte = inst.days_to_expiry

        # theta/premium ratio: more theta decay per dollar of premium = better
        theta_score = abs(inst.greeks.theta) * 100 if inst.greeks.theta != 0 else 0

        # OTM distance: further OTM = safer (capped contribution)
        otm_score = min(inst.otm_pct * 100, 30)

        # IV premium: higher IV = more premium to harvest
        iv_score = inst.iv * 10 if inst.iv > 0 else 0

        # DTE sweet spot: 20-30 DTE is optimal for theta acceleration
        dte_score = 10 - abs(dte - 25) * 0.3

        # liquidity (tighter spread = easier to fill)
        spread_pct = (inst.ask - inst.bid) / inst.mid if inst.mid > 0 else 1.0
        liq_score = max(0, 10 - spread_pct * 20)

        # premium size (USD)
        prem_score = min(inst.premium_usd / 10, 10)

        return (
            theta_score * 2.0
            + otm_score * 1.5
            + iv_score * 1.0
            + dte_score * 1.0
            + liq_score * 1.5
            + prem_score * 1.0
        )

    def _annualized_yield(self, inst: OptionInstrument) -> float:
        """Annualized premium yield if selling this option."""
        if inst.underlying_price <= 0 or inst.days_to_expiry <= 0:
            return 0.0
        collateral = inst.strike if inst.option_type == OptionType.PUT else inst.underlying_price
        if collateral <= 0:
            return 0.0
        period_yield = inst.premium_usd / collateral
        return period_yield * (365 / inst.days_to_expiry)

    def format_report(self, results: list[ScanResult], top_n: int = 15) -> str:
        if not results:
            return "No options found matching criteria."

        lines = [
            "═" * 80,
            "  OPTIONS SCANNER — Best Options to Sell (TradingWarz Theta Harvest)",
            "═" * 80,
            "",
            f"{'Instrument':<28} {'Type':>4} {'Strike':>8} {'DTE':>4} "
            f"{'Delta':>7} {'IV':>6} {'Prem$':>7} {'AnnYld':>7} {'Score':>6}",
            "─" * 80,
        ]

        for r in results[:top_n]:
            inst = r.instrument
            lines.append(
                f"{inst.instrument_name:<28} "
                f"{'P' if inst.option_type == OptionType.PUT else 'C':>4} "
                f"${inst.strike:>7,.0f} "
                f"{inst.days_to_expiry:>4.0f} "
                f"{inst.greeks.delta:>+7.3f} "
                f"{inst.iv:>5.0f}% "
                f"${inst.premium_usd:>6.1f} "
                f"{r.annualized_yield:>6.1%} "
                f"{r.score:>6.1f}"
            )

        lines.append("─" * 80)
        lines.append(f"Total candidates: {len(results)}")
        if results:
            best = results[0]
            lines.append(
                f"Top pick: {best.instrument.instrument_name} — "
                f"${best.instrument.premium_usd:.1f} premium, "
                f"{best.annualized_yield:.1%} annualized"
            )
        lines.append("═" * 80)
        return "\n".join(lines)
