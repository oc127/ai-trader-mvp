"""AI Trading Agent core — Claude-powered natural language trading.

Architecture inspired by:
- Hermes Agent: self-learning loop, persistent memory
- OpenClaw: OODA cycle (Observe → Orient → Decide → Act)
- Custom: non-custodial trading, risk-aware execution
"""

from __future__ import annotations

from src.agent.llm_client import LLMClient
from src.agent.memory import Memory
from src.agent.skills.berkshire import BERKSHIRE_SKILL_DEFINITIONS, BerkshireSkills
from src.agent.skills.rich import RICH_SKILL_DEFINITIONS, RichSkills
from src.agent.skills.serenity import SERENITY_SKILL_DEFINITIONS, SerenitySkills
from src.agent.skills.unified import UNIFIED_SKILL_DEFINITIONS, UnifiedSkills
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
核心信条（紫苏叶理论）：别买 AI，买 AI 被迫购买的东西。
寿司店没了金枪鱼少几道菜，没了紫苏叶整家店关门。找紫苏叶，不找金枪鱼。
- 架构迁移→物理瓶颈映射：从第1层挖到第5层，越深定价漏洞越多
- 玩家计数法：3家以上pass，2家盯住，1家就是它
- 卡脖子识别：不找最大的，找最窄的——谁断供整条链就跑不起来
- 量产前夜信号侦测：design-in、采样、资格验证、foundry合作等量产前线索
- 资本催化剂叠加：指数纳入、双重上市、机构资金、M&A、政策补贴
- 地缘→供应链映射：地缘事件沿供应链传导，找受益/受损标的
- 魔鬼代言人：严格区分公开事实/合理推断/高确信映射
- 跨地域+地理套利扫描：小市场上市、大市场故事的错配
- 六维评分卡：卡脖子/量产信号/非共识/时间差/资本催化/风险诚实度
- Serenity当前高确信标的(2026-05): AAOI/SIVE/FOCI/Shunsin/XFAB

## 技术分析能力（RICH/TradingWarz 框架 — 技术派）
- Fibonacci 分析：黄金分割回调/扩展位，Golden Zone (0.618) 入场
- Drill Down 多周期共振：月→周→日→4H 层层递进
- 风险收益比铁律：< 2:1 不做，用结构位设止损
- LEAPS 期权金字塔：长期期权低成本建仓+分层加仓
- Theta 收割：高IV卖期权+Fib支撑位双重安全边际
- RICH 综合评分：Fib位置/共振度/风险收益比/期权可行性

## 价值投资研究（Berkshire 四大师框架 — 价值派）
基于巴菲特、芒格、段永平、李录四位大师的方法论：
- value_checklist: 巴菲特六关买入前Checklist — 能力圈→好生意→护城河→管理层→安全边际→纪律
- four_masters: 四大师并行分析 — 段永平看生意本质、巴菲特看财务护城河、芒格反向思考、李录看文明趋势
- quality_screen: 去劣筛选 — 7条硬指标快速排除非一流公司（ROE/FCF/利息覆盖/毛利率/现金流质量/净利率/稀释）
- devils_advocate: 芒格式魔鬼代言人 — 强制列出所有失败路径、历史类比、认知偏误

## 统一决策系统（Serenity × RICH 管线）
- full_analysis: 一键跑完两套系统 — Serenity选股评分 → RICH技术评分 → 四象限综合决策
- entry_plan: 从标的到可执行交易计划 — Drill Down → Fib → R:R → 期权策略
- position_check: 双镜头持仓审查 — 论点还成立吗？技术位该加还是该减？

决策逻辑：
- Serenity说值得 + RICH说好时机 = 🟢 全力出击
- Serenity说值得 + RICH说不是时候 = 🟡 建观察仓，等位置
- Serenity说不值得 + RICH说好时机 = 🟡 纯技术交易，轻仓快进快出
- Serenity说不值得 + RICH说不是时候 = 🔴 不碰

