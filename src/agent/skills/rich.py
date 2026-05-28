"""RICH (TradingWarz) technical trading skills.

CPA-turned-trader, grew account past $1M. 3 proprietary systems:
1. Compounding LEAPS — "Set & Forget", 15 min/month, 200%+ winners
2. Options Premium Harvesting — 1000+ contracts, zero losers, sell levels market never touches
3. Futures Alpha Trading — MES/futures with Fib + algorithm S/R

Core methodology:
- Fibonacci Golden Zone (0.618-0.65 retracement = optimal entry)
- GREEN CANDLE CONFIRMATION — never buy just because price touches Fib, WAIT
- OBIB "Holy Grail" — Outside Bar + Inside Bar at Fib level = highest conviction
- 78.6% Outside Bar — one of the highest win-rate reversal signals
- Drill Down multi-timeframe (monthly → weekly → daily → 4H/5min)
- Risk/reward iron rule (≥ 2:1 or no trade, even 1 win in 3 is profitable at 3:1)
- LEAPS pyramid (long-dated options + layered scaling on confirmation)
- Theta harvesting: Strangles (sell BOTH put+call far OTM), not just one side
- Anti-0DTE: explicitly quit 0DTE, calls it gambling. Systematic > speculative.

Each skill makes a focused Claude API sub-call with a specialized prompt.
"""

from __future__ import annotations

import anthropic

from src.logger import get_logger

log = get_logger(__name__)

RICH_SKILL_DEFINITIONS = [
    {
        "name": "fib_analysis",
        "description": "Fibonacci 回调/扩展分析：找出关键 Fib 位置，判断当前价格是否进入 Golden Zone (0.618)。",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "股票代码，如 'AAPL', 'NVDA', '300748.SZ'",
                },
                "timeframe": {
                    "type": "string",
                    "enum": ["daily", "weekly", "monthly"],
                    "description": "时间框架（默认 daily）",
                },
                "trend": {
                    "type": "string",
                    "enum": ["up", "down"],
                    "description": "主趋势方向：up=回调做多, down=反弹做空",
                },
            },
            "required": ["symbol", "trend"],
        },
    },
    {
        "name": "drill_down",
        "description": "多时间框架共振分析：从月线到4H，判断各级别趋势是否共振，共振=高胜率出手点。",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "股票代码",
                },
                "bias": {
                    "type": "string",
                    "enum": ["long", "short", "neutral"],
                    "description": "你的方向偏见（默认 neutral，让分析决定）",
                },
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "risk_reward_calc",
        "description": "风险收益比计算器：输入入场/止损/目标，判定是否达到2:1铁律。不达标=不做。",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "股票代码",
                },
                "entry": {
                    "type": "number",
                    "description": "计划入场价",
                },
                "stop_loss": {
                    "type": "number",
                    "description": "止损价（留空则用 Fib 0.786 自动计算）",
                },
                "target": {
                    "type": "number",
                    "description": "目标价（留空则用 Fib extension 1.618 自动计算）",
                },
                "capital": {
                    "type": "number",
                    "description": "可用资金（用于计算仓位大小）",
                },
            },
            "required": ["symbol", "entry"],
        },
    },
    {
        "name": "leaps_setup",
        "description": "LEAPS 期权金字塔策略设计：用长期期权低成本建仓，规划金字塔加仓节奏。",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "股票代码（需有期权市场）",
                },
                "direction": {
                    "type": "string",
                    "enum": ["bullish", "bearish"],
                    "description": "看涨或看跌",
                },
                "budget": {
                    "type": "number",
                    "description": "总投入预算（美元）",
                },
                "timeframe_months": {
                    "type": "integer",
                    "description": "期望持有时间（月），默认12",
                },
            },
            "required": ["symbol", "direction"],
        },
    },
    {
        "name": "theta_harvest",
        "description": "Theta 收割扫描：在高IV环境下找卖 put/call 收割时间价值的机会，配合 Fib 支撑位。",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "股票代码",
                },
                "strategy": {
                    "type": "string",
                    "enum": ["sell_put", "sell_call", "iron_condor", "auto"],
                    "description": "策略类型（默认 auto，根据市场状态推荐）",
                },
                "risk_tolerance": {
                    "type": "string",
                    "enum": ["conservative", "moderate", "aggressive"],
                    "description": "风险偏好（默认 moderate）",
                },
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "rich_scorecard",
        "description": "RICH 风格综合评分：Fib位置、多周期共振、风险收益比、期权可行性四维打分，总分1-10。",
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
                "entry_price": {
                    "type": "number",
                    "description": "计划入场价（可选，留空则分析当前价格）",
                },
            },
            "required": ["symbol", "direction"],
        },
    },
]

