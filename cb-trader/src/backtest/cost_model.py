from __future__ import annotations

from typing import Any


class CostModel:
    def __init__(self, config: dict[str, Any]) -> None:
        cc = config.get("cost", {})
        self.commission_rate: float = cc.get("commission_rate", 0.0001)
        self.slippage_bps: float = cc.get("slippage_bps", 2)
        self.min_commission: float = cc.get("min_commission", 0.1)

    def calc_commission(self, amount: float) -> float:
        return max(amount * self.commission_rate, self.min_commission)

    def calc_slippage(self, price: float) -> float:
        return price * self.slippage_bps / 10000

    def calc_total_cost(self, price: float, shares: int) -> float:
        amount = price * shares
        commission = self.calc_commission(amount)
        slippage = self.calc_slippage(price) * shares
        return commission + slippage

    def execution_price(self, price: float, is_buy: bool) -> float:
        slip = self.calc_slippage(price)
        return price + slip if is_buy else price - slip
