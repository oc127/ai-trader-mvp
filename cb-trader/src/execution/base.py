from __future__ import annotations

from abc import ABC, abstractmethod

from src.models import Side, Trade


class Executor(ABC):
    @abstractmethod
    def execute(self, code: str, side: Side, shares: int, price: float) -> Trade | None: ...
