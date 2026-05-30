"""Unified trading system — Serenity × RICH pipeline.

Serenity answers WHAT to buy (chokepoint fundamentals).
RICH answers WHEN and HOW to enter (Fib timing + options execution).
This module chains them into a single decision pipeline:

    Serenity (选什么) → RICH (怎么进) → Decision (做不做/怎么做)

Each unified skill orchestrates multiple sub-skill calls,
then synthesizes results through a final Claude call.
"""

from __future__ import annotations

import anthropic

from src.agent.skills.rich import RichSkills
from src.agent.skills.serenity import SerenitySkills
from src.logger import get_logger

log = get_logger(__name__)

UNIFIED_SKILL_DEFINITIONS = [
    {
        "name": "full_analysis",
        "description": "Serenity × RICH 完整分析管线：基本面选股 → 技术面择时 → 综合决策。一键跑完两套系统。",
        "input_schema": {
            "type": "object",
            "properties": {
                "company": {
                    "type": "string",
                    "description": "公司名称或股票代码，如 'SIVE', 'BESI.AS', '300748.SZ'",
                },
                "thesis": {
                    "type": "string",
                    "description": "投资论点，如 '西方CPO供应链里最被低估的激光chokepoint'",
                },
                "direction": {
                    "type": "string",
                    "enum": ["long", "short"],
                    "description": "交易方向",
                },
                "capital": {
                    "type": "number",
                    "description": "可用资金（美元），用于仓位计算",
                },
            },
            "required": ["company", "thesis", "direction"],
        },
    },
    {
        "name": "entry_plan",
        "description": "RICH 完整入场方案：Drill Down → Fib → R:R → 期权策略，输出可执行交易计划。",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "股票代码",
                },
                "direction": {
                    "type": "string",
                    "enum": ["long", "short"],
                    "description": "交易方向",
                },
                "capital": {
                    "type": "number",
                    "description": "可用资金（美元）",
                },
                "style": {
                    "type": "string",
                    "enum": ["leaps", "theta", "stock", "auto"],
                    "description": "偏好风格：leaps=期权进攻, theta=期权收租, stock=纯股票, auto=自动推荐",
                },
            },
            "required": ["symbol", "direction"],
        },
    },
    {
        "name": "position_check",
        "description": "双镜头持仓审查：Serenity 审论点是否还成立，RICH 审技术位是否该加/减/跑。",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "持仓股票代码",
                },
                "thesis": {
                    "type": "string",
                    "description": "原始投资论点",
                },
                "entry_price": {
                    "type": "number",
                    "description": "入场均价",
                },
                "direction": {
                    "type": "string",
                    "enum": ["long", "short"],
                    "description": "持仓方向",
                },
            },
            "required": ["symbol", "thesis", "entry_price", "direction"],
        },
    },
]

