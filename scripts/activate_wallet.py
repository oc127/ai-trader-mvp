"""Activate Polymarket wallet for CLOB API trading.

This script performs the on-chain "deposit wallet flow" required before
the CLOB API will accept orders from this wallet. Steps:
1. Check MATIC balance (needed for gas)
2. Check current USDC allowances for exchange contracts
3. Send approve(MAX_UINT256) transactions for collateral token
4. Verify allowances are set

Run once per wallet. After successful execution, the bot can place orders.

Usage:
    python scripts/activate_wallet.py [--dry-run]
"""

from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

POLYGON_RPCS = [
    "https://polygon-rpc.com",
    "https://rpc.ankr.com/polygon",
    "https://polygon.llamarpc.com",
    "https://1rpc.io/matic",
]

# Polymarket contract addresses (from SDK config for chain_id=137)
COLLATERAL = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
EXCHANGE_V2 = "0xE111180000d2663C0091e4f400237545B87B996B"
NEG_RISK_EXCHANGE_V2 = "0xe2222d279d744050d28e00520010520000310F59"
NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
CONDITIONAL_TOKENS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"

# Also approve standard USDC.e on Polygon in case the collateral is that
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

MAX_UINT256 = "0x" + "f" * 64  # unlimited approval

# ERC-20 ABI fragments
APPROVE_SELECTOR = "0x095ea7b3"  # approve(address,uint256)
ALLOWANCE_SELECTOR = "0xdd62ed3e"  # allowance(address,address)
BALANCE_OF_SELECTOR = "0x70a08231"  # balanceOf(address)


def pad_address(addr: str) -> str:
    return addr.lower().replace("0x", "").zfill(64)


def pad_uint256(value: str) -> str:
    return value.replace("0x", "").zfill(64)


def get_private_key() -> str:
    key = os.getenv("POLYMARKET_PRIVATE_KEY", "")
    if not key:
        print("ERROR: POLYMARKET_PRIVATE_KEY not set in .env")
        sys.exit(1)
    return key


def get_wallet_address(private_key: str) -> str:
    from eth_account import Account
    return Account.from_key(private_key).address


