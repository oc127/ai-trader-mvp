from __future__ import annotations

import os

from src.config import load_config
from src.logger import get_logger, setup_logging

logger = get_logger(__name__)


def main() -> None:
    setup_logging()
    environment = os.getenv("ENVIRONMENT", "backtest")
    config = load_config(environment=environment)
    logger.info("CB Trader started, environment=%s", environment)
    logger.info("Config loaded: %d sections", len(config))


if __name__ == "__main__":
    main()
