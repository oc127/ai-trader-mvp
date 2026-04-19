"""Check Hyperliquid account balance and positions."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config
from src.hl_client.rest import HLRestClient


def main() -> None:
    cfg = load_config("testnet")
    client = HLRestClient(cfg)

    print(f"Address: {client.address}")
    print()

    account = client.get_account_state()
    print(f"Equity:       ${account.equity:,.2f}")
    print(f"Available:    ${account.available_balance:,.2f}")
    print(f"Margin Used:  ${account.margin_used:,.2f}")
    print(f"Margin Util:  {account.margin_utilization:.1%}")
    print()

    if account.positions:
        print("Positions:")
        for p in account.positions:
            print(f"  {p.coin}: size={p.size:.4f}, entry=${p.entry_price:,.2f}, PnL=${p.unrealized_pnl:+,.2f}")
    else:
        print("No open positions.")

    print()
    spot = client.get_spot_balances()
    if spot:
        print("Spot Balances:")
        for b in spot:
            print(f"  {b.coin}: {b.total:.4f} (available: {b.available:.4f})")
    else:
        print("No spot balances.")


if __name__ == "__main__":
    main()
