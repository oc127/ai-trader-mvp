"""Serenity chokepoint research skills.

Methodology based on the Serenity framework:
- Reverse supply chain mapping (end product → bottleneck)
- Chokepoint identification (monopoly, irreplaceable, single-source)
- Cross-geography stock screening (US/TW/EU/JP/KR)
- Geopolitical event → supply chain coordinate mapping
- Devil's advocate thesis challenging
- Non-consensus + time-gap scoring

Each skill makes a focused Claude API sub-call with a specialized prompt
to produce deep, structured research output.
"""

from __future__ import annotations

import anthropic

from src.logger import get_logger

log = get_logger(__name__)

SERENITY_SKILL_DEFINITIONS = [
    {
        "name": "map_supply_chain",
        "description": "逆向供应链映射：从终端产品/行业出发，追溯到上游每一层，标注关键公司和卡脖子环节。",
        "input_schema": {
            "type": "object",
            "properties": {
                "product": {
                    "type": "string",
                    "description": "终端产品或行业，如 'AI服务器', 'iPhone', '电动汽车', 'HBM内存'",
                },
                "depth": {
                    "type": "string",
                    "enum": ["quick", "deep"],
                    "description": "分析深度：quick=关键环节概览, deep=完整供应链（默认 deep）",
                },
            },
            "required": ["product"],
        },
    },
    {
        "name": "find_chokepoints",
        "description": "卡脖子识别：在某个行业/供应链中找出市占率>50%、替代成本极高、不可绕过的公司。",
        "input_schema": {
            "type": "object",
            "properties": {
                "industry": {
                    "type": "string",
                    "description": "行业或供应链环节，如 'AI芯片', '光刻机', '先进封装', '碳化硅'",
                },
                "region": {
                    "type": "string",
                    "enum": ["all", "us", "tw", "eu", "jp", "kr", "cn"],
                    "description": "筛选区域（默认 all）",
                },
            },
            "required": ["industry"],
        },
    },
    {
        "name": "geopolitical_impact",
        "description": "地缘→供应链映射：分析地缘政治事件（制裁、关税、政策）如何沿供应链传导，找到受益/受损标的。",
        "input_schema": {
            "type": "object",
            "properties": {
                "event": {
                    "type": "string",
                    "description": "地缘事件描述，如 '美国对华AI芯片出口管制升级', '台海局势紧张'",
                },
                "supply_chains": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "关注的供应链（可选），如 ['AI芯片', '先进封装', 'EDA工具']",
                },
            },
            "required": ["event"],
        },
    },
    {
        "name": "challenge_thesis",
        "description": "魔鬼代言人：系统性挑战一个投资论点。找出逻辑漏洞、隐含假设、被忽略的风险、反面证据。",
        "input_schema": {
            "type": "object",
            "properties": {
                "thesis": {
                    "type": "string",
                    "description": "投资论点，如 '台积电在先进制程上不可替代，值得长期持有'",
                },
                "position": {
                    "type": "string",
                    "enum": ["long", "short"],
                    "description": "你持有的方向（我来挑战你）",
                },
            },
            "required": ["thesis", "position"],
        },
    },
    {
        "name": "cross_market_scan",
        "description": "跨地域扫描：给定一个卡脖子主题，在全球市场（美/台/欧/日/韩/中）找出所有相关上市公司。",
        "input_schema": {
            "type": "object",
            "properties": {
                "theme": {
                    "type": "string",
                    "description": "投资主题，如 '先进封装', 'GPU供应链', '碳化硅功率器件', 'ASML供应链'",
                },
                "min_market_cap_usd": {
                    "type": "string",
                    "description": "最低市值（美元），如 '500M', '1B', '10B'（默认 1B）",
                },
            },
            "required": ["theme"],
        },
    },
    {
        "name": "thesis_scorecard",
        "description": "论点评分卡：从卡脖子强度、非共识度、时间差、催化剂清晰度四个维度给投资论点打分（1-10）。",
        "input_schema": {
            "type": "object",
            "properties": {
                "company": {
                    "type": "string",
                    "description": "公司名称或股票代码，如 'ASML', '台积电/TSM', 'BE Semiconductor/BESI'",
                },
                "thesis": {
                    "type": "string",
                    "description": "投资论点简述",
                },
                "timeframe": {
                    "type": "string",
                    "enum": ["short", "medium", "long"],
                    "description": "投资周期：short(1-3月), medium(3-12月), long(1-3年)。默认 medium",
                },
            },
            "required": ["company", "thesis"],
        },
    },
]

