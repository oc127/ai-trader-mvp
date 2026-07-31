from __future__ import annotations

import os

from src.logger import get_logger

logger = get_logger(__name__)


class AlertManager:
    def __init__(self, config: dict | None = None) -> None:
        mon_cfg = (config or {}).get("monitor", {})
        self._telegram_enabled = mon_cfg.get("telegram_enabled", False)
        self._bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self._chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

        if self._telegram_enabled and not (self._bot_token and self._chat_id):
            logger.warning("Telegram enabled but missing BOT_TOKEN or CHAT_ID")
            self._telegram_enabled = False

    def send(self, message: str, level: str = "info") -> None:
        log_level = {"info": 20, "warning": 30, "error": 40, "critical": 50}.get(level, 20)
        logger.log(log_level, "Alert [%s]: %s", level.upper(), message)

        if self._telegram_enabled:
            self._send_telegram(message)

    def _send_telegram(self, message: str) -> None:
        try:
            import httpx

            url = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"
            httpx.post(url, json={"chat_id": self._chat_id, "text": message, "parse_mode": "Markdown"}, timeout=10)
        except Exception:
            logger.exception("Failed to send Telegram alert")
