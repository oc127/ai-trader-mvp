"""Cross-exchange funding rate monitor.

Compares funding rates across Hyperliquid and Gate.io,
outputs a comparison table with annualized APY, and optionally
sends a daily summary via Telegram.

Usage:
    python -m src.monitor.rate_monitor
    python -m src.monitor.rate_monitor --coins ETH BTC SOL
"""
from __future__ import annotations

import argparse
import os
import time
from datetime import datetime, timezone

import httpx

from src.logger import get_logger

log = get_logger(__name__)

# Default coins to compare
DEFAULT_COINS = ["ETH", "BTC", "SOL", "DOGE", "ARB"]

# Gate.io public API (no auth required)
GATE_FUNDING_URL = "https://api.gateio.ws/api/v4/futures/usdt/funding_rate"

# Hyperliquid public API
HL_INFO_URL = "https://api.hyperliquid.xyz/info"

# Funding periods per year by exchange
# Hyperliquid: every 8 hours (3x/day) -> 1095 periods/year
HL_PERIODS_PER_YEAR = 3 * 365
# Gate.io: every 8 hours (3x/day) -> 1095 periods/year
GATE_PERIODS_PER_YEAR = 3 * 365


def fetch_hl_funding_rates() -> dict[str, float]:
    """Fetch current predicted funding rates from Hyperliquid.

    Returns a dict mapping coin symbol to funding rate (as a decimal).
    """
    try:
        resp = httpx.post(HL_INFO_URL, json={"type": "metaAndAssetCtxs"}, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        # data is [meta, [asset_ctx, ...]]
        if not isinstance(data, list) or len(data) < 2:
            log.warning("Unexpected HL response format")
            return {}

        meta = data[0]
        asset_ctxs = data[1]
        coins = [u["name"] for u in meta.get("universe", [])]

        rates = {}
        for i, ctx in enumerate(asset_ctxs):
            if i < len(coins):
                rate = float(ctx.get("funding", 0))
                rates[coins[i]] = rate

        return rates
    except Exception:
        log.exception("Failed to fetch HL funding rates")
        return {}


def fetch_gate_funding_rate(coin: str) -> float | None:
    """Fetch current funding rate for a coin from Gate.io.

    Gate.io contract format: {COIN}_USDT (e.g., ETH_USDT, BTC_USDT).
    Returns the funding rate as a decimal, or None on failure.
    """
    contract = f"{coin}_USDT"
    try:
        resp = httpx.get(
            GATE_FUNDING_URL,
            params={"contract": contract, "limit": 1},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data and isinstance(data, list) and len(data) > 0:
            return float(data[0].get("r", 0))
        return None
    except Exception:
        log.debug("Failed to fetch Gate.io rate for %s", coin)
        return None


def fetch_gate_funding_rates(coins: list[str]) -> dict[str, float]:
    """Fetch funding rates from Gate.io for multiple coins."""
    rates = {}
    for coin in coins:
        rate = fetch_gate_funding_rate(coin)
        if rate is not None:
            rates[coin] = rate
        # Small delay to avoid rate limiting
        time.sleep(0.1)
    return rates


def build_comparison_table(
    coins: list[str],
    hl_rates: dict[str, float],
    gate_rates: dict[str, float],
) -> str:
    """Build a formatted comparison table of funding rates."""
    lines = []
    header = (
        f"{'Coin':>6} | {'HL Rate':>12} | {'Gate Rate':>12} | "
        f"{'Best':>6} | {'HL APY':>10} | {'Gate APY':>10} | {'Spread APY':>10}"
    )
    sep = "-" * len(header)
    lines.append(header)
    lines.append(sep)

    for coin in coins:
        hl_rate = hl_rates.get(coin)
        gate_rate = gate_rates.get(coin)

        if hl_rate is None and gate_rate is None:
            continue

        hl_str = f"{hl_rate:.6f}" if hl_rate is not None else "N/A"
        gate_str = f"{gate_rate:.6f}" if gate_rate is not None else "N/A"

        hl_apy = hl_rate * HL_PERIODS_PER_YEAR if hl_rate is not None else 0
        gate_apy = gate_rate * GATE_PERIODS_PER_YEAR if gate_rate is not None else 0

        hl_apy_str = f"{hl_apy:.2%}" if hl_rate is not None else "N/A"
        gate_apy_str = f"{gate_apy:.2%}" if gate_rate is not None else "N/A"

        # Best exchange to short (highest positive rate = shorts earn more)
        if hl_rate is not None and gate_rate is not None:
            best = "HL" if hl_rate > gate_rate else "Gate"
            spread_apy = abs(hl_rate - gate_rate) * max(HL_PERIODS_PER_YEAR, GATE_PERIODS_PER_YEAR)
            spread_str = f"{spread_apy:.2%}"
        elif hl_rate is not None:
            best = "HL"
            spread_str = "N/A"
        else:
            best = "Gate"
            spread_str = "N/A"

        lines.append(
            f"{coin:>6} | {hl_str:>12} | {gate_str:>12} | "
            f"{best:>6} | {hl_apy_str:>10} | {gate_apy_str:>10} | {spread_str:>10}"
        )

    return "\n".join(lines)


def build_telegram_summary(
    coins: list[str],
    hl_rates: dict[str, float],
    gate_rates: dict[str, float],
) -> str:
    """Build a concise Telegram-friendly summary."""
    lines = ["Funding Rate Comparison"]
    lines.append(f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC")
    lines.append("")

    for coin in coins:
        hl_rate = hl_rates.get(coin)
        gate_rate = gate_rates.get(coin)
        if hl_rate is None and gate_rate is None:
            continue

        hl_apy = hl_rate * HL_PERIODS_PER_YEAR * 100 if hl_rate is not None else 0
        gate_apy = gate_rate * GATE_PERIODS_PER_YEAR * 100 if gate_rate is not None else 0

        best = "HL" if (hl_rate or 0) > (gate_rate or 0) else "Gate"
        lines.append(f"{coin}: HL {hl_apy:.1f}% | Gate {gate_apy:.1f}% [{best}]")

    return "\n".join(lines)


def send_telegram(message: str) -> bool:
    """Send a message via Telegram if credentials are available."""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not (bot_token and chat_id):
        return False

    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        httpx.post(url, json={"chat_id": chat_id, "text": message}, timeout=10)
        return True
    except Exception:
        log.exception("Failed to send Telegram message")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Cross-exchange funding rate monitor")
    parser.add_argument(
        "--coins",
        nargs="+",
        default=DEFAULT_COINS,
        help="Coins to compare (default: ETH BTC SOL DOGE ARB)",
    )
    parser.add_argument("--telegram", action="store_true", help="Send summary via Telegram")
    args = parser.parse_args()

    coins = [c.upper() for c in args.coins]

    print(f"Fetching funding rates for: {', '.join(coins)}")
    print()

    hl_rates = fetch_hl_funding_rates()
    gate_rates = fetch_gate_funding_rates(coins)

    # Filter to requested coins only
    hl_filtered = {c: hl_rates[c] for c in coins if c in hl_rates}

    table = build_comparison_table(coins, hl_filtered, gate_rates)
    print(table)
    print()

    # Show timestamp
    print(f"Timestamp: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")

    # Optionally send Telegram
    if args.telegram:
        summary = build_telegram_summary(coins, hl_filtered, gate_rates)
        if send_telegram(summary):
            print("Telegram summary sent.")
        else:
            print("Telegram not configured (set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID).")


if __name__ == "__main__":
    main()
