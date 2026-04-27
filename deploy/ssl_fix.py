"""SSL certificate fix for VPS environments with SSL inspection proxies.

Import this module BEFORE any SDK imports to patch SSL globally.
Works by forcing urllib3 and requests to use the system CA bundle
instead of certifi's bundled certificates.

Usage:
    import deploy.ssl_fix  # must be first import
    from hyperliquid.info import Info  # now uses system CA
"""
from __future__ import annotations

import os
import ssl
import sys


def _find_system_ca() -> str | None:
    candidates = [
        "/etc/ssl/certs/ca-certificates.crt",  # Debian/Ubuntu
        "/etc/pki/tls/certs/ca-bundle.crt",    # RHEL/CentOS
        "/etc/ssl/cert.pem",                     # macOS/Alpine
        "/etc/ssl/certs/ca-bundle.crt",          # OpenSUSE
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def patch_ssl() -> bool:
    """Patch SSL to use system CA bundle. Returns True if patch was applied."""
    system_ca = _find_system_ca()
    if system_ca is None:
        return False

    os.environ["SSL_CERT_FILE"] = system_ca
    os.environ["REQUESTS_CA_BUNDLE"] = system_ca
    os.environ["CURL_CA_BUNDLE"] = system_ca

    # Verify the system CA actually works before patching
    try:
        ctx = ssl.create_default_context(cafile=system_ca)
        stats = ctx.cert_store_stats()
        if stats.get("x509_ca", 0) == 0:
            return False
    except Exception:
        return False

    # Patch certifi to return system CA path
    try:
        import certifi
        certifi.where = lambda: system_ca  # type: ignore[assignment]
        # Also patch the core module attribute used by some libraries
        certifi.core.where = lambda: system_ca  # type: ignore[attr-defined,assignment]
    except (ImportError, AttributeError):
        pass

    # Patch urllib3 if already imported
    try:
        import urllib3.util.ssl_
        if hasattr(urllib3.util.ssl_, "DEFAULT_CERTS"):
            urllib3.util.ssl_.DEFAULT_CERTS = system_ca  # type: ignore[attr-defined]
    except (ImportError, AttributeError):
        pass

    # Patch httpx if available
    try:
        import httpx
        httpx._config.DEFAULT_CA_BUNDLE_PATH = system_ca  # type: ignore[attr-defined]
    except (ImportError, AttributeError):
        pass

    return True


_patched = patch_ssl()

if _patched:
    _ca = _find_system_ca()
    print(f"[ssl_fix] Using system CA: {_ca}", file=sys.stderr)
else:
    print("[ssl_fix] No system CA found, using defaults", file=sys.stderr)
