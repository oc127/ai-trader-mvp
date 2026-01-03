from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .momentum import MomentumSignal
from .policy import Policy


@dataclass(frozen=True)
class ReportConfig:
    title: str = "A股趋势动量日报（MVP）"
    top_n: int = 20


def render_markdown(
    asof_date: str,
    signals: list[MomentumSignal],
    cfg: ReportConfig = ReportConfig(),
    policy: Optional[Policy] = None,
) -> str:
    lines: list[str] = []
    lines.append(f"# {cfg.title}")
    lines.append("")
    lines.append(f"- 日期：**{asof_date}**")
    lines.append(f"- 信号：**动量排名**（lookback = {signals[0].lookback if signals else 'N/A'}）")
    if policy:
        lines.append(f"- 政策（Policy）：`{policy.id}`（mode={policy.mode}，live_trading={policy.allow_live_trading}）")
    lines.append("")

    if not signals:
        lines.append("> 无可用信号（数据不足）。")
        lines.append("")
        return "\n".join(lines)

    n = min(cfg.top_n, len(signals))
    lines.append(f"## Top {n}")
    lines.append("")
    lines.append("| 排名 | 股票 | 动量收益 |")
    lines.append("|---:|---|---:|")
    for i, s in enumerate(signals[:n], start=1):
        lines.append(f"| {i} | {s.ts_code} | {s.momentum_return:.2%} |")
    lines.append("")

    lines.append("## 说明（风险提示）")
    lines.append("")
    lines.append("- 本报告为研究/学习用途，不构成投资建议。")
    lines.append("- 动量策略对回撤、换手、涨跌停不可成交、幸存者偏差等非常敏感；后续需要纳入回测与交易规则。")
    lines.append("")
    return "\n".join(lines)