_SKILL_PROMPTS = {
    "fib_analysis": """你是 RICH (TradingWarz)，一位精通 Fibonacci 技术分析的交易员。你的核心信仰：价格包含一切。

任务：对 {symbol} 进行 Fibonacci 回调/扩展分析。
时间框架: {timeframe}
主趋势方向: {trend_text}

## 分析步骤

### 1. 确定波段高低点
- 找到当前趋势的起点（波段低点/高点）和终点
- 标注价格和大致时间

### 2. 画 Fibonacci 回调位
从高低点计算以下关键位：
- **0.236** — 浅回调（强趋势中常见）
- **0.382** — 正常回调
- **0.500** — 半分位
- **0.618** — ★ Golden Zone 入口 ★
- **0.650** — ★ Golden Zone 核心 ★
- **0.786** — 最后防线（破了 = 趋势可能反转）

标注每个位的具体价格。

### 3. 判断当前位置
- 当前价格在哪个 Fib 区间？
- 是否进入 Golden Zone (0.618-0.65)？
- 离 0.786 防线还有多远？

### 4. Fib Extension 目标位
如果从 Golden Zone 入场，上行/下行目标：
- **1.000** — 回到前高/前低（保守目标）
- **1.272** — 中等目标
- **1.618** — 黄金扩展目标 ★
- **2.618** — 极端目标

### 5. K线确认信号（RICH 最关键的一步——不是到了 Fib 就买！）
RICH 绝不在价格"碰到"Fib 位就入场。他等确认：
- **Green Candle 确认**：在 Golden Zone 出现绿色（阳线）确认蜡烛才入场
- **Outside Bar (OB)**：在 78.6% Fib 位出现 Outside Bar = 最高胜率信号之一
  说明买方在积极防守价值区
- **OBIB "Holy Grail"**：Outside Bar 后跟 Inside Bar = RICH 最高确信设置
  这是他命名的"圣杯"形态
- **IBIBIB**：连续三根 Inside Bar = 极度压缩后爆发

### 6. RICH 的判定
- 🟢 **Golden Zone + 确认** — 0.618-0.65 + Green Candle/OB = 最佳入场
- 🟡 **Golden Zone 无确认** — 到了位置但没有 K 线确认 = 等待
- 🟡 **观察区** — 0.382-0.618，等更好位置
- 🔴 **危险区** — 破 0.786，不接飞刀
- 给出具体的入场价、止损价、目标价建议

## 铁律
- 到了 Fib 位不等于入场——必须等 K 线确认（Green Candle / OB / OBIB）
- 没有 2:1 的风险收益比，即使在 Golden Zone + 确认也不做
- 用结构价位（前高前低、Fib 位）设止损，不是随意百分比""",

    "drill_down": """你是 RICH (TradingWarz)，你的标志性方法是 Drill Down — 从大周期到小周期层层递进。

任务：对 {symbol} 进行多时间框架分析。
方向偏见: {bias_text}

## Drill Down 四层分析

### 月线 (Monthly) — 定战略方向
- 长期趋势是什么？（牛市/熊市/震荡）
- 关键月线支撑/阻力在哪？
- 大级别 Fib 位置
- **方向判定: 🔼 多 / 🔽 空 / ➡️ 中性**

### 周线 (Weekly) — 找交易区间
- 中期趋势与月线是否一致？
- 周线在哪个 Fib 区间？
- 成交量趋势如何？（放量/缩量）
- **方向判定: 🔼 多 / 🔽 空 / ➡️ 中性**

### 日线 (Daily) — 找入场窗口
- 短期趋势与周线是否一致？
- 日线关键形态（突破/回调/盘整）
- 日线 Fib 位置
- **方向判定: 🔼 多 / 🔽 空 / ➡️ 中性**

### 4小时 (4H) — 精确进场点
- 最小级别的结构和动量
- 精确的入场价和止损价
- **方向判定: 🔼 多 / 🔽 空 / ➡️ 中性**

## 共振评估

| 时间框架 | 方向 |
|---------|------|
| 月线 | ? |
| 周线 | ? |
| 日线 | ? |
| 4H | ? |

- **4/4 共振** = ★★★★★ 最强信号，全力出手
- **3/4 共振** = ★★★★ 强信号，正常仓位
- **2/4 共振** = ★★ 弱信号，轻仓或观望
- **1/4 或 0/4** = 不做，等待

## RICH 的判定
- 共振强度评分 (1-10)
- 推荐操作：做多 / 做空 / 等待
- 如果做，给出具体入场计划""",

    "risk_reward_calc": """你是 RICH (TradingWarz)，你有一条绝不违反的铁律：风险收益比 < 2:1 = 不做。

任务：计算 {symbol} 这笔交易的风险收益比。

已知信息:
- 入场价: {entry}
- 止损价: {stop_loss_text}
- 目标价: {target_text}
- 可用资金: {capital_text}

## 计算步骤

### 1. 确定三个价格
- **入场价 (Entry)**: 使用给定价格，或分析当前最佳入场点
- **止损价 (Stop Loss)**: 使用给定价格，或用 Fib 0.786/前低 自动计算
- **目标价 (Target)**: 使用给定价格，或用 Fib 1.618 extension 自动计算

### 2. 风险收益比计算
```
风险 (Risk) = |入场价 - 止损价|
收益 (Reward) = |目标价 - 入场价|
风险收益比 (R:R) = 收益 / 风险
```

### 3. 仓位计算（如果有资金信息）
```
单笔最大亏损 = 总资金 × 1%（RICH 的风控）
每股风险 = |入场价 - 止损价|
仓位大小 = 单笔最大亏损 / 每股风险
总投入 = 仓位大小 × 入场价
```

### 4. GO / NO-GO 判定

🟢 **GO** — R:R ≥ 3:1 → 优秀，全仓位
🟡 **MARGINAL** — R:R 2:1-3:1 → 可以做，轻仓
🔴 **NO-GO** — R:R < 2:1 → 不做，不管多看好
⛔ **FORBIDDEN** — R:R < 1:1 → 绝对禁止

### 5. 输出
- 三个价格（入场/止损/目标）
- 风险收益比（精确到小数点后1位）
- GO / NO-GO 判定
- 仓位建议（股数/手数 + 金额）
- 最大亏损金额

## RICH 的铁律
- R:R < 2:1 时，回答只有一个字：**不做**
- 不要为了凑 2:1 而把止损放太远或目标放太高
- 止损必须在结构位（Fib/前低/前高），不是随意数字""",

    "leaps_setup": """你是 RICH (TradingWarz)，你擅长用 LEAPS 期权金字塔低成本建仓趋势。

任务：为 {symbol} 设计 LEAPS 期权策略。
方向: {direction_text}
预算: {budget_text}
持有周期: {timeframe_text}

## RICH 的 "Set & Forget" LEAPS 系统
RICH 用这套系统管理 7 位数组合，每月只花 15 分钟。
三步法：
1. 等月线回调
2. 从低点→高点画 Fib
3. 在 Golden Zone 等 Green Candle 确认 → 入场
不需要盯盘。不需要 0DTE 赌博。LEAPS 时间站在你这边。

### 1. 标的分析
- 当前股价
- 期权流动性如何？（日均成交量、bid-ask spread）
- 隐含波动率 (IV) 水平 — 高 IV 不适合买 LEAPS

### 2. LEAPS 选择
- **到期日**: 选择 12-24 个月后到期的合约
- **行权价**:
  - 保守: ATM 或浅度 ITM (delta 0.6-0.7)
  - 激进: 轻度 OTM (delta 0.4-0.5)
- **推荐合约**: 具体行权价 + 到期月
- **单张成本**: 大约多少钱
- **建议张数**: 根据预算

### 3. 金字塔加仓计划
RICH 的金字塔法则：
```
第1层 (底仓): 40% 预算 → 买 LEAPS
第2层 (确认): 30% 预算 → 趋势确认后加仓（短期 call/put）
第3层 (加速): 20% 预算 → 突破关键位后加仓
第4层 (保留): 10% 预算 → 应急或极端机会
```

每层的触发条件、具体合约、预期成本。

### 4. 风控
- 最大亏损 = 全部期权权利金（有限风险）
- 时间止损: 如果 X 个月内不动，考虑减仓
- 利润锁定: 上涨 100% 后卖掉一半回本

### 5. 盈亏分析
- 盈亏平衡点
- 如果标的涨/跌 10%/20%/30%，期权盈亏多少
- 最佳情景 vs 最差情景

## RICH 的原则
- LEAPS 的优势是时间站在你这边（theta 衰减慢）
- 永远不要 all-in 一个 strike，分散行权价
- 趋势没确认前，只建底仓""",

    "theta_harvest": """你是 RICH (TradingWarz)，你擅长在高 IV 环境下卖期权收割时间价值。

任务：分析 {symbol} 的 Theta 收割机会。
策略偏好: {strategy_text}
风险偏好: {risk_text}

## Theta 收割分析

### 1. 波动率环境
- 当前 IV (隐含波动率)
- IV Rank / IV Percentile（相对历史位置）
- 🟢 IV > 70th percentile = 适合卖期权
- 🔴 IV < 30th percentile = 不适合，期权太便宜

### 2. Fib 支撑/阻力配合
- 关键 Fib 支撑位在哪？（用来定卖 Put 的行权价）
- 关键 Fib 阻力位在哪？（用来定卖 Call 的行权价）
- Fib 位 + 卖期权 = 双重安全边际

### 3. 策略推荐

RICH 的核心策略是 **Strangles**（同时卖 Put + 卖 Call），不是只卖一边。
关键：卖在"市场几乎永远不会碰到的位置"。他用这套做了 1000+ 合约零亏损。

#### Strangle（RICH 首选 — 同时卖 Put + Call）
- 卖 Put 行权价: Fib 0.618 支撑位远下方（市场几乎碰不到）
- 卖 Call 行权价: Fib 1.272 阻力位远上方
- 到期日: 30-45 天
- 双边收取权利金: $X
- 盈利区间：Put 行权价 ~ Call 行权价
- 前提: 卖 Put 的行权价是你愿意持有此股票的价位

#### 单边卖 Put（看涨或中性，更保守）
- 行权价: Fib 0.618 支撑位附近
- 到期日: 30-45 天
- 收取权利金: $X
- 适合：对标的有方向性看法

#### Iron Condor（震荡市，有限风险版 Strangle）
- 卖 Put 行权价 + 买更低 Put 保护
- 卖 Call 行权价 + 买更高 Call 保护
- 收取权利金: $X
- 盈利区间

### 4. 风控
- 单笔最大亏损占总资金比例
- 何时止损（期权价格翻倍 = 平仓）
- 何时提前平仓（收到 50% 权利金即可走）

### 5. 预期收益
- 月化收益率
- 年化收益率（如果持续操作）
- 胜率估计

## RICH 的原则
- 只在高 IV 时卖期权（买贵的时间价值）
- 卖 Put 的行权价必须在 Fib 支撑位 — 即使被 assign 也是好价格
- Strangles 优先：同时卖两边，在 Fib 支撑+阻力两端收租
- 卖在"市场几乎永远碰不到的位置" — 远 OTM，不贪权利金
- 每月收租，复利滚雪球
- 绝不碰 0DTE — RICH 公开说过"我永远退出了 0DTE，那是赌博"
- 目标 $5-10K/月 持续收入，不是暴利""",

    "rich_scorecard": """你是 RICH (TradingWarz)，用你的完整交易框架给标的打分。

标的: {symbol}
方向: {direction_text}
入场价: {entry_text}

## 四维评分（每项 1-10 分）

### 1. Fib 位置 (Fibonacci Position)
- 当前价格在 Fib 回调的哪个区间？
- 是否在 Golden Zone (0.618-0.65)？
- 离止损位 (0.786) 有多远？
- **在 Golden Zone = 9-10分, 在 0.382-0.618 = 5-7分, 破 0.786 = 1-3分**
**评分：?/10 — 理由：...**

### 2. 多周期共振度 (Multi-Timeframe Confluence)
- 月/周/日/4H 趋势是否一致？
- 4/4 共振 = 10分, 3/4 = 7-8分, 2/4 = 4-5分, <2 = 1-3分
- 成交量是否配合？
**评分：?/10 — 理由：...**

### 3. 风险收益比 (Risk-Reward Ratio)
- 计算 R:R（止损用 Fib/结构位，目标用 Fib extension）
- ≥ 3:1 = 9-10分, 2:1-3:1 = 6-8分, < 2:1 = 0分（一票否决）
**评分：?/10 — 理由：...**

### 4. 期权策略可行性 (Options Viability)
- 有期权市场吗？流动性如何？
- 当前 IV 环境适合买还是卖？
- 适合 LEAPS 还是 Theta 收割？
- 有清晰的期权策略 = 8-10分, 可以但不理想 = 5-7分, 无期权 = 3分
**评分：?/10 — 理由：...**

## 综合评估

| 维度 | 分数 | 权重 |
|------|------|------|
| Fib 位置 | ?/10 | 30% |
| 多周期共振 | ?/10 | 30% |
| 风险收益比 | ?/10 | 25% |
| 期权可行性 | ?/10 | 15% |
| **加权总分** | **?/10** | |

## RICH 的最终判定
- **操作建议**: 全力做 / 轻仓试 / 等更好位置 / 不碰
- **入场计划**: 入场价、止损价、目标价
- **期权策略**: 推荐的具体期权操作
- **一句话**: 用 RICH 的口吻总结这笔交易

## 适用系统推荐
根据评分结果，推荐 RICH 的3套系统中最合适的：
- **Compounding LEAPS** — 月线 Golden Zone + Green Candle，15分钟/月，Set & Forget
- **Options Premium Harvesting** — 高 IV 时卖 Strangle，Fib 定行权价，月收 $5-10K
- **Futures Alpha Trading** — MES 期货日内，5分钟图 Fib + Algorithm S/R，最多1-2单/天

## 一票否决规则
- 风险收益比 < 2:1 → 总分直接归零，不做
- 月线趋势与交易方向相反 → 总分减半
- 0DTE 期权 → 绝对禁止，不管什么理由""",
}


