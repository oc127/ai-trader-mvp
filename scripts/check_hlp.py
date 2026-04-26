#!/usr/bin/env python3
"""HLP Vault monitoring script.

Shows vault equity, user share, recent P&L, and APY estimate
for the Hyperliquid HLP vault.

Usage:
    python scripts/check_hlp.py
    python scripts/check_hlp.py --address 0xYourAddress
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

import httpx

HLP_VAULT_ADDRESS = "0xdfc24b077bc1425ad1dea75bcb6f8158e10df303"
MAINNET_URL = "https://api.hyperliquid.xyz"


def _post(url: str, payload: dict) -> dict:
    """Send a POST request to the Hyperliquid info API."""
    resp = httpx.post(f"{url}/info", json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_vault_details(base_url: str, vault_address: str) -> dict:
    """Query vault details from the Hyperliquid info API."""
    return _post(base_url, {"type": "vaultDetails", "vaultAddress": vault_address})


def get_user_state(base_url: str, address: str) -> dict:
    """Query user account state."""
    return _post(base_url, {"type": "clearinghouseState", "user": address})


def format_usd(value: float) -> str:
    if abs(value) >= 1_000_000:
        return f"${value:,.0f}"
    return f"${value:,.2f}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Check HLP vault status")
    parser.add_argument(
        "--address",
        default=os.getenv("HL_WALLET_ADDRESS", ""),
        help="Your wallet address to check vault share (default: HL_WALLET_ADDRESS env var)",
    )
    parser.add_argument(
        "--base-url",
        default=MAINNET_URL,
        help="Hyperliquid API base URL",
    )
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    args = parser.parse_args()

    print(f"Querying HLP vault: {HLP_VAULT_ADDRESS[:10]}...{HLP_VAULT_ADDRESS[-6:]}")
    print()

    try:
        vault_data = get_vault_details(args.base_url, HLP_VAULT_ADDRESS)
    except Exception as e:
        print(f"Error querying vault: {e}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(vault_data, indent=2))
        return

    # Parse vault summary
    summary = vault_data.get("summary", {})
    portfolio = vault_data.get("portfolio", vault_data)

    vault_equity = float(summary.get("accountValue", 0))
    total_deposits = float(summary.get("totalDeposits", 0))
    pnl = float(summary.get("allTimePnl", 0))
    apr = float(summary.get("apr", 0))
    days_running = float(summary.get("daysRunning", 0))

    print("=== HLP Vault Summary ===")
    print(f"  Vault equity:    {format_usd(vault_equity)}")
    if total_deposits > 0:
        print(f"  Total deposits:  {format_usd(total_deposits)}")
    if pnl != 0:
        print(f"  All-time P&L:    {format_usd(pnl)}")
    if apr != 0:
        print(f"  APR:             {apr:.2%}")
    if days_running > 0:
        print(f"  Days running:    {days_running:.0f}")
    print()

    # Check positions
    positions = portfolio.get("positions", portfolio.get("assetPositions", []))
    if positions:
        print("=== Vault Positions ===")
        for p in positions[:10]:
            pos = p.get("position", p)
            coin = pos.get("coin", "?")
            size = float(pos.get("szi", pos.get("size", 0)))
            entry = float(pos.get("entryPx", pos.get("entry_price", 0)))
            upnl = float(pos.get("unrealizedPnl", 0))
            if abs(size) > 1e-10:
                side = "LONG" if size > 0 else "SHORT"
                print(f"  {coin:>8}: {side} {abs(size):.4f} @ {entry:,.2f}  uPnL: {format_usd(upnl)}")
        if len(positions) > 10:
            print(f"  ... and {len(positions) - 10} more positions")
        print()

    # Check user's vault share
    user_address = args.address
    if user_address:
        followers = vault_data.get("followers", [])
        user_share = None
        for f in followers:
            if f.get("user", "").lower() == user_address.lower():
                user_share = f
                break

        if user_share:
            deposit = float(user_share.get("vaultEquity", user_share.get("deposit", 0)))
            user_pnl = float(user_share.get("allTimePnl", 0))
            lock_until = user_share.get("lockupUntil")

            print("=== Your Vault Share ===")
            print(f"  Deposit value:   {format_usd(deposit)}")
            if user_pnl != 0:
                print(f"  Your P&L:        {format_usd(user_pnl)}")
            if vault_equity > 0 and deposit > 0:
                share_pct = deposit / vault_equity * 100
                print(f"  Share of vault:  {share_pct:.4f}%")
            if lock_until:
                print(f"  Lock until:      {lock_until}")
            print()
        else:
            print(f"  No deposit found for address {user_address[:10]}...")
            print()

    # APY estimate from recent data
    if apr != 0:
        print("=== APY Estimate ===")
        print(f"  Annualized APR:  {apr:.2%}")
        daily_rate = apr / 365
        print(f"  Daily rate:      {daily_rate:.4%}")
        monthly_est = apr / 12
        print(f"  Monthly est:     {monthly_est:.2%}")
    elif pnl != 0 and days_running > 0 and total_deposits > 0:
        daily_return = pnl / total_deposits / days_running
        apy_est = daily_return * 365
        print("=== APY Estimate (from P&L history) ===")
        print(f"  Daily return:    {daily_return:.4%}")
        print(f"  Est. APY:        {apy_est:.2%}")

    print()
    print(f"Timestamp: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")


if __name__ == "__main__":
    main()
