"""Pre-deployment health check for all trading bots.

Validates: credentials, connectivity, config, and test suite.
"""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

BOTS = {
    "hyperliquid": {
        "dir": "/home/user/ai-trader-mvp",
        "env_vars": ["HL_WALLET_ADDRESS"],
        "test_cmd": ["uv", "run", "pytest", "--tb=short", "-q"],
        "api_test": "hl_api_check",
    },
    "deribit": {
        "dir": "/home/user/deribit-mm",
        "env_vars": ["DERIBIT_CLIENT_ID", "DERIBIT_CLIENT_SECRET"],
        "test_cmd": ["uv", "run", "pytest", "--tb=short", "-q"],
        "api_test": "deribit_api_check",
    },
    "gate": {
        "dir": "/home/user/gate-trader",
        "env_vars": ["GATE_API_KEY", "GATE_API_SECRET"],
        "test_cmd": ["uv", "run", "pytest", "--tb=short", "-q"],
        "api_test": "gate_api_check",
    },
    "polymarket": {
        "dir": "/home/user/polymarket-bot",
        "env_vars": ["POLY_PRIVATE_KEY"],
        "test_cmd": ["uv", "run", "pytest", "--tb=short", "-q"],
        "api_test": "poly_api_check",
    },
}


def check_credentials(name: str, cfg: dict) -> tuple[bool, str]:
    missing = [v for v in cfg["env_vars"] if not os.getenv(v)]
    if missing:
        return False, f"Missing env vars: {', '.join(missing)}"
    return True, "All credentials set"


def check_tests(name: str, cfg: dict) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            cfg["test_cmd"],
            cwd=cfg["dir"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split("\n")
            summary = lines[-1] if lines else "passed"
            return True, summary
        else:
            return False, f"Tests failed: {result.stdout[-200:]}"
    except subprocess.TimeoutExpired:
        return False, "Tests timed out (>120s)"
    except Exception as e:
        return False, f"Error: {e}"


def check_config(name: str, cfg: dict) -> tuple[bool, str]:
    config_dir = Path(cfg["dir"]) / "config"
    yaml_files = list(config_dir.glob("*.yaml")) + list(config_dir.glob("*.yml"))
    if not yaml_files:
        return False, "No config files found"
    return True, f"{len(yaml_files)} config file(s) found"


def check_connectivity(name: str) -> tuple[bool, str]:
    """Quick connectivity check via urllib3 with system CA."""
    import urllib3

    ca_path = "/etc/ssl/certs/ca-certificates.crt"
    if os.path.isfile(ca_path):
        http = urllib3.PoolManager(ca_certs=ca_path)
    else:
        http = urllib3.PoolManager()

    endpoints = {
        "hyperliquid": "https://api.hyperliquid.xyz/info",
        "deribit": "https://www.deribit.com/api/v2/public/get_time",
        "gate": "https://api.gateio.ws/api/v4/spot/tickers?limit=1",
        "polymarket": "https://clob.polymarket.com/time",
    }

    url = endpoints.get(name)
    if not url:
        return False, "No endpoint configured"

    try:
        if name == "hyperliquid":
            resp = http.request(
                "POST", url,
                headers={"Content-Type": "application/json"},
                body=json.dumps({"type": "meta"}).encode(),
                timeout=10,
            )
        else:
            resp = http.request("GET", url, timeout=10)

        if resp.status in (200, 403):
            if resp.status == 403:
                body = resp.data.decode("utf-8", errors="replace")[:100]
                if "allowlist" in body.lower():
                    return False, f"Blocked by network allowlist (403)"
                return True, f"API reachable (403 - may need auth)"
            return True, f"API reachable ({resp.status})"
        return False, f"HTTP {resp.status}"
    except Exception as e:
        return False, f"Connection failed: {e}"


def run_health_check(bots: list[str] | None = None) -> dict[str, dict]:
    targets = bots or list(BOTS.keys())
    results = {}

    for name in targets:
        if name not in BOTS:
            continue

        cfg = BOTS[name]
        print(f"\n{'─'*40}")
        print(f"  {name.upper()}")
        print(f"{'─'*40}")

        checks = {}

        ok, msg = check_config(name, cfg)
        checks["config"] = {"ok": ok, "msg": msg}
        print(f"  Config:       {'OK' if ok else 'FAIL'} — {msg}")

        ok, msg = check_credentials(name, cfg)
        checks["credentials"] = {"ok": ok, "msg": msg}
        print(f"  Credentials:  {'OK' if ok else 'FAIL'} — {msg}")

        ok, msg = check_tests(name, cfg)
        checks["tests"] = {"ok": ok, "msg": msg}
        print(f"  Tests:        {'OK' if ok else 'FAIL'} — {msg}")

        ok, msg = check_connectivity(name)
        checks["connectivity"] = {"ok": ok, "msg": msg}
        print(f"  Connectivity: {'OK' if ok else 'FAIL'} — {msg}")

        all_ok = all(c["ok"] for c in checks.values())
        checks["ready"] = all_ok
        results[name] = checks

        status = "READY" if all_ok else "NOT READY"
        print(f"  Status:       {status}")

    print(f"\n{'='*40}")
    ready = [n for n, c in results.items() if c["ready"]]
    not_ready = [n for n, c in results.items() if not c["ready"]]
    print(f"  Ready:     {', '.join(ready) if ready else 'none'}")
    print(f"  Not ready: {', '.join(not_ready) if not_ready else 'none'}")
    print(f"{'='*40}\n")

    return results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Trading System Health Check")
    parser.add_argument("--bots", nargs="+", choices=list(BOTS.keys()))
    args = parser.parse_args()

    run_health_check(args.bots)
