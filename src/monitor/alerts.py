from __future__ import annotations

import os

from src.logger import get_logger

log = get_logger(__name__)


class AlertManager:
    def __init__(self, cfg: dict) -> None:
        mon_cfg = cfg.get("monitor", {})
        self._telegram_enabled = mon_cfg.get("telegram_enabled", False)
        self._bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self._chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

        if self._telegram_enabled and not (self._bot_token and self._chat_id):
            log.warning("Telegram enabled but missing BOT_TOKEN or CHAT_ID")
            self._telegram_enabled = False

    def send(self, message: str, level: str = "info") -> None:
        prefixed = f"HL | {message}"
        log.log(
            {"info": 20, "warning": 30, "error": 40, "critical": 50}.get(level, 20),
            f"Alert: {prefixed}",
        )

        if self._telegram_enabled:
            self._send_telegram(prefixed)

    def _send_telegram(self, message: str) -> None:
        try:
            import httpx

            url = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"
            httpx.post(url, json={"chat_id": self._chat_id, "text": message, "parse_mode": "Markdown"})
        except Exception:
            log.exception("Failed to send Telegram alert")
