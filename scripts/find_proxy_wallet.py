"""Find the Polymarket proxy wallet address for your signer key.

Polymarket embedded wallets work like this:
- Your private key = "signer" (signs transactions)
- A separate proxy wallet (smart contract) = holds your funds
- The CLOB API maps signer → proxy wallet internally

This script tries multiple methods to find the proxy wallet:
1. CLOB API with signature_type=1 (API resolves signer → proxy)
2. Gamma API user profile lookup
3. Polygonscan transaction history

Usage:
    uv run python scripts/find_proxy_wallet.py
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
GAMMA_API = "https://gamma-api.polymarket.com"


def main():
    import requests
    from eth_account import Account

    private_key = os.getenv("POLYMARKET_PRIVATE_KEY", "")
    if not private_key:
        print("ERROR: POLYMARKET_PRIVATE_KEY not set in .env")
        sys.exit(1)

    signer_address = Account.from_key(private_key).address
    print(f"Signer (EOA) address: {signer_address}")
    print()

    # ─── Method 1: Try CLOB API with signature_type=1 ───
    print("Method 1: CLOB API with signature_type=1 (POLY_PROXY)...")
    try:
        from py_clob_client_v2 import ClobClient
        from py_clob_client_v2.clob_types import AssetType, BalanceAllowanceParams

        # With signature_type=1, don't pass funder — let API resolve it
        client = ClobClient(
            host=CLOB_API,
            chain_id=137,
            key=private_key,
            signature_type=1,  # POLY_PROXY
        )
        creds = client.create_or_derive_api_key()
        client.set_api_creds(creds)

        params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        bal = client.get_balance_allowance(params)
        print(f"  Response: {json.dumps(bal, indent=2)}")

        if isinstance(bal, dict):
            balance = float(bal.get("balance", 0) or 0)
            if balance > 0:
                # Convert from wei if needed
                display = balance / 1e6 if balance > 1_000_000 else balance
                print(f"  → Balance found: ${display:.2f}")
                print(f"  → signature_type=1 works! Set this in config.")
    except Exception as e:
        print(f"  Failed: {e}")

    print()

    # ─── Method 2: Try CLOB API with signature_type=2 ───
    print("Method 2: CLOB API with signature_type=2 (POLY_GNOSIS_SAFE)...")
    try:
        client2 = ClobClient(
            host=CLOB_API,
            chain_id=137,
            key=private_key,
            signature_type=2,  # POLY_GNOSIS_SAFE
        )
        creds2 = client2.create_or_derive_api_key()
        client2.set_api_creds(creds2)

        bal2 = client2.get_balance_allowance(params)
        print(f"  Response: {json.dumps(bal2, indent=2)}")

        if isinstance(bal2, dict):
            balance = float(bal2.get("balance", 0) or 0)
            if balance > 0:
                display = balance / 1e6 if balance > 1_000_000 else balance
                print(f"  → Balance found: ${display:.2f}")
                print(f"  → signature_type=2 works!")
    except Exception as e:
        print(f"  Failed: {e}")

    print()

    # ─── Method 3: Gamma API user profile ───
    print("Method 3: Gamma API profile lookup...")
    try:
        resp = requests.get(
            f"{GAMMA_API}/profile/{signer_address}",
            timeout=10,
        )
        if resp.status_code == 200:
            profile = resp.json()
            print(f"  Profile: {json.dumps(profile, indent=2)[:500]}")
            proxy = profile.get("proxyWallet") or profile.get("proxy_wallet") or profile.get("depositAddress")
            if proxy:
                print(f"\n  → PROXY WALLET FOUND: {proxy}")
        else:
            print(f"  Status {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        print(f"  Failed: {e}")

    print()

    # ─── Method 4: Try common Gamma API endpoints ───
    print("Method 4: Gamma API user endpoints...")
    endpoints = [
        f"/user?address={signer_address}",
        f"/users?addresses={signer_address}",
        f"/profile?address={signer_address}",
    ]
    for ep in endpoints:
        try:
            resp = requests.get(f"{GAMMA_API}{ep}", timeout=10)
            if resp.status_code == 200 and resp.text.strip() not in ["", "[]", "{}", "null"]:
                data = resp.json()
                print(f"  {ep}: {json.dumps(data, indent=2)[:300]}")
                # Look for proxy wallet in response
                if isinstance(data, dict):
                    for key in ["proxyWallet", "proxy_wallet", "depositAddress", "smartContractAddress", "address"]:
                        val = data.get(key)
                        if val and val.lower() != signer_address.lower():
                            print(f"\n  → POSSIBLE PROXY: {val} (from field '{key}')")
                elif isinstance(data, list) and data:
                    for item in data:
                        if isinstance(item, dict):
                            for key in ["proxyWallet", "proxy_wallet", "depositAddress", "smartContractAddress"]:
                                val = item.get(key)
                                if val and val.lower() != signer_address.lower():
                                    print(f"\n  → POSSIBLE PROXY: {val} (from field '{key}')")
                break
        except Exception as e:
            pass

    print()

    # ─── Method 5: Check Polygonscan for proxy deployment ───
    print("Method 5: Checking Polygonscan for transactions...")
    try:
        # Free Polygonscan API (limited but works)
        resp = requests.get(
            "https://api.polygonscan.com/api",
            params={
                "module": "account",
                "action": "txlist",
                "address": signer_address,
                "startblock": "0",
                "endblock": "99999999",
                "page": "1",
                "offset": "10",
                "sort": "asc",
            },
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            txs = data.get("result", [])
            if isinstance(txs, list) and txs:
                print(f"  Found {len(txs)} transactions")
                for tx in txs[:5]:
                    to_addr = tx.get("to", "")
                    func = tx.get("functionName", "")[:40]
                    print(f"    → to={to_addr[:20]}... fn={func}")
                    # Contract creation (proxy deployment) has empty 'to'
                    if not to_addr and tx.get("contractAddress"):
                        print(f"\n  → DEPLOYED CONTRACT: {tx['contractAddress']}")
            else:
                print(f"  No transactions found (result: {data.get('message', '')})")
                print("  This is normal if the proxy was deployed by a factory")
    except Exception as e:
        print(f"  Failed: {e}")

    print()

    # ─── Method 6: Try CLOB API order placement error message ───
    print("Method 6: Attempting order to get detailed error...")
    try:
        from py_clob_client_v2 import ClobClient, OrderArgs, OrderType
        from py_clob_client_v2 import Side as ClobSide

        # Use a real token_id from an active market
        resp = requests.get(f"{GAMMA_API}/markets", params={"limit": 1, "active": True}, timeout=10)
        markets = resp.json()
        if markets:
            m = markets[0]
            tokens = m.get("clobTokenIds", [])
            if isinstance(tokens, str):
                tokens = json.loads(tokens)
            if tokens:
                token_id = tokens[0]
                print(f"  Using token: {token_id[:20]}...")

                for sig_type in [0, 1]:
                    sig_name = "EOA" if sig_type == 0 else "POLY_PROXY"
                    try:
                        tc = ClobClient(
                            host=CLOB_API,
                            chain_id=137,
                            key=private_key,
                            signature_type=sig_type,
                        )
                        tc_creds = tc.create_or_derive_api_key()
                        tc.set_api_creds(tc_creds)

                        result = tc.create_and_post_order(
                            order_args=OrderArgs(
                                token_id=token_id,
                                price=0.01,
                                size=1.0,
                                side=ClobSide.BUY,
                            ),
                            order_type=OrderType.GTC,
                        )
                        print(f"  sig_type={sig_type} ({sig_name}): ORDER PLACED! {result}")
                        # If this works, we're done!
                        print(f"\n{'='*60}")
                        print(f"SUCCESS! signature_type={sig_type} ({sig_name}) WORKS!")
                        print(f"Update config/polymarket_live.yaml: signature_type: {sig_type}")
                        print(f"{'='*60}")
                        # Cancel it immediately
                        order_id = result.get("orderID", result.get("id", ""))
                        if order_id:
                            tc.cancel(order_id)
                            print(f"  (cancelled test order {order_id})")
                        return
                    except Exception as e:
                        err = str(e)
                        print(f"  sig_type={sig_type} ({sig_name}): {err[:150]}")
    except Exception as e:
        print(f"  Failed: {e}")

    # ─── Summary ───
    print()
    print("=" * 60)
    print("NEXT STEPS:")
    print("─" * 60)
    print(f"""
Your signer key derives to: {signer_address}
This address has $0 balance in Polymarket's system.

Your $59 is in a PROXY WALLET (different address).
To find it, do ONE of these on polymarket.com:

Option A (easiest):
  1. Go to polymarket.com → open browser DevTools (F12)
  2. Go to Console tab, type: localStorage
  3. Look for keys containing "wallet" or "address"
  4. Your proxy address will be there (starts with 0x, different from above)

Option B:
  1. Go to polymarket.com → Portfolio → any position
  2. Click "View on Polygonscan"
  3. The address in the URL is your proxy wallet

Option C:
  1. Go to polymarket.com → Settings → Deposit
  2. The deposit address shown IS your proxy wallet

Once you have the proxy address, update .env:
  POLYMARKET_FUNDER_ADDRESS=0x<your-proxy-wallet>

And set signature_type: 1 in config/polymarket_live.yaml
""")


if __name__ == "__main__":
    main()
