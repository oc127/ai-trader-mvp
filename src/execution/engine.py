from __future__ import annotations

from abc import ABC, abstractmethod

from src.hl_client.types import OrderResult
from src.logger import get_logger
from src.strategy.base import Signal

log = get_logger(__name__)


class Executor(ABC):
    @abstractmethod
    def execute_signal(self, signal: Signal) -> list[OrderResult]: ...

    @abstractmethod
    def flatten_all(self) -> list[OrderResult]: ...