_SKILL_PROMPTS = {
    "map_supply_chain": """你是一位供应链研究专家，擅长逆向映射产业链。

任务：为 "{product}" 绘制完整的逆向供应链地图。

## 输出格式

从终端产品开始，逐层往上游追溯。每层标注：
- 环节名称
- 关键公司（标注市占率估计、上市地、股票代码）
- 卡脖子程度（★ 到 ★★★★★）
- 替代方案（有/困难/几乎无）

{depth_instruction}

## 规则
- 公司信息要准确，标注股票代码和交易所
- 市占率用公开数据，标注"约"表示估计
- 重点标注卡脖子程度★★★★以上的环节
- 最后给出"卡脖子热力图"：哪些环节最值得深入研究""",

    "find_chokepoints": """你是一位产业链卡脖子分析师。

任务：在 "{industry}" 领域找出所有卡脖子公司。

## 筛选标准（Serenity框架）
1. **市场集中度**：该环节前3名合计市占率 > 70%
2. **替代成本**：客户切换需要 > 2年或 > $1B投入
3. **技术壁垒**：需要10年以上积累的核心技术
4. **单一来源**：存在只有1-2家能供应的关键环节
5. **地理集中**：产能集中在单一国家/地区

区域筛选: {region}

## 输出格式（每个卡脖子公司）
- **公司名** (代码, 交易所) — 市值
- 卡脖子领域：具体做什么
- 市占率：X%（数据来源/时间）
- 替代难度：1-10 分 + 原因
- 护城河类型：专利/know-how/客户锁定/设备壁垒
- 风险：什么情况下卡脖子地位会被削弱

最后按 "卡脖子强度" 排序，给出 Top 5 投资优先级。""",

    "geopolitical_impact": """你是一位地缘政治-供应链分析师，擅长将宏观事件映射到具体公司。

任务：分析这个地缘事件的供应链影响：
**"{event}"**

{chains_context}

## 分析框架

### 1. 事件拆解
- 直接影响：哪些环节被直接波及？
- 间接传导：沿供应链上下游如何传导？（1阶→2阶→3阶效应）
- 时间维度：短期冲击 vs 中期重组 vs 长期格局变化

### 2. 供应链坐标定位
对每个受影响的环节：
- **受损方**（代码, 交易所）：为什么受损、影响程度
- **受益方**（代码, 交易所）：为什么受益、受益程度
- **替代供应商**：谁在抢份额

### 3. 投资地图
- 🔴 回避：受损最大、恢复最慢的标的
- 🟢 机会：受益最大、市场尚未定价的标的
- ⏰ 时间差：市场需要多久才会反应？

## 规则
- 列出具体公司、代码、交易所
- 区分 "已定价" vs "尚未定价" 的影响
- 标注你的确信度（高/中/低）""",

    "challenge_thesis": """你是一位严厉的投资论点审查官（魔鬼代言人）。你的工作是系统性地摧毁投资论点。

要挑战的论点：
**"{thesis}"**
持有方向：**{position}**

## 你的武器（按顺序使用）

### 1. 逻辑审查
- 论点的核心假设是什么？哪些假设未经验证？
- 有没有 "如果...那么..." 链条中的薄弱环节？
- 是否存在幸存者偏差或确认偏误？

### 2. 反面证据
- 列出3-5个与此论点矛盾的事实或数据
- 历史上类似的论点失败的案例
- 竞争对手/替代技术的威胁

### 3. 隐藏风险
- 这个投资最可能"死"在什么地方？
- 尾部风险：低概率但致命的情景
- 宏观/政策/技术变化的冲击

### 4. 估值审查
- 当前估值隐含了什么预期？
- 这个预期合理吗？需要什么条件才能兑现？
- 如果核心假设打折50%，估值还有安全边际吗？

### 5. 最终裁决
- 论点评分（1-10，10=无懈可击）
- 最大的1个盲点
- "如果只能做一件事来验证/推翻此论点，应该做什么？"

## 规则
- 不要客气，越尖锐越好
- 用具体数据和事实支撑每个反驳
- 最后必须给出 "kill this thesis" 的最佳路径""",

    "cross_market_scan": """你是一位全球股票筛选专家，覆盖美国/台湾/欧洲/日本/韩国/中国市场。

任务：围绕 "{theme}" 主题，扫描全球上市公司。

最低市值要求: {min_cap}

## 扫描范围

### 美国 (NYSE/NASDAQ)
列出所有相关公司，标注：代码、市值、核心业务、与主题关联度

### 台湾 (TWSE/TPEx)
列出所有相关公司，标注：代码、市值、核心业务、与主题关联度

### 欧洲 (AMS/XETRA/LSE 等)
列出所有相关公司，标注：代码、市值、核心业务、与主题关联度

### 日本 (TSE)
列出所有相关公司，标注：代码、市值、核心业务、与主题关联度

### 韩国 (KRX)
列出所有相关公司，标注：代码、市值、核心业务、与主题关联度

### 中国 (SH/SZ/HK)
列出所有相关公司，标注：代码、市值、核心业务、与主题关联度

## 输出要求
- 每个公司一行：**公司名** (代码.交易所) | 市值 | 业务 | 关联度(★-★★★)
- 按关联度排序
- 标注哪些是 "隐形冠军"（知名度低但卡脖子地位强）
- 最后给出 "最佳投资候选 Top 10"，按风险收益比排序""",

    "thesis_scorecard": """你是一位投资论点评分专家，使用 Serenity 卡脖子投资框架。

评估对象：**{company}**
投资论点：**"{thesis}"**
投资周期：**{timeframe}**

## 评分维度（每项 1-10 分）

### 1. 卡脖子强度 (Chokepoint Strength)
- 市场集中度如何？是寡头还是垄断？
- 客户切换成本多高？
- 技术壁垒是否经过时间验证？
- 有没有正在追赶的竞争对手？
**评分：?/10 — 理由：...**

### 2. 非共识度 (Non-Consensus Level)
- 这个论点是华尔街主流观点还是少数派？
- 卖方分析师覆盖了吗？买方已经重仓了吗？
- 散户认知度如何？
- 如果这是共识——alpha 从哪来？
**评分：?/10 — 理由：...**

### 3. 时间差 (Time Gap)
- 市场定价了几成的预期？（0%=完全未定价, 100%=充分定价）
- 从现在到充分定价还需要多久？
- 有没有明确的催化剂来关闭时间差？
**评分：?/10 — 理由：...**

### 4. 催化剂清晰度 (Catalyst Clarity)
- 催化剂是什么？（财报、产品发布、政策、客户签约）
- 催化剂的时间窗口明确吗？
- 催化剂是 "必然发生" 还是 "可能发生"？
**评分：?/10 — 理由：...**

## 综合评估

| 维度 | 分数 | 权重 |
|------|------|------|
| 卡脖子强度 | ?/10 | 30% |
| 非共识度 | ?/10 | 25% |
| 时间差 | ?/10 | 25% |
| 催化剂清晰度 | ?/10 | 20% |
| **加权总分** | **?/10** | |

## 最终判断
- **操作建议**：强烈买入 / 买入 / 观察 / 回避
- **核心风险**：一句话总结最大风险
- **验证清单**：3件事可以进一步验证此论点""",
}