## 思考框架（OODA）
每次收到指令，按这个顺序思考：
1. Observe（观察）：当前市场状态、账户状态是什么？
2. Orient（判断）：这个操作的风险和机会是什么？
3. Decide（决策）：具体执行什么操作？
4. Act（执行）：调用工具执行，报告结果

## 研究框架（Serenity 问题序列）
当用户询问产业/供应链/投资论点时，按 Serenity 的顺序思考：
1. 架构迁移在哪？下一次升级的物理瓶颈是什么？
2. 谁最难替代？谁断供整条链就停？
3. 谁已经被 design-in？有公开证据还是推断？
4. 市场还在用旧报表定价吗？TTM revenue 能代表未来吗？
5. 资本流会不会打爆错配？（指数、上市、机构、M&A）
6. 魔鬼代言：公开事实和推断分开，主动挑战自己

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
_UNIFIED_NAMES = {d["name"] for d in UNIFIED_SKILL_DEFINITIONS}
_BERKSHIRE_NAMES = {d["name"] for d in BERKSHIRE_SKILL_DEFINITIONS}
SKILL_NAMES = _SERENITY_NAMES | _RICH_NAMES | _UNIFIED_NAMES | _BERKSHIRE_NAMES
ALL_TOOL_DEFINITIONS = (
    TOOL_DEFINITIONS
    + SERENITY_SKILL_DEFINITIONS
    + RICH_SKILL_DEFINITIONS
    + UNIFIED_SKILL_DEFINITIONS
    + BERKSHIRE_SKILL_DEFINITIONS
)


class TradingAgent:
    def __init__(
        self,
        tools: TradingTools,
        memory: Memory | None = None,
        model: str | None = None,
    ) -> None:
        self._client = LLMClient(model=model)
        self._tools = tools
        self._serenity = SerenitySkills(model=model)
        self._rich = RichSkills(model=model)
        self._unified = UnifiedSkills(model=model)
        self._berkshire = BerkshireSkills(model=model)
        self._memory = memory or Memory()
        self._history: list[dict] = []
        self._interaction_count = 0

    def chat(self, user_message: str) -> str:
        self._history.append({"role": "user", "content": user_message})
        self._interaction_count += 1

        system = SYSTEM_PROMPT.format(memory_context=self._memory.get_context())

        response = self._client.chat(
            system=system,
            messages=self._history,
            tools=ALL_TOOL_DEFINITIONS,
        )

        while response.stop_reason == "tool_use":
            self._history.append({"role": "assistant", "content": response.content})

            tool_results = []
            for tc in response.tool_calls:
                log.info("Tool call: %s(%s)", tc.name, tc.arguments)
                if tc.name in _UNIFIED_NAMES:
                    result = self._unified.execute(tc.name, tc.arguments)
                elif tc.name in _SERENITY_NAMES:
                    result = self._serenity.execute(tc.name, tc.arguments)
                elif tc.name in _RICH_NAMES:
                    result = self._rich.execute(tc.name, tc.arguments)
                elif tc.name in _BERKSHIRE_NAMES:
                    result = self._berkshire.execute(tc.name, tc.arguments)
                else:
                    result = self._tools.execute(tc.name, tc.arguments)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": result,
                })
                if tc.name in ("place_order", "market_order", "close_position"):
                    self._memory.add_trade({
                        "tool": tc.name,
                        "args": tc.arguments,
                        "result": result[:200],
                    })

            self._history.append({"role": "user", "content": tool_results})

            response = self._client.chat(
                system=system,
                messages=self._history,
                tools=ALL_TOOL_DEFINITIONS,
            )

        reply = response.text or ""
        self._history.append({"role": "assistant", "content": reply})

        if len(self._history) > 40:
            self._history = self._history[-30:]

        return reply

    def reset(self) -> None:
        self._history = []
        self._interaction_count = 0
