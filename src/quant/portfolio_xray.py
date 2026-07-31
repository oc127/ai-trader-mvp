"""Portfolio X-ray: correlation matrix, PCA, concentration risk — zero external deps."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class AssetStats:
    symbol: str
    ann_return: float
    ann_volatility: float
    sharpe: float
    max_drawdown: float


@dataclass(frozen=True)
class CorrelationPair:
    a: str
    b: str
    correlation: float


@dataclass(frozen=True)
class PCAComponent:
    index: int
    explained_ratio: float
    weights: dict[str, float]


@dataclass(frozen=True)
class PortfolioXray:
    symbols: list[str]
    asset_stats: list[AssetStats]
    correlations: list[CorrelationPair]
    avg_correlation: float
    max_correlation: Optional[CorrelationPair]
    concentration_risk: str
    pca_components: list[PCAComponent]
    pca_effective_bets: float
    verdict: str


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _std(xs: list[float], ddof: int = 1) -> float:
    if len(xs) <= ddof:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - ddof))


def _corr(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mx, my = _mean(xs), _mean(ys)
    cov = sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / (n - 1)
    sx, sy = _std(xs), _std(ys)
    return cov / (sx * sy) if sx > 0 and sy > 0 else 0.0


def _max_drawdown(returns: list[float]) -> float:
    peak = cum = 1.0
    mdd = 0.0
    for r in returns:
        cum *= (1 + r)
        peak = max(peak, cum)
        mdd = max(mdd, (peak - cum) / peak)
    return mdd


def _align_returns(
    returns: dict[str, list[tuple[str, float]]],
) -> tuple[list[str], list[str], list[list[float]]]:
    all_dates: set[str] = set()
    for rets in returns.values():
        for d, _ in rets:
            all_dates.add(d)
    dates = sorted(all_dates)
    date_idx = {d: i for i, d in enumerate(dates)}

    symbols = sorted(returns.keys())
    matrix: list[list[Optional[float]]] = [[None] * len(dates) for _ in symbols]
    for si, sym in enumerate(symbols):
        for d, r in returns[sym]:
            matrix[si][date_idx[d]] = r

    valid = [j for j in range(len(dates)) if all(matrix[si][j] is not None for si in range(len(symbols)))]
    clean_dates = [dates[j] for j in valid]
    clean_matrix: list[list[float]] = [[matrix[si][j] for j in valid] for si in range(len(symbols))]  # type: ignore
    return symbols, clean_dates, clean_matrix


def _power_iteration_pca(
    cov_matrix: list[list[float]], n_components: int, max_iter: int = 200,
) -> list[tuple[float, list[float]]]:
    n = len(cov_matrix)
    if n == 0:
        return []
    mat = [row[:] for row in cov_matrix]
    results: list[tuple[float, list[float]]] = []

    for _ in range(min(n_components, n)):
        v = [1.0 / math.sqrt(n)] * n
        eigenval = 0.0
        for _ in range(max_iter):
            new_v = [sum(mat[i][j] * v[j] for j in range(n)) for i in range(n)]
            norm = math.sqrt(sum(x * x for x in new_v))
            if norm < 1e-12:
                break
            new_v = [x / norm for x in new_v]
            eigenval = sum(new_v[i] * sum(mat[i][j] * new_v[j] for j in range(n)) for i in range(n))
            v = new_v
        results.append((eigenval, v))
        for i in range(n):
            for j in range(n):
                mat[i][j] -= eigenval * v[i] * v[j]

    return results


def xray(
    returns: dict[str, list[tuple[str, float]]],
    risk_free_rate: float = 0.02,
) -> PortfolioXray:
    """Run portfolio X-ray.

    returns: {symbol: [(date, daily_return), ...]} — each list sorted by date.
    """
    symbols = sorted(returns.keys())
    if len(symbols) < 2:
        return PortfolioXray(
            symbols=symbols, asset_stats=[], correlations=[], avg_correlation=0,
            max_correlation=None, concentration_risk="N/A",
            pca_components=[], pca_effective_bets=0,
            verdict="Need >= 2 assets for portfolio analysis",
        )

    asset_stats: list[AssetStats] = []
    for sym in symbols:
        rets = [r for _, r in returns[sym]]
        if not rets:
            continue
        ann_r = _mean(rets) * 252
        ann_v = _std(rets) * math.sqrt(252)
        sharpe = (ann_r - risk_free_rate) / ann_v if ann_v > 0 else 0.0
        mdd = _max_drawdown(rets)
        asset_stats.append(AssetStats(sym, round(ann_r, 4), round(ann_v, 4), round(sharpe, 4), round(mdd, 4)))

    syms, dates, matrix = _align_returns(returns)
    if len(dates) < 10:
        return PortfolioXray(
            symbols=symbols, asset_stats=asset_stats, correlations=[], avg_correlation=0,
            max_correlation=None, concentration_risk="N/A",
            pca_components=[], pca_effective_bets=0,
            verdict=f"Insufficient overlapping dates ({len(dates)}), need >= 10",
        )

    n_sym = len(syms)
    corr_pairs: list[CorrelationPair] = []
    for i in range(n_sym):
        for j in range(i + 1, n_sym):
            c = _corr(matrix[i], matrix[j])
            corr_pairs.append(CorrelationPair(syms[i], syms[j], round(c, 4)))

    abs_corrs = [abs(cp.correlation) for cp in corr_pairs]
    avg_corr = _mean(abs_corrs)
    max_cp = max(corr_pairs, key=lambda x: abs(x.correlation)) if corr_pairs else None

    concentration = "high" if avg_corr > 0.7 else "moderate" if avg_corr > 0.4 else "low"

    # covariance for PCA
    means = [_mean(matrix[i]) for i in range(n_sym)]
    nd = len(dates)
    cov = [[0.0] * n_sym for _ in range(n_sym)]
    for i in range(n_sym):
        for j in range(n_sym):
            cov[i][j] = sum((matrix[i][k] - means[i]) * (matrix[j][k] - means[j]) for k in range(nd)) / (nd - 1)

    n_comp = min(3, n_sym)
    eigen = _power_iteration_pca(cov, n_comp)
    total_var = sum(cov[i][i] for i in range(n_sym))

    pca_comps: list[PCAComponent] = []
    for idx, (val, vec) in enumerate(eigen):
        ratio = val / total_var if total_var > 0 else 0.0
        weights = {syms[i]: round(vec[i], 4) for i in range(n_sym)}
        pca_comps.append(PCAComponent(idx + 1, round(ratio, 4), weights))

    eigenvalues = [v for v, _ in eigen]
    total_eig = sum(eigenvalues) if eigenvalues else 1.0
    if total_eig > 0 and all(e > 0 for e in eigenvalues):
        probs = [e / total_eig for e in eigenvalues]
        entropy = -sum(p * math.log(p) for p in probs if p > 0)
        enb = math.exp(entropy)
    else:
        enb = 1.0

    if enb < 1.5:
        verdict = f"~{enb:.1f} effective independent bets — highly concentrated, diversify"
    elif enb < 2.5:
        verdict = f"~{enb:.1f} effective independent bets — moderate diversification"
    else:
        verdict = f"~{enb:.1f} effective independent bets — well diversified"

    return PortfolioXray(
        symbols=syms, asset_stats=asset_stats, correlations=corr_pairs,
        avg_correlation=round(avg_corr, 4), max_correlation=max_cp,
        concentration_risk=concentration, pca_components=pca_comps,
        pca_effective_bets=round(enb, 2), verdict=verdict,
    )


def format_xray_report(px: PortfolioXray) -> str:
    lines = [
        "# Portfolio X-Ray",
        "",
        f"Assets: {', '.join(px.symbols)}",
        "",
    ]
    if px.asset_stats:
        lines.extend([
            "## Per-Asset Stats (annualized)",
            "",
            "| Asset | Ann Return | Ann Vol | Sharpe | Max DD |",
            "|---|---:|---:|---:|---:|",
        ])
        for a in px.asset_stats:
            lines.append(f"| {a.symbol} | {a.ann_return:+.1%} | {a.ann_volatility:.1%} | {a.sharpe:.2f} | {a.max_drawdown:.1%} |")
        lines.append("")

    if px.correlations:
        lines.extend([
            "## Correlation Matrix",
            f"Avg |corr|: **{px.avg_correlation:.2f}** — Concentration: **{px.concentration_risk}**",
            "",
        ])
        if px.max_correlation:
            lines.append(f"Highest: {px.max_correlation.a} x {px.max_correlation.b} = {px.max_correlation.correlation:+.2f}")
        lines.append("")

    if px.pca_components:
        lines.extend(["## PCA Components", ""])
        for pc in px.pca_components:
            lines.append(f"- PC{pc.index}: explains {pc.explained_ratio:.1%} variance")
            top3 = sorted(pc.weights.items(), key=lambda x: abs(x[1]), reverse=True)[:3]
            lines.append(f"  Top: {', '.join(f'{s}({w:+.2f})' for s, w in top3)}")
        lines.append(f"\nEffective independent bets (ENB): **{px.pca_effective_bets:.1f}**")
        lines.append("")

    lines.extend([f"## Verdict: {px.verdict}", ""])
    return "\n".join(lines)
