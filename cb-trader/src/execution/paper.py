from __future__ import annotations

from datetime import datetime
from typing import Any

from src.backtest.cost_model import CostModel
from src.execution.base import Executor
from src.logger import get_logger
from src.models import Side, Trade

logger = get_logger(__name__)


class PaperExecutor(Executor):
    def __init__(self, config: dict[str, Any]) -> None:
        self.cost_model = CostModel(config)
        self.trades: list[Trade] = []

    def execute(
        self,
        code: str,
        side: Side,
        shares: int,
        price: float,
        timestamp: datetime | None = None,
        signal_composite: float = 0.0,
    ) -> Trade | None:
        if shares <= 0 or price <= 0:
            return None

        exec_price = self.cost_model.execution_price(price, is_buy=(side == Side.BUY))
        cost = self.cost_model.calc_total_cost(price, shares)

        trade = Trade(
            code=code,
            side=side,
            shares=shares,
            price=exec_price,
            cost=cost,
            timestamp=timestamp or datetime.now(),
            signal_composite=signal_composite,
        )
        self.trades.append(trade)
        logger.debug("Paper %s %s x%d @ %.3f, cost=%.2f", side.value, code, shares, exec_price, cost)
        return trade

    def get_trades(self) -> list[Trade]:
        return list(self.trades)

    def reset(self) -> None:
        self.trades.clear()
