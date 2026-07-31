"""Fetch historical convertible bond data and store in SQLite."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import load_config
from src.data.fetcher import DataFetcher
from src.data.store import DataStore
from src.logger import get_logger, setup_logging

logger = get_logger(__name__)


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(description="Fetch CB data from akshare")
    parser.add_argument("--config-dir", type=Path, default=None)
    parser.add_argument("--bonds-only", action="store_true", help="Only fetch bond list, skip bars")
    parser.add_argument("--limit", type=int, default=0, help="Max bonds to fetch bars for (0=all)")
    args = parser.parse_args()

    config = load_config(config_dir=args.config_dir)
    db_path = Path(config["data"]["db_path"])
    store = DataStore(db_path)
    fetcher = DataFetcher()

    logger.info("=== Fetching bond list from Jisilu ===")
    snapshots = fetcher.fetch_bond_list_jsl()
    if not snapshots:
        logger.error("No snapshots fetched, aborting")
        return

    store.insert_snapshots(snapshots)
    logger.info("Stored %d snapshots", len(snapshots))

    bond_infos = fetcher.snapshots_to_bond_info(snapshots)
    store.upsert_bond_info_batch(bond_infos)
    logger.info("Stored %d bond_info records", len(bond_infos))

    if args.bonds_only:
        logger.info("--bonds-only flag set, skipping bar fetching")
        return

    codes = [s.code for s in snapshots if s.volume_cny > 0]
    if args.limit > 0:
        codes = codes[: args.limit]

    logger.info("=== Fetching daily bars for %d bonds ===", len(codes))
    for i, code in enumerate(codes):
        existing = store.get_bar_count(code)
        if existing > 100:
            logger.debug("Skipping %s, already has %d bars", code, existing)
            continue

        bars = fetcher.fetch_daily_bars(code, start=None, end=None)
        if bars:
            inserted = store.insert_daily_bars(bars)
            logger.info("[%d/%d] %s: fetched %d bars, inserted %d new", i + 1, len(codes), code, len(bars), inserted)
        else:
            logger.warning("[%d/%d] %s: no bars returned", i + 1, len(codes), code)

        time.sleep(0.5)

    logger.info("=== Data fetch complete ===")


if __name__ == "__main__":
    main()
