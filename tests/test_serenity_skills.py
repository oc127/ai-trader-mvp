"""Tests for Serenity chokepoint research skills."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.agent.skills.serenity import _SKILL_PROMPTS, SERENITY_SKILL_DEFINITIONS, SerenitySkills


def test_all_skills_have_definitions():
    names = {d["name"] for d in SERENITY_SKILL_DEFINITIONS}
    expected = {
        "map_supply_chain",
        "find_chokepoints",
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


@patch("src.agent.skills.serenity.anthropic.Anthropic")
def test_map_supply_chain_calls_claude(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="supply chain analysis result")]
    mock_client.messages.create.return_value = mock_response
    mock_anthropic_cls.return_value = mock_client

    skills = SerenitySkills()
    result = skills.execute("map_supply_chain", {"product": "AI服务器"})

    assert result == "supply chain analysis result"
    mock_client.messages.create.assert_called_once()
    call_kwargs = mock_client.messages.create.call_args[1]
    assert "AI服务器" in call_kwargs["system"]
    assert call_kwargs["max_tokens"] == 4096


@patch("src.agent.skills.serenity.anthropic.Anthropic")
def test_challenge_thesis_calls_claude(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="counter arguments")]
    mock_client.messages.create.return_value = mock_response
    mock_anthropic_cls.return_value = mock_client

    skills = SerenitySkills()
    result = skills.execute(
        "challenge_thesis",
        {"thesis": "ASML不可替代", "position": "long"},
    )

    assert result == "counter arguments"
    call_kwargs = mock_client.messages.create.call_args[1]
    assert "ASML不可替代" in call_kwargs["system"]
    assert "看多" in call_kwargs["system"]


@patch("src.agent.skills.serenity.anthropic.Anthropic")
def test_thesis_scorecard_default_timeframe(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="scorecard")]
    mock_client.messages.create.return_value = mock_response
    mock_anthropic_cls.return_value = mock_client

    skills = SerenitySkills()
    result = skills.execute(
        "thesis_scorecard",
        {"company": "TSM", "thesis": "先进制程垄断"},
    )

    assert result == "scorecard"
    call_kwargs = mock_client.messages.create.call_args[1]
    assert "中期" in call_kwargs["system"]


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
    assert "先进封装" in call_kwargs["system"]


def test_no_name_collisions_with_trading_tools():
    trading_tool_names = {
        "get_account", "get_price", "get_orderbook", "scan_pairs",
        "place_order", "market_order", "cancel_orders", "close_position",
        "get_open_orders", "set_leverage", "get_funding_rates",
    }
    skill_names = {d["name"] for d in SERENITY_SKILL_DEFINITIONS}
    assert len(skill_names) == 6
    assert skill_names.isdisjoint(trading_tool_names)
