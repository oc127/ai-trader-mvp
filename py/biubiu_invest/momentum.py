from __future__ import annotations

from dataclasses import dataclass

from .storage import DailyBar


@dataclass(frozen=True)
class MomentumSignal:
    ts_code: str
    asof_date: str
    lookback: int
    momentum_return: float  # close/close[-lookback]-1


def compute_momentum(
    bars: list[DailyBar],
    lookback: int = 20,
) -> list[MomentumSignal]:
    """
    Very simple momentum for MVP:
      momentum_return = last_close / close_lookback - 1

    bars must include multiple symbols and be sorted by ts_code/date (storage loader does this).
    """
    if lookback < 2:
        raise ValueError("lookback must be >= 2")

    if not bars:
        return []

    # bars are expected sorted by ts_code/date (storage loader does this),
    # but we'll still handle unsorted input defensively.
    by_code: dict[str, list[DailyBar]] = {}
    for b in bars:
        by_code.setdefault(b.ts_code, []).append(b)
    for ts_code in list(by_code.keys()):
        by_code[ts_code].sort(key=lambda x: x.trade_date)

    out: list[MomentumSignal] = []
    # We need (lookback + 1) closes to compute last_close / close[-lookback] - 1
    for ts_code, series in by_code.items():
        if len(series) <= lookback:
            continue
        last = series[-1]
        lb = series[-(lookback + 1)]
        if lb.close == 0:
            continue
        mom = (last.close / lb.close) - 1.0
        out.append(
            MomentumSignal(
                ts_code=ts_code,
                asof_date=last.trade_date,
                lookback=lookback,
                momentum_return=float(mom),
            )
        )

    out.sort(key=lambda x: x.momentum_return, reverse=True)
    return out


