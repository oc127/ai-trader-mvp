from __future__ import annotations

from src.hl_client.types import FundingRate
from src.logger import get_logger

log = get_logger(__name__)


def validate_funding_rate(rate: FundingRate) -> bool:
    if abs(rate.rate) > 0.01:
        log.debug("Suspicious funding rate: %.6f for %s", rate.rate, rate.coin)
        return False
    if rate.rate == 0.0 and rate.premium == 0.0:
        log.debug("Zero rate and premium for %s, likely stale", rate.coin)
        return False
    return True


def validate_price(coin: str, price: float) -> bool:
    if price <= 0:
        log.debug("Non-positive price for %s: %.8f", coin, price)
        return False
    return True


def validate_order_sanity(coin: str, size: float, price: float, max_notional_usd: float = 50000) -> bool:
    notional = size * price
    if notional > max_notional_usd:
        log.warning("Order notional $%.0f exceeds max $%.0f for %s", notional, max_notional_usd, coin)
        return False
    if size <= 0:
        log.debug("Non-positive size for %s: %.8f", coin, size)
        return False
    return True
