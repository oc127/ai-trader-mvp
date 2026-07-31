"""Multi-asset position optimizer with Kelly, single-position caps, and correlation constraints."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from src.quant.expected_value import EVResult
from src.quant.portfolio_xray import _align_returns, _corr, _mean, _std


@dataclass(frozen=True)
class PositionConstraints:
    total_capital: float
    max_single_position_pct: float = 0.25
    max_correlated_cluster_pct: float = 0.40
    min_positions: int = 3
    max_positions: int = 10
    correlation_threshold: float = 0.6
    lot_size: int = 1  # set to 100 for A-shares


@dataclass(frozen=True)
class PositionAllocation:
    symbol: str
    weight: float
    amount: float
    units: int
    kelly_raw: float
    capped_reason: Optional[str]


@dataclass(frozen=True)
class OptimizedPortfolio:
    allocations: list[PositionAllocation]
    total_allocated: float
    cash_reserve: float
    cash_reserve_pct: float
    portfolio_ev: float
    effective_positions: float
    warnings: list[str]
    verdict: str


def _hhi_effective_n(weights: list[float]) -> float:
    hhi = sum(w * w for w in weights)
    return 1.0 / hhi if hhi > 0 else 0.0


def _find_correlated_clusters(
    symbols: list[str],
    returns: dict[str, list[tuple[str, float]]],
    threshold: float,
) -> list[list[str]]:
    syms, dates, matrix = _align_returns(returns)
    if len(dates) < 10:
        return [[s] for s in symbols]

    sym_idx = {s: i for i, s in enumerate(syms)}
    parent = list(range(len(symbols)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py

    for i in range(len(symbols)):
        for j in range(i + 1, len(symbols)):
            si, sj = sym_idx.get(symbols[i]), sym_idx.get(symbols[j])
            if si is None or sj is None:
                continue
            c = _corr(matrix[si], matrix[sj])
            if abs(c) > threshold:
                union(i, j)

    clusters: dict[int, list[str]] = {}
    for i, s in enumerate(symbols):
        clusters.setdefault(find(i), []).append(s)
    return list(clusters.values())


def optimize_positions(
    ev_results: list[EVResult],
    constraints: PositionConstraints,
    returns: Optional[dict[str, list[tuple[str, float]]]] = None,
) -> OptimizedPortfolio:
    warnings: list[str] = []
    positive_ev = [r for r in ev_results if r.kelly_half > 0 and r.expected_value_per_unit > 0]

    if not positive_ev:
        return OptimizedPortfolio(
            allocations=[], total_allocated=0,
            cash_reserve=constraints.total_capital, cash_reserve_pct=1.0,
            portfolio_ev=0, effective_positions=0, warnings=["No +EV trades"],
            verdict="All cash — no positive expected value opportunities",
        )

    raw_weights = {r.symbol: r.kelly_half for r in positive_ev}
    total_raw = sum(raw_weights.values())
    weights = {s: w / total_raw for s, w in raw_weights.items()} if total_raw > 0 else {s: 1.0 / len(raw_weights) for s in raw_weights}

    capped: dict[str, Optional[str]] = {s: None for s in weights}
    max_pct = constraints.max_single_position_pct
    for s in weights:
        if weights[s] > max_pct:
            capped[s] = f"Single position cap {max_pct:.0%}"
            weights[s] = max_pct

    if returns:
        clusters = _find_correlated_clusters(list(weights.keys()), returns, constraints.correlation_threshold)
        max_cluster = constraints.max_correlated_cluster_pct
        for cluster in clusters:
            if len(cluster) <= 1:
                continue
            cluster_weight = sum(weights.get(s, 0) for s in cluster)
            if cluster_weight > max_cluster:
                scale = max_cluster / cluster_weight
                for s in cluster:
                    if s in weights:
                        weights[s] *= scale
                        capped[s] = f"Correlated cluster ({', '.join(cluster)})"
                warnings.append(f"Correlated cluster [{', '.join(cluster)}] capped at {max_cluster:.0%}")

    total_w = sum(weights.values())
    max_total = 0.90
    if total_w > max_total:
        scale = max_total / total_w
        weights = {s: w * scale for s, w in weights.items()}

    ev_by_sym = {r.symbol: r for r in positive_ev}
    allocations: list[PositionAllocation] = []
    for sym, w in sorted(weights.items(), key=lambda x: -x[1]):
        ev_r = ev_by_sym[sym]
        amount = round(constraints.total_capital * w, 2)
        units = int(math.floor(amount / ev_r.entry)) if ev_r.entry > 0 else 0
        if constraints.lot_size > 1:
            units = (units // constraints.lot_size) * constraints.lot_size
        actual_amount = round(units * ev_r.entry, 2) if units > 0 else 0.0
        allocations.append(PositionAllocation(
            symbol=sym, weight=round(w, 4), amount=actual_amount,
            units=units, kelly_raw=ev_r.kelly_half, capped_reason=capped.get(sym),
        ))

    total_alloc = sum(a.amount for a in allocations)
    cash = round(constraints.total_capital - total_alloc, 2)
    cash_pct = cash / constraints.total_capital if constraints.total_capital > 0 else 1.0

    port_ev = sum(a.weight * ev_by_sym[a.symbol].ev_ratio for a in allocations if a.symbol in ev_by_sym)
    eff_w = [a.amount / total_alloc for a in allocations if a.amount > 0] if total_alloc > 0 else []
    eff_n = _hhi_effective_n(eff_w) if eff_w else 0

    if eff_n < 2:
        verdict = "Portfolio too concentrated — add uncorrelated assets"
    elif cash_pct > 0.5:
        verdict = "Heavy cash — few opportunities, stay patient"
    elif port_ev > 0.1:
        verdict = "Portfolio EV looks good — execute with discipline"
    else:
        verdict = "Portfolio EV marginal — keep position sizes small"

    if len(allocations) < constraints.min_positions and len(positive_ev) >= constraints.min_positions:
        warnings.append(f"Position count ({len(allocations)}) below minimum ({constraints.min_positions})")

    return OptimizedPortfolio(
        allocations=allocations, total_allocated=round(total_alloc, 2),
        cash_reserve=cash, cash_reserve_pct=round(cash_pct, 4),
        portfolio_ev=round(port_ev, 4), effective_positions=round(eff_n, 2),
        warnings=warnings, verdict=verdict,
    )


def format_portfolio_report(p: OptimizedPortfolio, capital: float) -> str:
    lines = [
        "# Position Optimization",
        "",
        f"Total capital: ${capital:,.0f}",
        "",
    ]
    if p.allocations:
        lines.extend([
            "## Allocations",
            "",
            "| Asset | Weight | Amount | Units | Kelly | Adjustment |",
            "|---|---:|---:|---:|---:|---|",
        ])
        for a in p.allocations:
            lines.append(f"| {a.symbol} | {a.weight:.1%} | ${a.amount:,.0f} | {a.units:,} | {a.kelly_raw:.1%} | {a.capped_reason or '—'} |")
        lines.append("")

    lines.extend([
        "## Summary",
        f"- Allocated: ${p.total_allocated:,.0f}",
        f"- Cash reserve: ${p.cash_reserve:,.0f} ({p.cash_reserve_pct:.1%})",
        f"- Effective positions: {p.effective_positions:.1f}",
        f"- Portfolio EV/risk: {p.portfolio_ev:+.4f}",
        "",
    ])
    if p.warnings:
        lines.append("## Warnings")
        for w in p.warnings:
            lines.append(f"- {w}")
        lines.append("")

    lines.extend([f"## Verdict: {p.verdict}", ""])
    return "\n".join(lines)
