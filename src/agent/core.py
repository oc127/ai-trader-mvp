"""AI Trading Agent core — Claude-powered natural language trading.

Architecture inspired by:
- Hermes Agent: self-learning loop, persistent memory
- OpenClaw: OODA cycle (Observe → Orient → Decide → Act)
- Custom: non-custodial trading, risk-aware execution
"""

from __future__ import annotations

import anthropic

from src.agent.memory import Memory
from src.agent.skills.rich import RICH_SKILL_DEFINITIONS, RichSkills
from src.agent.skills.serenity import SERENITY_SKILL_DEFINITIONS, SerenitySkills
from src.agent.tools import TOOL_DEFINITIONS, TradingTools
from src.logger import get_logger

log = get_logger(__name__)

SYSTEM_PROMPT = """你是一个专业的加密货币交易 Agent，连接到 Hyperliquid 交易所。你同时具备深度产业链研究能力。

## 交易能力
- 查看账户余额和持仓
- 查看任何币的价格和订单簿
- 扫描适合做市的交易对
- 下限价单和市价单
- 撤单、平仓
- 设置杠杆
- 查看资金费率

## 研究能力（Serenity 卡脖子框架 — 基本面派）
- 逆向供应链映射：从终端产品追溯到卡脖子环节
- 卡脖子公司识别：找出市占率>50%、不可替代的公司
- 地缘政治→供应链映射：将地缘事件转化为具体投资机会
- 魔鬼代言人：系统性挑战投资论点
- 跨地域扫描：美/台/欧/日/韩/中 全市场公司筛选
- 论点评分卡：卡脖子强度、非共识度、时间差、催化剂四维打分

## 技术分析能力（RICH/TradingWarz 框架 — 技术派）
- Fibonacci 分析：黄金分割回调/扩展位，Golden Zone (0.618) 入场
- Drill Down 多周期共振：月→周→日→4H 层层递进
- 风险收益比铁律：< 2:1 不做，用结构位设止损
- LEAPS 期权金字塔：长期期权低成本建仓+分层加仓
- Theta 收割：高IV卖期权+Fib支撑位双重安全边际
- RICH 综合评分：Fib位置/共振度/风险收益比/期权可行性

## 思考框架（OODA）
每次收到指令，按这个顺序思考：
1. Observe（观察）：当前市场状态、账户状态是什么？
2. Orient（判断）：这个操作的风险和机会是什么？
3. Decide（决策）：具体执行什么操作？
4. Act（执行）：调用工具执行，报告结果

## 研究框架（Serenity 方法论）
当用户询问产业/供应链/投资论点时：
1. 逆向映射：从终端产品向上追溯，找到瓶颈
2. 卡脖子评估：市占率、替代成本、技术壁垒
3. 非共识检验：这是共识还是独到洞察？
4. 时间差定位：市场定价了多少？催化剂在哪？
5. 魔鬼代言：主动挑战自己的结论

## 规则
- 用户资金量小，谨慎操作
- 每笔订单默认不超过 $50，除非用户明确指定
- 执行交易前简要说明风险
- 用中文回复，简洁不废话
- 指令不清楚时先问清楚
- 报价用 $ 符号
- 研究分析要有数据支撑，标注信息来源和确信度

## 交易记忆
{memory_context}"""


_SERENITY_NAMES = {d["name"] for d in SERENITY_SKILL_DEFINITIONS}
_RICH_NAMES = {d["name"] for d in RICH_SKILL_DEFINITIONS}
SKILL_NAMES = _SERENITY_NAMES | _RICH_NAMES
ALL_TOOL_DEFINITIONS = TOOL_DEFINITIONS + SERENITY_SKILL_DEFINITIONS + RICH_SKILL_DEFINITIONS


class TradingAgent:
    def __init__(
        self,
        tools: TradingTools,
        memory: Memory | None = None,
        model: str = "claude-sonnet-4-20250514",
    ) -> None:
        self._client = anthropic.Anthropic()
        self._tools = tools
        self._serenity = SerenitySkills(model=model)
        self._rich = RichSkills(model=model)
        self._memory = memory or Memory()
        self._model = model
        self._history: list[dict] = []
        self._interaction_count = 0

    def chat(self, user_message: str) -> str:
        self._history.append({"role": "user", "content": user_message})
        self._interaction_count += 1

        system = SYSTEM_PROMPT.format(memory_context=self._memory.get_context())

        response = self._client.messages.create(
            model=self._model,
            max_tokens=4096,
            system=system,
            tools=ALL_TOOL_DEFINITIONS,
            messages=self._history,
        )

        while response.stop_reason == "tool_use":
            assistant_content = response.content
            self._history.append({"role": "assistant", "content": assistant_content})

            tool_results = []
            for block in assistant_content:
                if block.type == "tool_use":
                    log.info("Tool call: %s(%s)", block.name, block.input)
                    if block.name in _SERENITY_NAMES:
                        result = self._serenity.execute(block.name, block.input)
                    elif block.name in _RICH_NAMES:
                        result = self._rich.execute(block.name, block.input)
                    else:
                        result = self._tools.execute(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })
                    if block.name in ("place_order", "market_order", "close_position"):
                        self._memory.add_trade({
                            "tool": block.name,
                            "args": block.input,
                            "result": result[:200],
                        })

            self._history.append({"role": "user", "content": tool_results})

            response = self._client.messages.create(
                model=self._model,
                max_tokens=4096,
                system=system,
                tools=ALL_TOOL_DEFINITIONS,
                messages=self._history,
            )

        text_parts = []
        for block in response.content:
            if hasattr(block, "text"):
                text_parts.append(block.text)

        reply = "\n".join(text_parts)
        self._history.append({"role": "assistant", "content": reply})

        if len(self._history) > 40:
            self._history = self._history[-30:]

        return reply

    def reset(self) -> None:
        self._history = []
        self._interaction_count = 0
