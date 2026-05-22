"""AI Trading Agent core — Claude-powered natural language trading.

Architecture inspired by:
- Hermes Agent: self-learning loop, persistent memory
- OpenClaw: OODA cycle (Observe → Orient → Decide → Act)
- Custom: non-custodial trading, risk-aware execution
"""

from __future__ import annotations

import anthropic

from src.agent.memory import Memory
from src.agent.tools import TOOL_DEFINITIONS, TradingTools
from src.logger import get_logger

log = get_logger(__name__)

SYSTEM_PROMPT = """你是一个专业的加密货币交易 Agent，连接到 Hyperliquid 交易所。

## 能力
- 查看账户余额和持仓
- 查看任何币的价格和订单簿
- 扫描适合做市的交易对
- 下限价单和市价单
- 撤单、平仓
- 设置杠杆
- 查看资金费率

## 思考框架（OODA）
每次收到指令，按这个顺序思考：
1. Observe（观察）：当前市场状态、账户状态是什么？
2. Orient（判断）：这个操作的风险和机会是什么？
3. Decide（决策）：具体执行什么操作？
4. Act（执行）：调用工具执行，报告结果

## 规则
- 用户资金量小，谨慎操作
- 每笔订单默认不超过 $50，除非用户明确指定
- 执行交易前简要说明风险
- 用中文回复，简洁不废话
- 指令不清楚时先问清楚
- 报价用 $ 符号

## 交易记忆
{memory_context}"""


class TradingAgent:
    def __init__(
        self,
        tools: TradingTools,
        memory: Memory | None = None,
        model: str = "claude-sonnet-4-20250514",
    ) -> None:
        self._client = anthropic.Anthropic()
        self._tools = tools
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
            tools=TOOL_DEFINITIONS,
            messages=self._history,
        )

        while response.stop_reason == "tool_use":
            assistant_content = response.content
            self._history.append({"role": "assistant", "content": assistant_content})

            tool_results = []
            for block in assistant_content:
                if block.type == "tool_use":
                    log.info("Tool call: %s(%s)", block.name, block.input)
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
                tools=TOOL_DEFINITIONS,
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
