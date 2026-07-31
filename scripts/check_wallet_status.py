"""Diagnose Polymarket wallet status via CLOB API.

Checks:
1. API connectivity (GET /ok)
2. API key derivation (auth level)
3. Balance-allowance status
4. Closed-only (ban) status
5. Signature type compatibility

Usage:
    uv run python scripts/check_wallet_status.py
"""

from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

CLOB_API = "https://clob.polymarket.com"


def main():
    print("=" * 60)
    print("Polymarket Wallet Diagnostic")
    print("=" * 60)

    # Load key
    private_key = os.getenv("POLYMARKET_PRIVATE_KEY", "")
    if not private_key:
        print("ERROR: POLYMARKET_PRIVATE_KEY not set in .env")
        sys.exit(1)

    funder = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")

    from eth_account import Account
    wallet = Account.from_key(private_key).address
    print(f"\nSigner address:  {wallet}")
    print(f"Funder address:  {funder or '(not set, defaults to signer)'}")
    print(f"Same address:    {wallet.lower() == funder.lower() if funder else 'N/A'}")

    # Init SDK
    from py_clob_client_v2 import ClobClient

    # Test all signature types
    for sig_type in [0, 1, 2]:
        sig_names = {0: "EOA", 1: "POLY_PROXY", 2: "POLY_GNOSIS_SAFE"}
        print(f"\n{'─' * 60}")
        print(f"Testing signature_type={sig_type} ({sig_names[sig_type]})")
        print(f"{'─' * 60}")

        kwargs = {
            "host": CLOB_API,
            "chain_id": 137,
            "key": private_key,
            "signature_type": sig_type,
        }
        if funder:
            kwargs["funder"] = funder

        try:
            client = ClobClient(**kwargs)
        except Exception as e:
            print(f"  Client init failed: {e}")
            continue

        # 1. Check /ok
        try:
            ok = client.get_ok()
            print(f"  /ok: {ok}")
        except Exception as e:
            print(f"  /ok FAILED: {e}")
            continue

        # 2. Derive API key
        try:
            creds = client.create_or_derive_api_key()
            if hasattr(creds, 'api_key'):
                api_key = creds.api_key
            elif isinstance(creds, dict):
                api_key = creds.get("apiKey", creds.get("api_key", ""))
            else:
                api_key = str(creds)[:20]
            print(f"  API key: {api_key[:12]}...")
            client.set_api_creds(creds)
        except Exception as e:
            print(f"  API key derivation FAILED: {e}")
            continue

        # 3. Balance-allowance
        try:
            from py_clob_client_v2.clob_types import AssetType, BalanceAllowanceParams
            params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            bal = client.get_balance_allowance(params)
            print(f"  Balance-allowance: {json.dumps(bal, indent=4)}")
        except Exception as e:
            print(f"  Balance-allowance FAILED: {e}")

        # 4. Check closed-only (ban status)
        try:
            import requests
            from py_clob_client_v2.headers import create_level_2_headers
            from py_clob_client_v2.clob_types import RequestArgs

            request_args = RequestArgs(
                method="GET",
                request_path="/auth/ban-status/closed-only",
            )
            headers = create_level_2_headers(client.signer, client.creds, request_args)
            resp = requests.get(
                f"{CLOB_API}/auth/ban-status/closed-only",
                headers=headers,
                timeout=10,
            )
            print(f"  Ban status: {resp.status_code} → {resp.text[:200]}")
        except Exception as e:
            print(f"  Ban status check FAILED: {e}")

        # 5. Try update_balance_allowance
        try:
            result = client.update_balance_allowance(params)
            print(f"  Update balance: {json.dumps(result, indent=4)}")
        except Exception as e:
            print(f"  Update balance FAILED: {e}")

        # 6. Try placing a tiny order (will fail, but error message is diagnostic)
        try:
            from py_clob_client_v2.order_builder.constants import get_contract_config
            config = get_contract_config(137)
            print(f"\n  Contract config:")
            print(f"    exchange_v2:          {config.exchange_v2}")
            print(f"    neg_risk_exchange_v2: {config.neg_risk_exchange_v2}")
            print(f"    collateral:           {config.collateral}")
            print(f"    conditional_tokens:   {config.conditional_tokens}")
        except Exception as e:
            print(f"  Contract config: {e}")

        # 7. Get open orders (tests L2 auth)
        try:
            orders = client.get_orders()
            count = len(orders) if isinstance(orders, list) else 0
            print(f"  Open orders: {count}")
        except Exception as e:
            print(f"  Get orders FAILED: {e}")

        # Only need to test once if basic auth works
        if api_key:
            break

    print(f"\n{'=' * 60}")
    print("DIAGNOSIS:")
    print("─" * 60)
    print("""
If balance shows 0 and you have funds on polymarket.com:
  → Your web account uses a DIFFERENT wallet (proxy/deposit wallet)
  → The private key you exported may be for a sub-wallet

If ban-status shows 'closed_only: true':
  → Your region is blocked. Use VPN and try again.

If 'maker address not allowed' persists with signature_type=0:
  → Try signature_type=1 with a DIFFERENT funder address
  → Your Polymarket proxy wallet address (visible in web app) may differ
     from your private key's derived address

To find your actual proxy wallet address:
  → Go to polymarket.com → Profile → look at your deposit address
  → Or check your transaction history on Polygonscan
""")


if __name__ == "__main__":
    main()
