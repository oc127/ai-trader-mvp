from __future__ import annotations

from src.logger import get_logger
from src.models import BondSnapshot, DailyBar

logger = get_logger(__name__)


def validate_bar(bar: DailyBar) -> bool:
    if bar.close <= 0 or bar.open <= 0:
        logger.debug("Invalid bar: non-positive price, code=%s date=%s", bar.code, bar.date)
        return False
    if bar.high < bar.low:
        logger.debug("Invalid bar: high < low, code=%s date=%s", bar.code, bar.date)
        return False
    if bar.high < max(bar.open, bar.close):
        logger.debug("Invalid bar: high below open/close, code=%s date=%s", bar.code, bar.date)
        return False
    if bar.low > min(bar.open, bar.close):
        logger.debug("Invalid bar: low above open/close, code=%s date=%s", bar.code, bar.date)
        return False
    if bar.volume < 0:
        logger.debug("Invalid bar: negative volume, code=%s date=%s", bar.code, bar.date)
        return False
    if bar.close > 1000 or bar.close < 10:
        logger.debug("Suspicious bar: price %.2f out of range, code=%s date=%s", bar.close, bar.code, bar.date)
        return False
    return True


def validate_snapshot(snapshot: BondSnapshot) -> bool:
    if not snapshot.code or len(snapshot.code) < 5:
        logger.debug("Invalid snapshot: bad code '%s'", snapshot.code)
        return False
    if snapshot.price <= 0:
        logger.debug("Invalid snapshot: non-positive price, code=%s", snapshot.code)
        return False
    if snapshot.price > 1000 or snapshot.price < 10:
        logger.debug("Suspicious snapshot: price %.2f out of range, code=%s", snapshot.price, snapshot.code)
        return False
    if snapshot.conversion_price <= 0:
        logger.debug("Invalid snapshot: non-positive conversion_price, code=%s", snapshot.code)
        return False
    if snapshot.premium_rate < -1.0 or snapshot.premium_rate > 10.0:
        logger.debug("Suspicious snapshot: premium_rate %.4f, code=%s", snapshot.premium_rate, snapshot.code)
        return False
    if snapshot.volume_cny < 0:
        logger.debug("Invalid snapshot: negative volume, code=%s", snapshot.code)
        return False
    if snapshot.remaining_years < 0:
        logger.debug("Invalid snapshot: negative remaining_years, code=%s", snapshot.code)
        return False
    return True


def filter_valid_bars(bars: list[DailyBar]) -> list[DailyBar]:
    valid = [b for b in bars if validate_bar(b)]
    rejected = len(bars) - len(valid)
    if rejected > 0:
        logger.warning("Rejected %d / %d bars during validation", rejected, len(bars))
    return valid


def filter_valid_snapshots(snapshots: list[BondSnapshot]) -> list[BondSnapshot]:
    valid = [s for s in snapshots if validate_snapshot(s)]
    rejected = len(snapshots) - len(valid)
    if rejected > 0:
        logger.warning("Rejected %d / %d snapshots during validation", rejected, len(snapshots))
    return valid