_SYNTHESIS_PROMPTS = {
    "full_analysis": """你是一个整合了两套顶级投资框架的决策系统。

## 两套框架

**Serenity（基本面派）**：
- 核心：别买AI，买AI被迫购买的东西
- 方法：卡脖子识别、量产前夜信号、资本催化剂、六维评分
- 输出：这家公司是不是值得买

**RICH/TradingWarz（技术派）**：
- 核心：Fibonacci Golden Zone + K线确认 + 风险收益比铁律
- 方法：多周期共振、LEAPS金字塔、Theta收割、四维评分
- 输出：现在该不该进、怎么进

## 你收到了两份报告

### Serenity 论点评估:
{serenity_result}

### RICH 技术评估:
{rich_result}

## 你的任务：综合决策

### 1. 双框架交叉验证
- Serenity 说值得买吗？评分多少？
- RICH 说现在是好的入场时机吗？评分多少？
- 两者一致 = 高确信。分歧 = 需要权衡。

### 2. 四象限判定

| | RICH: 好时机 | RICH: 不是时候 |
|---|---|---|
| **Serenity: 值得买** | 🟢 全力出击 | 🟡 建观察仓，等位置 |
| **Serenity: 不值得** | 🟡 纯技术交易，轻仓快进快出 | 🔴 不碰 |

当前属于哪个象限？

### 3. 综合评分
- Serenity 分 (60% 权重) + RICH 分 (40% 权重) = 总分
- 基本面更重要，因为它决定的是"这个位置值不值得承受风险"

### 4. 执行方案
- **操作**: 买入 / 建观察仓 / 等待 / 不碰
- **仓位**: 占总资金的百分比{capital_context}
- **入场策略**: 一次性 / 分批 / 等回调
- **具体计划**:
  - 入场价和触发条件
  - 止损价和原因
  - 目标价（分批止盈）
  - 期权策略（如果适用）
- **时间框架**: 短线/波段/中长期

### 5. 风险清单
- 基本面风险（论点最可能死在哪）
- 技术面风险（当前价位的下行空间）
- 组合风险（仓位相关性）

### 6. 一句话总结
用一句话告诉交易员：做不做、怎么做、为什么。""",

    "entry_plan": """你是 RICH (TradingWarz)，你收到了多层技术分析的结果，现在需要输出一份可执行的交易计划。

标的: {symbol}
方向: {direction_text}
可用资金: {capital_text}
偏好风格: {style_text}

## 分析结果

### 多周期共振分析:
{drill_down_result}

### Fibonacci 分析:
{fib_result}

## 你的任务：输出可执行的交易计划

### 交易计划书

#### 1. 方向判定
- 多周期共振支持这个方向吗？
- 如果不支持 → 明确说"不做"，不要硬凑

#### 2. 入场方案
- **入场价**: 具体价格（不是区间）
- **入场条件**: 需要等什么确认？（Green Candle / OBIB / 直接进）
- **分批计划**: 第一笔多少，后续怎么加

#### 3. 风控方案
- **止损价**: 具体价格（基于 Fib / 结构位）
- **止损原因**: 为什么设在这里
- **风险收益比**: 计算结果，是否达到 2:1 铁律
- **最大亏损额**: 具体金额

#### 4. 止盈方案
- **目标1**: 价格 + 减仓比例（如 Fib 1.0 前高，减 1/3）
- **目标2**: 价格 + 减仓比例（如 Fib 1.272）
- **目标3**: 价格 + 减仓比例（如 Fib 1.618）

#### 5. 期权策略（如果适用）
根据偏好风格推荐：
- **LEAPS（进攻）**: 具体合约 + 金字塔加仓计划
- **Theta（收租）**: 具体 Strangle 设置
- **股票**: 纯股票操作方案

#### 6. 执行清单（打勾用）
- [ ] 确认多周期共振 ≥ 3/4
- [ ] 确认 R:R ≥ 2:1
- [ ] 确认有 K 线确认信号
- [ ] 下单: 价格 X, 数量 Y
- [ ] 设置止损: 价格 Z
- [ ] 设置第一目标止盈

## 铁律
- R:R < 2:1 → 整个计划作废，输出"不做"
- 月线方向相反 → 最多轻仓试探
- 没有确认信号 → 等，不追""",

    "position_check": """你是一个双框架持仓审查官。你同时用 Serenity 和 RICH 的视角审查一个持仓。

持仓: {symbol}
方向: {direction_text}
入场价: ${entry_price}

## 审查结果

### Serenity 论点审查（基本面）:
{thesis_check}

### RICH 技术审查:
{technical_check}

## 你的任务：持仓决策

### 1. 论点审查（Serenity 视角）
- 原始论点还成立吗？
- 有没有新的信息改变了论点？
- 量产时间表有没有推迟？
- 竞争格局有没有变化？
- **论点状态**: ✅ 完好 / ⚠️ 受损 / ❌ 失效

### 2. 技术审查（RICH 视角）
- 当前价格在 Fib 的什么位置？
- 多周期共振支持持有方向吗？
- 有没有触发止损？
- 有没有到达止盈目标？
- **技术状态**: 🟢 健康 / 🟡 警惕 / 🔴 危险

### 3. 交叉决策矩阵

| | 技术: 🟢 健康 | 技术: 🟡 警惕 | 技术: 🔴 危险 |
|---|---|---|---|
| **论点: ✅** | 持有/加仓 | 持有，收紧止损 | 减仓保核心 |
| **论点: ⚠️** | 持有，不加仓 | 开始减仓 | 大幅减仓 |
| **论点: ❌** | 趁反弹出 | 立即减仓 | 全部清仓 |

当前属于哪个格子？

### 4. 具体行动
- **操作**: 加仓 / 持有 / 减仓 X% / 清仓
- **理由**: 一句话
- **如果加仓**: 在什么价格加、加多少
- **如果减仓**: 在什么价格减、减多少
- **新的止损/止盈位**

### 5. 下次审查
- 什么事件发生了需要立即重新审查？
- 常规审查建议频率""",
}


