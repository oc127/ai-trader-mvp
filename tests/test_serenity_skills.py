"""Tests for Serenity chokepoint research skills."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.agent.skills.serenity import _SKILL_PROMPTS, SERENITY_SKILL_DEFINITIONS, SerenitySkills


def test_all_skills_have_definitions():
    names = {d["name"] for d in SERENITY_SKILL_DEFINITIONS}
    expected = {
        "map_supply_chain",
        "find_chokepoints",
        "design_in_detective",
        "capital_catalyst_stack",
        "geopolitical_impact",
        "challenge_thesis",
        "cross_market_scan",
        "thesis_scorecard",
    }
    assert names == expected


def test_all_skills_have_prompts():
    for defn in SERENITY_SKILL_DEFINITIONS:
        assert defn["name"] in _SKILL_PROMPTS, f"Missing prompt for {defn['name']}"


def test_all_skills_have_handlers():
    skills = SerenitySkills.__new__(SerenitySkills)
    for defn in SERENITY_SKILL_DEFINITIONS:
        handler = getattr(skills, f"_skill_{defn['name']}", None)
        assert handler is not None, f"Missing handler for {defn['name']}"


def test_definitions_have_required_fields():
    for defn in SERENITY_SKILL_DEFINITIONS:
        assert "name" in defn
        assert "description" in defn
        assert "input_schema" in defn
        schema = defn["input_schema"]
        assert schema["type"] == "object"
        assert "properties" in schema
        assert "required" in schema


def test_execute_unknown_skill():
    skills = SerenitySkills.__new__(SerenitySkills)
    result = skills.execute("nonexistent_skill", {})
    assert "error" in result
    assert "Unknown skill" in result


def test_prompts_contain_serenity_philosophy():
    assert "被迫购买" in _SKILL_PROMPTS["map_supply_chain"]
    assert "物理瓶颈" in _SKILL_PROMPTS["map_supply_chain"]
    assert "最窄的口子" in _SKILL_PROMPTS["find_chokepoints"]
    assert "Kingmaker" in _SKILL_PROMPTS["find_chokepoints"]
    assert "量产倒计时" in _SKILL_PROMPTS["design_in_detective"]
    assert "官方确认前" in _SKILL_PROMPTS["design_in_detective"]
    assert "指数纳入" in _SKILL_PROMPTS["capital_catalyst_stack"]
    assert "双重上市" in _SKILL_PROMPTS["capital_catalyst_stack"]
    assert "LITE playbook" in _SKILL_PROMPTS["capital_catalyst_stack"]
    assert "信息分层" in _SKILL_PROMPTS["challenge_thesis"]
    assert "公开确认" in _SKILL_PROMPTS["challenge_thesis"]
    assert "地理套利" in _SKILL_PROMPTS["cross_market_scan"]
    assert "量产信号" in _SKILL_PROMPTS["thesis_scorecard"]
    assert "资本催化" in _SKILL_PROMPTS["thesis_scorecard"]
    assert "风险诚实度" in _SKILL_PROMPTS["thesis_scorecard"]
    assert "证据分级标准" in _SKILL_PROMPTS["thesis_scorecard"]
    assert "Strong" in _SKILL_PROMPTS["thesis_scorecard"]
    assert "Weak" in _SKILL_PROMPTS["thesis_scorecard"]
    assert "惩罚因子" in _SKILL_PROMPTS["thesis_scorecard"]
    assert "稀释" in _SKILL_PROMPTS["thesis_scorecard"]
    assert "替代设计风险" in _SKILL_PROMPTS["thesis_scorecard"]
    assert "最终得分" in _SKILL_PROMPTS["thesis_scorecard"]


@patch("src.agent.skills.serenity.anthropic.Anthropic")
def test_map_supply_chain_calls_claude(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="supply chain analysis result")]
    mock_client.messages.create.return_value = mock_response
    mock_anthropic_cls.return_value = mock_client

    skills = SerenitySkills()
    result = skills.execute("map_supply_chain", {"product": "CPO光互连"})

    assert result == "supply chain analysis result"
    mock_client.messages.create.assert_called_once()
    call_kwargs = mock_client.messages.create.call_args[1]
    assert "CPO光互连" in call_kwargs["system"]


@patch("src.agent.skills.serenity.anthropic.Anthropic")
def test_design_in_detective_calls_claude(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="pre-revenue signals")]
    mock_client.messages.create.return_value = mock_response
    mock_anthropic_cls.return_value = mock_client

    skills = SerenitySkills()
    result = skills.execute(
        "design_in_detective",
        {"company": "SIVE", "focus": "CW DFB lasers"},
    )

    assert result == "pre-revenue signals"
    call_kwargs = mock_client.messages.create.call_args[1]
    assert "SIVE" in call_kwargs["system"]
    assert "CW DFB lasers" in call_kwargs["system"]


@patch("src.agent.skills.serenity.anthropic.Anthropic")
def test_capital_catalyst_stack_calls_claude(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="catalyst analysis")]
    mock_client.messages.create.return_value = mock_response
    mock_anthropic_cls.return_value = mock_client

    skills = SerenitySkills()
    result = skills.execute(
        "capital_catalyst_stack",
        {"company": "Sivers", "current_listing": "OMX Stockholm"},
    )

    assert result == "catalyst analysis"
    call_kwargs = mock_client.messages.create.call_args[1]
    assert "Sivers" in call_kwargs["system"]
    assert "OMX Stockholm" in call_kwargs["system"]


@patch("src.agent.skills.serenity.anthropic.Anthropic")
def test_challenge_thesis_has_info_layering(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="counter arguments")]
    mock_client.messages.create.return_value = mock_response
    mock_anthropic_cls.return_value = mock_client

    skills = SerenitySkills()
    result = skills.execute(
        "challenge_thesis",
        {"thesis": "SIVE是CPO kingmaker", "position": "long"},
    )

    assert result == "counter arguments"
    call_kwargs = mock_client.messages.create.call_args[1]
    assert "信息分层" in call_kwargs["system"]
    assert "公开确认" in call_kwargs["system"]


@patch("src.agent.skills.serenity.anthropic.Anthropic")
def test_thesis_scorecard_has_six_dimensions(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="scorecard")]
    mock_client.messages.create.return_value = mock_response
    mock_anthropic_cls.return_value = mock_client

    skills = SerenitySkills()
    result = skills.execute(
        "thesis_scorecard",
        {"company": "SIVE", "thesis": "激光chokepoint"},
    )

    assert result == "scorecard"
    call_kwargs = mock_client.messages.create.call_args[1]
    assert "量产信号" in call_kwargs["system"]
    assert "资本催化" in call_kwargs["system"]
    assert "风险诚实度" in call_kwargs["system"]
    assert "25%" in call_kwargs["system"]


@patch("src.agent.skills.serenity.anthropic.Anthropic")
def test_geopolitical_impact_with_chains(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="impact analysis")]
    mock_client.messages.create.return_value = mock_response
    mock_anthropic_cls.return_value = mock_client

    skills = SerenitySkills()
    result = skills.execute(
        "geopolitical_impact",
        {
            "event": "美国对华芯片出口管制",
            "supply_chains": ["AI芯片", "先进封装"],
        },
    )

    assert result == "impact analysis"
    call_kwargs = mock_client.messages.create.call_args[1]
    assert "AI芯片" in call_kwargs["system"]


@patch("src.agent.skills.serenity.anthropic.Anthropic")
def test_cross_market_scan_has_geographic_arbitrage(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="scan results")]
    mock_client.messages.create.return_value = mock_response
    mock_anthropic_cls.return_value = mock_client

    skills = SerenitySkills()
    result = skills.execute("cross_market_scan", {"theme": "CPO激光源"})

    assert result == "scan results"
    call_kwargs = mock_client.messages.create.call_args[1]
    assert "地理套利" in call_kwargs["system"]
    assert "隐形冠军" in call_kwargs["system"]


def test_no_name_collisions_with_trading_tools():
    trading_tool_names = {
        "get_account", "get_price", "get_orderbook", "scan_pairs",
        "place_order", "market_order", "cancel_orders", "close_position",
        "get_open_orders", "set_leverage", "get_funding_rates",
    }
    skill_names = {d["name"] for d in SERENITY_SKILL_DEFINITIONS}
    assert len(skill_names) == 8
    assert skill_names.isdisjoint(trading_tool_names)
