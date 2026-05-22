"""Persistent memory — trade journal and learned patterns."""

from __future__ import annotations

import json
import time
from pathlib import Path

from src.logger import get_logger

log = get_logger(__name__)


class Memory:
    def __init__(self, path: str = "data/agent_memory.json") -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._data = self._load()

    def add_trade(self, trade: dict) -> None:
        trade["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self._data.setdefault("trades", []).append(trade)
        if len(self._data["trades"]) > 200:
            self._data["trades"] = self._data["trades"][-200:]
        self._save()

    def add_lesson(self, lesson: str) -> None:
        entry = {
            "lesson": lesson,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self._data.setdefault("lessons", []).append(entry)
        if len(self._data["lessons"]) > 50:
            self._data["lessons"] = self._data["lessons"][-50:]
        self._save()

    def get_context(self, max_trades: int = 10) -> str:
        parts = []
        trades = self._data.get("trades", [])[-max_trades:]
        if trades:
            parts.append("最近交易记录：")
            for t in trades:
                parts.append(f"  {t.get('timestamp', '?')} | {json.dumps(t, ensure_ascii=False)}")

        lessons = self._data.get("lessons", [])[-5:]
        if lessons:
            parts.append("\n经验总结：")
            for l in lessons:
                parts.append(f"  - {l['lesson']}")

        return "\n".join(parts) if parts else "暂无交易记录。"

    def get_stats(self) -> dict:
        trades = self._data.get("trades", [])
        return {
            "total_trades": len(trades),
            "lessons_learned": len(self._data.get("lessons", [])),
        }

    def _load(self) -> dict:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text())
            except Exception:
                pass
        return {}

    def _save(self) -> None:
        try:
            self._path.write_text(json.dumps(self._data, ensure_ascii=False, indent=2))
        except Exception:
            log.warning("Failed to save memory")
