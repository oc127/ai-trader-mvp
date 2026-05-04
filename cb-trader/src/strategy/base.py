from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from src.models import Signal


class Strategy(ABC):
    @abstractmethod
    def generate_signal(self, code: str, market_data: dict[str, Any]) -> Signal: ...

    @abstractmethod
    def select_universe(self, market_data: dict[str, Any]) -> list[str]: ...
