"""Expected value calculator with Kelly criterion position sizing."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class TradeSetup:
    symbol: str
    entry: float
    stop_loss: float
    target: float
    win_prob: float


@dataclass(frozen=True)
class EVResult:
    symbol: str
    entry: float
    stop_loss: float
    target: float
    win_prob: float
    risk_per_unit: float
    reward_per_unit: float
    risk_reward_ratio: float
    expected_value_per_unit: float
    ev_ratio: float
    kelly_fraction: float
    kelly_half: float
    optimal_units: Optional[int]
    optimal_position_value: Optional[float]
    verdict: str


def compute_ev(setup: TradeSetup, capital: Optional[float] = None) -> EVResult:
    if setup.entry <= 0:
        raise ValueError("entry must be > 0")
    if setup.win_prob < 0 or setup.win_prob > 1:
        raise ValueError("win_prob must be in [0, 1]")

    is_long = setup.target > setup.entry
    if is_long:
        if setup.stop_loss >= setup.entry:
            raise ValueError("long trade: stop_loss must be < entry")
        if setup.target <= setup.entry:
            raise ValueError("long trade: target must be > entry")
        risk = setup.entry - setup.stop_loss
        reward = setup.target - setup.entry
    else:
        if setup.stop_loss <= setup.entry:
            raise ValueError("short trade: stop_loss must be > entry")
        if setup.target >= setup.entry:
            raise ValueError("short trade: target must be < entry")
        risk = setup.stop_loss - setup.entry
        reward = setup.entry - setup.target

    rr = reward / risk if risk > 0 else 0.0
    p = setup.win_prob
    q = 1.0 - p
    ev = p * reward - q * risk
    ev_ratio = ev / risk if risk > 0 else 0.0

    # Kelly: f* = (b*p - q) / b where b = reward/risk
    kelly = max((rr * p - q) / rr, 0.0) if rr > 0 else 0.0
    kelly_h = kelly / 2.0

    opt_units: Optional[int] = None
    opt_value: Optional[float] = None
    if capital is not None and capital > 0 and risk > 0 and kelly_h > 0:
        risk_capital = capital * kelly_h
        opt_units = int(math.floor(risk_capital / risk))
        if opt_units > 0:
            opt_value = round(opt_units * setup.entry, 2)
        else:
            opt_units = None

    if ev > 1e-9:
        verdict = "+EV trade"
    elif ev < -1e-9:
        verdict = "negative EV — skip"
    else:
        verdict = "breakeven"

    return EVResult(
        symbol=setup.symbol,
        entry=setup.entry,
        stop_loss=setup.stop_loss,
        target=setup.target,
        win_prob=round(p, 4),
        risk_per_unit=round(risk, 4),
        reward_per_unit=round(reward, 4),
        risk_reward_ratio=round(rr, 4),
        expected_value_per_unit=round(ev, 4),
        ev_ratio=round(ev_ratio, 4),
        kelly_fraction=round(kelly, 6),
        kelly_half=round(kelly_h, 6),
        optimal_units=opt_units,
        optimal_position_value=opt_value,
        verdict=verdict,
    )


def ev_from_prices(
    symbol: str,
    entry: float,
    stop_loss: float,
    target: float,
    win_prob: float,
    capital: Optional[float] = None,
) -> EVResult:
    return compute_ev(
        TradeSetup(symbol=symbol, entry=entry, stop_loss=stop_loss, target=target, win_prob=win_prob),
        capital=capital,
    )


def format_ev_report(r: EVResult, capital: Optional[float] = None) -> str:
    lines = [
        f"# Expected Value Analysis: {r.symbol}",
        "",
        "## Trade Setup",
        f"- Entry: {r.entry}",
        f"- Stop Loss: {r.stop_loss}",
        f"- Target: {r.target}",
        f"- Win Probability: {r.win_prob:.1%}",
        "",
        "## Risk / Reward",
        f"- Risk per unit: {r.risk_per_unit:.4f}",
        f"- Reward per unit: {r.reward_per_unit:.4f}",
        f"- R:R ratio: 1:{r.risk_reward_ratio:.2f}",
        "",
        "## Expected Value",
        f"- EV per unit: {r.expected_value_per_unit:+.4f}",
        f"- EV / Risk: {r.ev_ratio:+.4f}",
        f"- **Verdict: {r.verdict}**",
        "",
        "## Kelly Position Sizing",
        f"- Full Kelly: {r.kelly_fraction:.2%}",
        f"- Half-Kelly (practical): {r.kelly_half:.2%}",
    ]
    if r.optimal_units is not None:
        lines.append(f"- Suggested units: {r.optimal_units:,}")
    if r.optimal_position_value is not None:
        lines.append(f"- Position value: ${r.optimal_position_value:,.2f}")
    if capital is not None:
        lines.append(f"- Total capital: ${capital:,.2f}")
        if r.optimal_position_value:
            pct = r.optimal_position_value / capital
            lines.append(f"- Position %: {pct:.1%}")

    lines.extend([
        "",
        "## Risk Warning",
        "- Win probability is an estimate; actual outcomes may differ.",
        "- Kelly assumes infinite repeated plays; use half-Kelly or less for single trades.",
        "- Not investment advice.",
        "",
    ])
    return "\n".join(lines)
