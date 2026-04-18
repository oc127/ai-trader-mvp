from __future__ import annotations

import time
from datetime import datetime, timezone

from src.data.store import DataStore
from src.hl_client.rest import HLRestClient
from src.logger import get_logger

log = get_logger(__name__)


class FundingCollector:
    def __init__(self, client: HLRestClient, store: DataStore, coins: list[str]) -> None:
        self._client = client
        self._store = store
        self._coins = coins

    def collect_current(self) -> None:
        for coin in self._coins:
            try:
                now_ms = int(time.time() * 1000)
                one_hour_ago = now_ms - 3600_000
                rates = self._client.get_funding_rates(coin, one_hour_ago, now_ms)
                if rates:
                    self._store.save_funding_rates(rates)
                    log.debug("Collected funding", extra={"coin": coin, "count": len(rates)})
            except Exception:
                log.exception("Failed to collect funding", extra={"coin": coin})

    def backfill(self, start: datetime, end: datetime | None = None) -> int:
        end = end or datetime.now(timezone.utc)
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        total = 0

        for coin in self._coins:
            try:
                rates = self._client.get_funding_rates(coin, start_ms, end_ms)
                if rates:
                    self._store.save_funding_rates(rates)
                    total += len(rates)
                    log.info("Backfilled funding", extra={"coin": coin, "count": len(rates)})
            except Exception:
                log.exception("Failed to backfill", extra={"coin": coin})

        return total
