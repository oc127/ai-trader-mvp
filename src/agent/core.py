"""AI Trading Agent core — Claude-powered natural language trading."""

from __future__ import annotations

import anthropic

from src.agent.tools import TOOL_DEFINITIONS, TradingTools
from src.logger import get_logger

log = get_logger(__name__)

SYSTEM_PROMPT = """你是一个专业的加密货币交易 Agent，连接到 Hyperliquid 交易所。

你的能力：
- 查看账户余额和持仓
- 查看任何币的价格和订单簿
- 扫描适合做市的交易对
- 下限价单和市价单
- 撤单、平仓
- 设置杠杆
- 查看资金费率

交易规则：
- 用户资金量小（$200-1000），谨慎操作
- 每笔订单默认不超过 $50，除非用户明确指定
- 执行交易前简要说明你要做什么，确认风险
- 用中文回复
- 回答简洁，不要废话
- 如果用户的指令不清楚，先问清楚再执行
- 报价和金额用 $ 符号，精度合理即可

你是用户的交易助手，帮他在 Hyperliquid 上执行交易策略。"""


class TradingAgent:
    def __init__(self, tools: TradingTools, model: str = "claude-sonnet-4-20250514") -> None:
        self._client = anthropic.Anthropic()
        self._tools = tools
        self._model = model
        self._history: list[dict] = []

    def chat(self, user_message: str) -> str:
        self._history.append({"role": "user", "content": user_message})

        response = self._client.messages.create(
            model=self._model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
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

            self._history.append({"role": "user", "content": tool_results})

            response = self._client.messages.create(
                model=self._model,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
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
