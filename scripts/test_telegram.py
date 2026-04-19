"""Send a test Telegram message."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config
from src.logger import setup_logging
from src.monitor.alerts import AlertManager


def main() -> None:
    cfg = load_config("testnet")
    setup_logging(cfg)

    alerts = AlertManager(cfg)
    alerts.send("System online — paper trading on testnet ✅")
    print("Telegram message sent.")


if __name__ == "__main__":
    main()