class SerenitySkills:
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

    def _skill_map_supply_chain(self, product: str, depth: str = "deep") -> str:
        depth_instruction = (
            "逐层展开每个环节，列出所有关键公司（至少覆盖4层供应链）。"
            if depth == "deep"
            else "只列出最关键的3层和卡脖子环节。"
        )
        prompt = _SKILL_PROMPTS["map_supply_chain"].format(
            product=product, depth_instruction=depth_instruction
        )
        return self._call_claude(prompt, f"请分析 {product} 的逆向供应链。")

    def _skill_find_chokepoints(self, industry: str, region: str = "all") -> str:
        region_map = {
            "all": "全球所有市场",
            "us": "仅美国市场",
            "tw": "仅台湾市场",
            "eu": "仅欧洲市场",
            "jp": "仅日本市场",
            "kr": "仅韩国市场",
            "cn": "仅中国市场",
        }
        region_text = region_map.get(region, "全球所有市场")
        prompt = _SKILL_PROMPTS["find_chokepoints"].format(
            industry=industry, region=region_text
        )
        return self._call_claude(prompt, f"请找出 {industry} 的卡脖子公司。")

    def _skill_geopolitical_impact(
        self, event: str, supply_chains: list[str] | None = None
    ) -> str:
        chains_context = ""
        if supply_chains:
            chains_context = f"重点关注以下供应链: {', '.join(supply_chains)}"
        prompt = _SKILL_PROMPTS["geopolitical_impact"].format(
            event=event, chains_context=chains_context
        )
        return self._call_claude(prompt, f"请分析此事件的供应链影响: {event}")

    def _skill_challenge_thesis(self, thesis: str, position: str) -> str:
        pos_text = "看多(做多)" if position == "long" else "看空(做空)"
        prompt = _SKILL_PROMPTS["challenge_thesis"].format(
            thesis=thesis, position=pos_text
        )
        return self._call_claude(prompt, f"请挑战此论点: {thesis}")

    def _skill_cross_market_scan(
        self, theme: str, min_market_cap_usd: str = "1B"
    ) -> str:
        prompt = _SKILL_PROMPTS["cross_market_scan"].format(
            theme=theme, min_cap=min_market_cap_usd
        )
        return self._call_claude(prompt, f"请扫描 {theme} 主题的全球上市公司。")

    def _skill_thesis_scorecard(
        self, company: str, thesis: str, timeframe: str = "medium"
    ) -> str:
        tf_map = {
            "short": "短期 (1-3个月)",
            "medium": "中期 (3-12个月)",
            "long": "长期 (1-3年)",
        }
        tf_text = tf_map.get(timeframe, "中期 (3-12个月)")
        prompt = _SKILL_PROMPTS["thesis_scorecard"].format(
            company=company, thesis=thesis, timeframe=tf_text
        )
        return self._call_claude(
            prompt, f"请评估 {company} 的投资论点: {thesis}"
        )