class UnifiedSkills:
    def __init__(self, model: str = "claude-sonnet-4-20250514") -> None:
        self._client = anthropic.Anthropic()
        self._model = model
        self._serenity = SerenitySkills(model=model)
        self._rich = RichSkills(model=model)

    def execute(self, name: str, args: dict) -> str:
        handler = getattr(self, f"_skill_{name}", None)
        if not handler:
            return f'{{"error": "Unknown skill: {name}"}}'
        try:
            return handler(**args)
        except Exception as e:
            log.exception("Unified skill %s failed", name)
            return f'{{"error": "{e}"}}'

    def _call_claude(self, system: str, user_msg: str) -> str:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": user_msg}],
        )
        return response.content[0].text

    def _skill_full_analysis(
        self,
        company: str,
        thesis: str,
        direction: str,
        capital: float | None = None,
    ) -> str:
        log.info("Full analysis pipeline: %s — %s", company, thesis)

        serenity_result = self._serenity.execute(
            "thesis_scorecard",
            {"company": company, "thesis": thesis, "timeframe": "medium"},
        )

        rich_result = self._rich.execute(
            "rich_scorecard",
            {"symbol": company, "direction": direction},
        )

        capital_context = ""
        if capital:
            capital_context = f"\n可用资金: ${capital:,.0f}"

        prompt = _SYNTHESIS_PROMPTS["full_analysis"].format(
            serenity_result=serenity_result,
            rich_result=rich_result,
            capital_context=capital_context,
        )
        return self._call_claude(
            prompt,
            f"请对 {company} 做 Serenity × RICH 综合决策。论点: {thesis}，方向: {direction}。",
        )

    def _skill_entry_plan(
        self,
        symbol: str,
        direction: str,
        capital: float | None = None,
        style: str = "auto",
    ) -> str:
        log.info("Entry plan pipeline: %s %s", symbol, direction)

        trend = "up" if direction == "long" else "down"

        drill_down_result = self._rich.execute(
            "drill_down",
            {"symbol": symbol, "bias": direction},
        )

        fib_result = self._rich.execute(
            "fib_analysis",
            {"symbol": symbol, "trend": trend, "timeframe": "daily"},
        )

        direction_text = "做多 (Long)" if direction == "long" else "做空 (Short)"
        capital_text = f"${capital:,.0f}" if capital else "未指定"
        style_map = {
            "leaps": "LEAPS 期权进攻",
            "theta": "Theta 收租",
            "stock": "纯股票",
            "auto": "自动推荐（根据分析结果）",
        }
        style_text = style_map.get(style, "自动推荐")

        prompt = _SYNTHESIS_PROMPTS["entry_plan"].format(
            symbol=symbol,
            direction_text=direction_text,
            capital_text=capital_text,
            style_text=style_text,
            drill_down_result=drill_down_result,
            fib_result=fib_result,
        )
        return self._call_claude(
            prompt,
            f"请为 {symbol} ({direction_text}) 输出完整入场计划。",
        )

    def _skill_position_check(
        self,
        symbol: str,
        thesis: str,
        entry_price: float,
        direction: str,
    ) -> str:
        log.info("Position check: %s @ $%s", symbol, entry_price)

        thesis_check = self._serenity.execute(
            "challenge_thesis",
            {"thesis": thesis, "position": direction},
        )

        technical_check = self._rich.execute(
            "drill_down",
            {"symbol": symbol, "bias": direction},
        )

        direction_text = "做多 (Long)" if direction == "long" else "做空 (Short)"

        prompt = _SYNTHESIS_PROMPTS["position_check"].format(
            symbol=symbol,
            direction_text=direction_text,
            entry_price=entry_price,
            thesis_check=thesis_check,
            technical_check=technical_check,
        )
        return self._call_claude(
            prompt,
            f"请审查持仓: {symbol}，方向 {direction_text}，入场价 ${entry_price}。",
        )
