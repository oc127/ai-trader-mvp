"""Trading strategy framework."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .broker import AccountInfo, Position
from .storage import DailyBar


@dataclass(frozen=True)
class TradeSignal:
    symbol: str
    action: str  # "buy" | "sell" | "hold"
    strength: float  # 0.0 to 1.0
    reason: str


class Strategy:
    """Abstract strategy interface."""

    name: str

    def generate_signals(
        self,
        bars_by_symbol: dict[str, list[DailyBar]],
        account: AccountInfo,
        positions: list[Position],
    ) -> list[TradeSignal]:
        raise NotImplementedError


class MomentumStrategy(Strategy):
    """
    Momentum strategy: buy top N stocks by momentum, sell those that drop out.

    - Ranks stocks by (close / close[-lookback]) - 1
    - Buys top_n stocks not already held
    - Sells held stocks that fall out of top_n
    """

    name = "momentum"

    def __init__(self, lookback: int = 20, top_n: int = 5):
        self.lookback = lookback
        self.top_n = top_n

    def generate_signals(
        self,
        bars_by_symbol: dict[str, list[DailyBar]],
        account: AccountInfo,
        positions: list[Position],
    ) -> list[TradeSignal]:
        # Compute momentum for each symbol
        momentum: list[tuple[str, float]] = []
        for symbol, bars in bars_by_symbol.items():
            if len(bars) <= self.lookback:
                continue
            bars_sorted = sorted(bars, key=lambda b: b.trade_date)
            last_close = bars_sorted[-1].close
            lb_close = bars_sorted[-(self.lookback + 1)].close
            if lb_close == 0:
                continue
            mom = (last_close / lb_close) - 1.0
            momentum.append((symbol, mom))

        momentum.sort(key=lambda x: x[1], reverse=True)
        top_symbols = {s for s, _ in momentum[: self.top_n]}
        held_symbols = {p.symbol for p in positions}

        signals: list[TradeSignal] = []

        # Sell signals: held stocks not in top_n
        for pos in positions:
            if pos.symbol not in top_symbols and pos.qty > 0:
                signals.append(
                    TradeSignal(
                        symbol=pos.symbol,
                        action="sell",
                        strength=1.0,
                        reason=f"Dropped out of top {self.top_n} momentum",
                    )
                )

        # Buy signals: top_n stocks not held
        for rank, (symbol, mom) in enumerate(momentum[: self.top_n]):
            if symbol not in held_symbols:
                strength = 1.0 - (rank / (2 * self.top_n))
                signals.append(
                    TradeSignal(
                        symbol=symbol,
                        action="buy",
                        strength=strength,
                        reason=f"Top {self.top_n} momentum: {mom:+.2%}",
                    )
                )

        return signals


class MeanReversionStrategy(Strategy):
    """
    Mean reversion strategy using RSI.

    - Buy when RSI < oversold_threshold (default 30)
    - Sell when RSI > overbought_threshold (default 70)
    """

    name = "mean_reversion"

    def __init__(
        self,
        rsi_period: int = 14,
        oversold: float = 30.0,
        overbought: float = 70.0,
    ):
        self.rsi_period = rsi_period
        self.oversold = oversold
        self.overbought = overbought

    @staticmethod
    def _compute_rsi(closes: list[float], period: int = 14) -> Optional[float]:
        """Simple RSI: avg_gain / avg_loss over `period` bars."""
        if len(closes) < period + 1:
            return None
        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        recent = deltas[-period:]
        gains = [d for d in recent if d > 0]
        losses = [-d for d in recent if d < 0]
        avg_gain = sum(gains) / period if gains else 0.0
        avg_loss = sum(losses) / period if losses else 0.0
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def generate_signals(
        self,
        bars_by_symbol: dict[str, list[DailyBar]],
        account: AccountInfo,
        positions: list[Position],
    ) -> list[TradeSignal]:
        held_symbols = {p.symbol for p in positions}
        signals: list[TradeSignal] = []

        for symbol, bars in bars_by_symbol.items():
            bars_sorted = sorted(bars, key=lambda b: b.trade_date)
            closes = [b.close for b in bars_sorted]
            rsi = self._compute_rsi(closes, self.rsi_period)
            if rsi is None:
                continue

            if rsi < self.oversold and symbol not in held_symbols:
                strength = (self.oversold - rsi) / self.oversold
                signals.append(
                    TradeSignal(
                        symbol=symbol,
                        action="buy",
                        strength=min(strength, 1.0),
                        reason=f"RSI oversold: {rsi:.1f}",
                    )
                )
            elif rsi > self.overbought and symbol in held_symbols:
                strength = (rsi - self.overbought) / (100 - self.overbought)
                signals.append(
                    TradeSignal(
                        symbol=symbol,
                        action="sell",
                        strength=min(strength, 1.0),
                        reason=f"RSI overbought: {rsi:.1f}",
                    )
                )

        return signals