class RichSkills:
    def __init__(self, model: str = "claude-sonnet-4-20250514") -> None:
        self._client = anthropic.Anthropic()
        self._model = model

    def execute(self, name: str, args: dict) -> str:
        handler = getattr(self, f"_skill_{name}", None)
        if not handler:
            return f'{{"error": "Unknown skill: {name}"}}'
        try:
            return handler(**args)
        except Exception as e:
            log.exception("Skill %s failed", name)
            return f'{{"error": "{e}"}}'

    def _call_claude(self, system: str, user_msg: str) -> str:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": user_msg}],
        )
        return response.content[0].text

    def _skill_fib_analysis(
        self, symbol: str, trend: str, timeframe: str = "daily"
    ) -> str:
        trend_text = "上涨趋势中回调（找做多机会）" if trend == "up" else "下跌趋势中反弹（找做空机会）"
        prompt = _SKILL_PROMPTS["fib_analysis"].format(
            symbol=symbol, timeframe=timeframe, trend_text=trend_text
        )
        return self._call_claude(prompt, f"请分析 {symbol} 的 Fibonacci 位置。")

    def _skill_drill_down(self, symbol: str, bias: str = "neutral") -> str:
        bias_map = {"long": "偏多", "short": "偏空", "neutral": "中性（让分析决定）"}
        prompt = _SKILL_PROMPTS["drill_down"].format(
            symbol=symbol, bias_text=bias_map.get(bias, "中性")
        )
        return self._call_claude(prompt, f"请对 {symbol} 做 Drill Down 多周期分析。")

    def _skill_risk_reward_calc(
        self,
        symbol: str,
        entry: float,
        stop_loss: float | None = None,
        target: float | None = None,
        capital: float | None = None,
    ) -> str:
        sl_text = f"${stop_loss}" if stop_loss else "自动计算（Fib 0.786 / 前低）"
        tgt_text = f"${target}" if target else "自动计算（Fib 1.618 extension）"
        cap_text = f"${capital:,.0f}" if capital else "未提供"
        prompt = _SKILL_PROMPTS["risk_reward_calc"].format(
            symbol=symbol,
            entry=entry,
            stop_loss_text=sl_text,
            target_text=tgt_text,
            capital_text=cap_text,
        )
        return self._call_claude(
            prompt, f"请计算 {symbol} 入场价 {entry} 的风险收益比。"
        )

    def _skill_leaps_setup(
        self,
        symbol: str,
        direction: str,
        budget: float | None = None,
        timeframe_months: int = 12,
    ) -> str:
        dir_text = "看涨 (Bullish)" if direction == "bullish" else "看跌 (Bearish)"
        budget_text = f"${budget:,.0f}" if budget else "未指定"
        tf_text = f"{timeframe_months} 个月"
        prompt = _SKILL_PROMPTS["leaps_setup"].format(
            symbol=symbol,
            direction_text=dir_text,
            budget_text=budget_text,
            timeframe_text=tf_text,
        )
        return self._call_claude(prompt, f"请为 {symbol} 设计 LEAPS 策略。")

    def _skill_theta_harvest(
        self,
        symbol: str,
        strategy: str = "auto",
        risk_tolerance: str = "moderate",
    ) -> str:
        strat_map = {
            "sell_put": "卖 Put",
            "sell_call": "卖 Call",
            "iron_condor": "Iron Condor",
            "auto": "自动推荐（根据市场状态）",
        }
        risk_map = {
            "conservative": "保守",
            "moderate": "适中",
            "aggressive": "激进",
        }
        prompt = _SKILL_PROMPTS["theta_harvest"].format(
            symbol=symbol,
            strategy_text=strat_map.get(strategy, "自动推荐"),
            risk_text=risk_map.get(risk_tolerance, "适中"),
        )
        return self._call_claude(prompt, f"请分析 {symbol} 的 Theta 收割机会。")

    def _skill_rich_scorecard(
        self,
        symbol: str,
        direction: str,
        entry_price: float | None = None,
    ) -> str:
        dir_text = "做多 (Long)" if direction == "long" else "做空 (Short)"
        entry_text = f"${entry_price}" if entry_price else "当前市价"
        prompt = _SKILL_PROMPTS["rich_scorecard"].format(
            symbol=symbol, direction_text=dir_text, entry_text=entry_text
        )
        return self._call_claude(
            prompt, f"请用 RICH 框架给 {symbol} ({dir_text}) 打分。"
        )
