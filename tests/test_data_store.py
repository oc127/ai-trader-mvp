import tempfile
from datetime import datetime, timezone

from src.data.store import DataStore
from src.hl_client.types import FundingRate


def test_store_and_retrieve_funding():
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        store = DataStore(f.name)
        rate = FundingRate(
            coin="ETH",
            rate=0.0001,
            premium=0.00005,
            timestamp=datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
        )
        store.save_funding_rate(rate)

        history = store.get_funding_history("ETH")
        assert len(history) == 1
        assert history[0].coin == "ETH"
        assert history[0].rate == 0.0001

        latest = store.get_latest_funding_rate("ETH")
        assert latest is not None
        assert latest.rate == 0.0001

        store.close()


def test_store_duplicate_funding():
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        store = DataStore(f.name)
        rate = FundingRate(
            coin="BTC",
            rate=0.0002,
            timestamp=datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
        )
        store.save_funding_rate(rate)
        store.save_funding_rate(rate)

        history = store.get_funding_history("BTC")
        assert len(history) == 1
        store.close()


def test_pnl_snapshot():
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        store = DataStore(f.name)
        store.save_pnl_snapshot(10000, 5000, 100, 200, 50)

        history = store.get_pnl_history()
        assert len(history) == 1
        assert history[0]["equity"] == 10000
        store.close()