def rpc_call(method: str, params: list, rpc_url: str = None) -> dict:
    """Make a JSON-RPC call to Polygon."""
    import requests

    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}

    rpcs = [rpc_url] if rpc_url else POLYGON_RPCS
    for rpc in rpcs:
        try:
            resp = requests.post(rpc, json=payload, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                if "error" in data:
                    print(f"  RPC error ({rpc}): {data['error']}")
                    continue
                return data
        except Exception as e:
            continue

    raise Exception(f"All RPCs failed for {method}")


def get_matic_balance(address: str) -> float:
    result = rpc_call("eth_getBalance", [address, "latest"])
    wei = int(result["result"], 16)
    return wei / 1e18


def get_erc20_balance(token: str, wallet: str) -> float:
    data = BALANCE_OF_SELECTOR + "000000000000000000000000" + pad_address(wallet)
    result = rpc_call("eth_call", [{"to": token, "data": data}, "latest"])
    raw = int(result["result"], 16)
    return raw / 1e6


def get_allowance(token: str, owner: str, spender: str) -> float:
    data = ALLOWANCE_SELECTOR + "000000000000000000000000" + pad_address(owner) + "000000000000000000000000" + pad_address(spender)
    result = rpc_call("eth_call", [{"to": token, "data": data}, "latest"])
    raw = int(result["result"], 16)
    return raw / 1e6


def send_approve_tx(private_key: str, token: str, spender: str, wallet: str, dry_run: bool = False) -> str:
    """Send an ERC-20 approve transaction."""
    from eth_account import Account

    data = APPROVE_SELECTOR + "000000000000000000000000" + pad_address(spender) + pad_uint256(MAX_UINT256)

    # Get nonce
    nonce_result = rpc_call("eth_getTransactionCount", [wallet, "latest"])
    nonce = int(nonce_result["result"], 16)

    # Get gas price
    gas_result = rpc_call("eth_gasPrice", [])
    gas_price = int(gas_result["result"], 16)
    # Add 20% buffer
    gas_price = int(gas_price * 1.2)

    tx = {
        "to": token,
        "value": 0,
        "gas": 60000,
        "gasPrice": gas_price,
        "nonce": nonce,
        "chainId": 137,
        "data": bytes.fromhex(data[2:]),
    }

    if dry_run:
        cost_matic = (60000 * gas_price) / 1e18
        print(f"  [DRY RUN] Would approve {spender[:10]}... on {token[:10]}...")
        print(f"  Gas cost estimate: ~{cost_matic:.6f} MATIC")
        return "dry_run"

    signed = Account.sign_transaction(tx, private_key)
    raw_tx = "0x" + signed.raw_transaction.hex()

    result = rpc_call("eth_sendRawTransaction", [raw_tx])
    if "result" in result:
        tx_hash = result["result"]
        print(f"  TX sent: {tx_hash}")
        return tx_hash
    else:
        raise Exception(f"TX failed: {result}")


def wait_for_tx(tx_hash: str, timeout: int = 60) -> bool:
    """Wait for transaction confirmation."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            result = rpc_call("eth_getTransactionReceipt", [tx_hash])
            if result.get("result"):
                status = int(result["result"]["status"], 16)
                if status == 1:
                    print(f"  Confirmed in block {int(result['result']['blockNumber'], 16)}")
                    return True
                else:
                    print(f"  TX REVERTED!")
                    return False
        except Exception:
            pass
        time.sleep(2)

    print(f"  Timeout waiting for {tx_hash}")
    return False


def main():
    dry_run = "--dry-run" in sys.argv

    print("=" * 60)
    print("Polymarket Wallet Activation Script")
    print("=" * 60)

    if dry_run:
        print("\n*** DRY RUN MODE — no transactions will be sent ***\n")

    # 1. Load wallet
    private_key = get_private_key()
    wallet = get_wallet_address(private_key)
    print(f"\nWallet address: {wallet}")

    # 2. Check MATIC balance
    matic = get_matic_balance(wallet)
    print(f"MATIC balance:  {matic:.6f} MATIC")
    if matic < 0.01 and not dry_run:
        print("\nERROR: Need at least 0.01 MATIC for gas (~4 approve transactions)")
        print("Send MATIC to your wallet first, then re-run this script.")
        sys.exit(1)

    # 3. Check collateral balances
    print(f"\n--- Token Balances ---")
    col_bal = get_erc20_balance(COLLATERAL, wallet)
    print(f"Collateral ({COLLATERAL[:10]}...): ${col_bal:.2f}")
    usdc_e_bal = get_erc20_balance(USDC_E, wallet)
    print(f"USDC.e ({USDC_E[:10]}...):         ${usdc_e_bal:.2f}")

    # 4. Check current allowances
    print(f"\n--- Current Allowances ---")
    spenders = {
        "Exchange V2": EXCHANGE_V2,
        "NegRisk Exchange V2": NEG_RISK_EXCHANGE_V2,
        "NegRisk Adapter": NEG_RISK_ADAPTER,
        "Conditional Tokens": CONDITIONAL_TOKENS,
    }

    needs_approval = []
    for name, spender in spenders.items():
        allowance = get_allowance(COLLATERAL, wallet, spender)
        status = "OK (unlimited)" if allowance > 1e12 else f"${allowance:.2f}"
        print(f"  {name}: {status}")
        if allowance < 1e12:
            needs_approval.append((name, spender))

    if not needs_approval:
        print("\n All allowances already set! Wallet is activated.")
        print("If you still get 'maker address not allowed', the issue may be:")
        print("  - Wrong signature_type (try 0 for EOA)")
        print("  - Wallet not recognized by Polymarket (need to trade on web UI first)")
        print("  - Geoblocking (use VPN)")
        return

    # 5. Send approvals
    print(f"\n--- Sending Approvals ({len(needs_approval)} needed) ---")
    for name, spender in needs_approval:
        print(f"\nApproving {name} ({spender[:12]}...)...")
        try:
            tx_hash = send_approve_tx(private_key, COLLATERAL, spender, wallet, dry_run)
            if not dry_run and tx_hash != "dry_run":
                success = wait_for_tx(tx_hash)
                if not success:
                    print(f"  FAILED — continuing with next approval")
                time.sleep(1)  # brief pause between TXs
        except Exception as e:
            print(f"  ERROR: {e}")

    # 6. Also approve USDC.e if user has balance there
    if usdc_e_bal > 0:
        print(f"\n--- Also approving USDC.e (you have ${usdc_e_bal:.2f}) ---")
        for name, spender in spenders.items():
            usdc_allowance = get_allowance(USDC_E, wallet, spender)
            if usdc_allowance < 1e12:
                print(f"\nApproving USDC.e for {name}...")
                try:
                    tx_hash = send_approve_tx(private_key, USDC_E, spender, wallet, dry_run)
                    if not dry_run and tx_hash != "dry_run":
                        wait_for_tx(tx_hash)
                        time.sleep(1)
                except Exception as e:
                    print(f"  ERROR: {e}")

    # 7. Verify
    if not dry_run:
        print(f"\n--- Verifying Allowances ---")
        time.sleep(3)
        all_ok = True
        for name, spender in spenders.items():
            allowance = get_allowance(COLLATERAL, wallet, spender)
            ok = allowance > 1e12
            print(f"  {name}: {'OK' if ok else 'FAILED'}")
            if not ok:
                all_ok = False

        if all_ok:
            print("\n Wallet activated! You can now run the bot with:")
            print("    python scripts/run_polymarket.py --live")
        else:
            print("\n Some approvals may have failed. Check Polygonscan for details.")

    print("\n--- Config Reminder ---")
    print("Make sure config/polymarket_live.yaml has:")
    print("  signature_type: 0  # EOA (private key = wallet)")
    print(f"  # funder_address: \"{wallet}\"  # (optional, SDK derives it)")


if __name__ == "__main__":
    main()
