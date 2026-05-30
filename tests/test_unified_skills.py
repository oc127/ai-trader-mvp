"""Tests for Unified (Serenity × RICH) trading pipeline skills."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.agent.skills.unified import (
    _SYNTHESIS_PROMPTS,
    UNIFIED_SKILL_DEFINITIONS,
    UnifiedSkills,
)


def test_all_skills_have_definitions():
    names = {d["name"] for d in UNIFIED_SKILL_DEFINITIONS}
    expected = {"full_analysis", "entry_plan", "position_check"}
    assert names == expected


def test_all_skills_have_prompts():
    for defn in UNIFIED_SKILL_DEFINITIONS:
        assert defn["name"] in _SYNTHESIS_PROMPTS, f"Missing prompt for {defn['name']}"


def test_all_skills_have_handlers():
    skills = UnifiedSkills.__new__(UnifiedSkills)
    for defn in UNIFIED_SKILL_DEFINITIONS:
        handler = getattr(skills, f"_skill_{defn['name']}", None)
        assert handler is not None, f"Missing handler for {defn['name']}"


def test_definitions_have_required_fields():
    for defn in UNIFIED_SKILL_DEFINITIONS:
        assert "name" in defn
        assert "description" in defn
        assert "input_schema" in defn
        schema = defn["input_schema"]
        assert schema["type"] == "object"
        assert "properties" in schema
        assert "required" in schema


def test_execute_unknown_skill():
    skills = UnifiedSkills.__new__(UnifiedSkills)
    result = skills.execute("nonexistent_skill", {})
    assert "error" in result
    assert "Unknown skill" in result


def test_no_name_collisions():
    from src.agent.skills.rich import RICH_SKILL_DEFINITIONS
    from src.agent.skills.serenity import SERENITY_SKILL_DEFINITIONS

    unified_names = {d["name"] for d in UNIFIED_SKILL_DEFINITIONS}
    serenity_names = {d["name"] for d in SERENITY_SKILL_DEFINITIONS}
    rich_names = {d["name"] for d in RICH_SKILL_DEFINITIONS}
    trading_names = {
        "get_account", "get_price", "get_orderbook", "scan_pairs",
        "place_order", "market_order", "cancel_orders", "close_position",
        "get_open_orders", "set_leverage", "get_funding_rates",
    }
    assert unified_names.isdisjoint(serenity_names)
    assert unified_names.isdisjoint(rich_names)
    assert unified_names.isdisjoint(trading_names)


def test_prompts_contain_framework_references():
    assert "Serenity" in _SYNTHESIS_PROMPTS["full_analysis"]
    assert "RICH" in _SYNTHESIS_PROMPTS["full_analysis"]
    assert "四象限" in _SYNTHESIS_PROMPTS["full_analysis"]
    assert "Fibonacci" in _SYNTHESIS_PROMPTS["entry_plan"]
    assert "2:1" in _SYNTHESIS_PROMPTS["entry_plan"]
    assert "OBIB" in _SYNTHESIS_PROMPTS["entry_plan"]
    assert "论点" in _SYNTHESIS_PROMPTS["position_check"]
    assert "技术" in _SYNTHESIS_PROMPTS["position_check"]
    assert "加仓" in _SYNTHESIS_PROMPTS["position_check"]
    assert "清仓" in _SYNTHESIS_PROMPTS["position_check"]


@patch("src.agent.skills.unified.anthropic.Anthropic")
def test_full_analysis_chains_both_frameworks(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="unified decision")]
    mock_client.messages.create.return_value = mock_response
    mock_anthropic_cls.return_value = mock_client

    skills = UnifiedSkills()
    skills._serenity = MagicMock()
    skills._serenity.execute.return_value = "serenity scorecard result"
    skills._rich = MagicMock()
    skills._rich.execute.return_value = "rich scorecard result"

    result = skills.execute("full_analysis", {
        "company": "SIVE",
        "thesis": "CPO激光chokepoint",
        "direction": "long",
    })

    assert result == "unified decision"
    skills._serenity.execute.assert_called_once_with(
        "thesis_scorecard",
        {"company": "SIVE", "thesis": "CPO激光chokepoint", "timeframe": "medium"},
    )
    skills._rich.execute.assert_called_once_with(
        "rich_scorecard",
        {"symbol": "SIVE", "direction": "long"},
    )
    mock_client.messages.create.assert_called_once()
    synth_kwargs = mock_client.messages.create.call_args[1]
    assert "serenity scorecard result" in synth_kwargs["system"]
    assert "rich scorecard result" in synth_kwargs["system"]


@patch("src.agent.skills.unified.anthropic.Anthropic")
def test_entry_plan_chains_rich_skills(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="entry plan")]
    mock_client.messages.create.return_value = mock_response
    mock_anthropic_cls.return_value = mock_client

    skills = UnifiedSkills()
    skills._rich = MagicMock()
    skills._rich.execute.side_effect = [
        "drill down result",
        "fib analysis result",
    ]

    result = skills.execute("entry_plan", {
        "symbol": "NVDA",
        "direction": "long",
        "capital": 50000,
    })

    assert result == "entry plan"
    assert skills._rich.execute.call_count == 2
    calls = skills._rich.execute.call_args_list
    assert calls[0][0][0] == "drill_down"
    assert calls[1][0][0] == "fib_analysis"
    synth_kwargs = mock_client.messages.create.call_args[1]
    assert "$50,000" in synth_kwargs["system"]
    assert "drill down result" in synth_kwargs["system"]
    assert "fib analysis result" in synth_kwargs["system"]


@patch("src.agent.skills.unified.anthropic.Anthropic")
def test_position_check_chains_both(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="hold position")]
    mock_client.messages.create.return_value = mock_response
    mock_anthropic_cls.return_value = mock_client

    skills = UnifiedSkills()
    skills._serenity = MagicMock()
    skills._serenity.execute.return_value = "thesis still valid"
    skills._rich = MagicMock()
    skills._rich.execute.return_value = "technical ok"

    result = skills.execute("position_check", {
        "symbol": "BESI.AS",
        "thesis": "先进封装卡脖子",
        "entry_price": 120.0,
        "direction": "long",
    })

    assert result == "hold position"
    skills._serenity.execute.assert_called_once_with(
        "challenge_thesis",
        {"thesis": "先进封装卡脖子", "position": "long"},
    )
    skills._rich.execute.assert_called_once_with(
        "drill_down",
        {"symbol": "BESI.AS", "bias": "long"},
    )
    synth_kwargs = mock_client.messages.create.call_args[1]
    assert "$120.0" in synth_kwargs["system"]
    assert "thesis still valid" in synth_kwargs["system"]
    assert "technical ok" in synth_kwargs["system"]


@patch("src.agent.skills.unified.anthropic.Anthropic")
def test_entry_plan_with_style(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="theta plan")]
    mock_client.messages.create.return_value = mock_response
    mock_anthropic_cls.return_value = mock_client

    skills = UnifiedSkills()
    skills._rich = MagicMock()
    skills._rich.execute.side_effect = ["drill", "fib"]

    result = skills.execute("entry_plan", {
        "symbol": "AAPL",
        "direction": "long",
        "style": "theta",
    })

    assert result == "theta plan"
    synth_kwargs = mock_client.messages.create.call_args[1]
    assert "Theta" in synth_kwargs["system"]


@patch("src.agent.skills.unified.anthropic.Anthropic")
def test_full_analysis_with_capital(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="decision")]
    mock_client.messages.create.return_value = mock_response
    mock_anthropic_cls.return_value = mock_client

    skills = UnifiedSkills()
    skills._serenity = MagicMock()
    skills._serenity.execute.return_value = "serenity"
    skills._rich = MagicMock()
    skills._rich.execute.return_value = "rich"

    result = skills.execute("full_analysis", {
        "company": "SIVE",
        "thesis": "test",
        "direction": "long",
        "capital": 100000,
    })

    assert result == "decision"
    synth_kwargs = mock_client.messages.create.call_args[1]
    assert "$100,000" in synth_kwargs["system"]


@patch("src.agent.skills.unified.anthropic.Anthropic")
def test_entry_plan_short_direction(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="short plan")]
    mock_client.messages.create.return_value = mock_response
    mock_anthropic_cls.return_value = mock_client

    skills = UnifiedSkills()
    skills._rich = MagicMock()
    skills._rich.execute.side_effect = ["drill", "fib"]

    result = skills.execute("entry_plan", {
        "symbol": "TSLA",
        "direction": "short",
    })

    assert result == "short plan"
    calls = skills._rich.execute.call_args_list
    assert calls[0][0][1] == {"symbol": "TSLA", "bias": "short"}
    assert calls[1][0][1]["trend"] == "down"
    synth_kwargs = mock_client.messages.create.call_args[1]
    assert "做空" in synth_kwargs["system"]
