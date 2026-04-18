from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from src.hl_client.types import OrderRequest


@dataclass
class Signal:
    coin: str
    action: str  # "open", "close", "rebalance", "hold"
    orders: list[OrderRequest]
    reason: str = ""
    score: float = 0.0


class Strategy(ABC):
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def evaluate(self) -> list[Signal]: ...

    @abstractmethod
    def on_fill(self, coin: str, side: str, size: float, price: float) -> None: ...
