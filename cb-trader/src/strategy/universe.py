from __future__ import annotations

from datetime import date
from typing import Any

from src.logger import get_logger
from src.models import BondSnapshot

logger = get_logger(__name__)


class UniverseFilter:
    def __init__(self, config: dict[str, Any]) -> None:
        uc = config.get("universe", {})
        self.max_premium_rate: float = uc.get("max_premium_rate", 0.20)
        self.min_volume_cny: float = uc.get("min_volume_cny", 50_000_000)
        self.min_price: float = uc.get("min_price", 100)
        self.max_price: float = uc.get("max_price", 200)
        self.min_remaining_years: float = uc.get("min_remaining_years", 0.5)
        self.min_days_since_ipo: int = uc.get("min_days_since_ipo", 20)
        self.max_count: int = uc.get("max_count", 20)

    def filter(self, snapshots: list[BondSnapshot], today: date | None = None) -> list[BondSnapshot]:
        if today is None:
            today = date.today()

        passed = []
        for s in snapshots:
            if not self._passes(s, today):
                continue
            passed.append(s)

        passed.sort(key=lambda s: s.volume_cny, reverse=True)

        selected = passed[: self.max_count]
        logger.info(
            "Universe filter: %d candidates → %d passed → %d selected",
            len(snapshots),
            len(passed),
            len(selected),
        )
        return selected

    def _passes(self, s: BondSnapshot, today: date) -> bool:
        if s.price <= 0:
            return False
        if s.premium_rate > self.max_premium_rate:
            return False
        if s.volume_cny < self.min_volume_cny:
            return False
        if s.price < self.min_price or s.price > self.max_price:
            return False
        if s.remaining_years < self.min_remaining_years:
            return False
        return True
