from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from src.backtest.metrics import BacktestMetrics, compute_metrics
from src.hl_client.types import FundingRate
from src.logger import get_logger

log = get_logger(__name__)

TAKER_FEE = 0.00035


@dataclass
class BacktestConfig:
    initial_capital: float = 10000.0
    entry_rate_threshold: float = 0.0001
    exit_rate_threshold: float = 0.00003
    max_pairs: int = 5
    per_pair_pct: float = 0.20
    margin_buffer_pct: float = 0.20


@dataclass
class SimPosition:
    coin: str
    size: float
    entry_price: float
    opened_at: datetime
    funding_earned: float = 0.0


@dataclass
class BacktestResult:
    metrics: BacktestMetrics
    equity_curve: list[float] = field(default_factory=list)
    trades: list[dict] = field(default_factory=list)


def run_backtest(
    funding_data: dict[str, list[FundingRate]],
    price_data: dict[str, dict[str, float]],
    config: BacktestConfig | None = None,
) -> BacktestResult:
    cfg = config or BacktestConfig()
    balance = cfg.initial_capital
    positions: dict[str, SimPosition] = {}
    equity_curve: list[float] = [balance]
    trades: list[dict] = []
    holding_hours: list[float] = []
    total_funding = 0.0

    all_timestamps = set()
    for rates in funding_data.values():
        for r in rates:
            all_timestamps.add(r.timestamp)
    timestamps = sorted(all_timestamps)

    rate_map: dict[str, dict[str, float]] = {}
    for coin, rates in funding_data.items():
        rate_map[coin] = {}
        for r in rates:
            rate_map[coin][r.timestamp.isoformat()] = r.rate

    for ts in timestamps:
        ts_key = ts.isoformat()

        for coin in list(positions.keys()):
            rate = rate_map.get(coin, {}).get(ts_key, 0)
            if rate != 0:
                pos = positions[coin]
                price = price_data.get(coin, {}).get(ts_key, pos.entry_price)
                funding = abs(pos.size) * price * rate
                total_funding += funding
                pos.funding_earned += funding
                balance += funding

            rate_val = rate_map.get(coin, {}).get(ts_key, 0)
            if rate_val < cfg.exit_rate_threshold and coin in positions:
                pos = positions.pop(coin)
                price = price_data.get(coin, {}).get(ts_key, pos.entry_price)
                fee = abs(pos.size) * price * TAKER_FEE * 2
                balance -= fee
                hours = (ts - pos.opened_at).total_seconds() / 3600
                holding_hours.append(hours)
                trades.append(
                    {
                        "coin": coin,
                        "action": "close",
                        "size": pos.size,
                        "entry": pos.entry_price,
                        "exit": price,
                        "funding": pos.funding_earned,
                        "fee": fee,
                        "hours": hours,
                        "ts": ts_key,
                    }
                )

        if len(positions) < cfg.max_pairs:
            available = balance * (1 - cfg.margin_buffer_pct)
            per_pair = cfg.initial_capital * cfg.per_pair_pct

            for coin in funding_data:
                if coin in positions or len(positions) >= cfg.max_pairs:
                    continue
                rate = rate_map.get(coin, {}).get(ts_key, 0)
                if rate >= cfg.entry_rate_threshold:
                    price = price_data.get(coin, {}).get(ts_key, 0)
                    if price <= 0:
                        continue
                    alloc = min(per_pair, available / 2)
                    if alloc < 50:
                        continue
                    size = alloc / price
                    fee = size * price * TAKER_FEE * 2
                    balance -= fee
                    positions[coin] = SimPosition(
                        coin=coin,
                        size=size,
                        entry_price=price,
                        opened_at=ts,
                    )
                    trades.append(
                        {
                            "coin": coin,
                            "action": "open",
                            "size": size,
                            "entry": price,
                            "fee": fee,
                            "ts": ts_key,
                        }
                    )

        equity = balance
        for pos in positions.values():
            price = price_data.get(pos.coin, {}).get(ts_key, pos.entry_price)
            equity += pos.size * (price - pos.entry_price)
            equity -= pos.size * (price - pos.entry_price)
        equity_curve.append(equity)

    metrics = compute_metrics(equity_curve, total_funding, len(trades), holding_hours)
    return BacktestResult(metrics=metrics, equity_curve=equity_curve, trades=trades)
