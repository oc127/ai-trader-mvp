"""Tests for RICH (TradingWarz) technical trading skills."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.agent.llm_client import LLMResponse
from src.agent.skills.rich import _SKILL_PROMPTS, RICH_SKILL_DEFINITIONS, RichSkills


def test_all_skills_have_definitions():
    names = {d["name"] for d in RICH_SKILL_DEFINITIONS}
    expected = {
        "fib_analysis",
        "drill_down",
        "risk_reward_calc",
        "leaps_setup",
        "theta_harvest",
        "rich_scorecard",
    }
    assert names == expected


def test_all_skills_have_prompts():
    for defn in RICH_SKILL_DEFINITIONS:
        assert defn["name"] in _SKILL_PROMPTS, f"Missing prompt for {defn['name']}"


def test_all_skills_have_handlers():
    skills = RichSkills.__new__(RichSkills)
    for defn in RICH_SKILL_DEFINITIONS:
        handler = getattr(skills, f"_skill_{defn['name']}", None)
        assert handler is not None, f"Missing handler for {defn['name']}"


def test_definitions_have_required_fields():
    for defn in RICH_SKILL_DEFINITIONS:
        assert "name" in defn
        assert "description" in defn
        assert "input_schema" in defn
        schema = defn["input_schema"]
        assert schema["type"] == "object"
        assert "properties" in schema
        assert "required" in schema


def test_execute_unknown_skill():
    skills = RichSkills.__new__(RichSkills)
    result = skills.execute("nonexistent_skill", {})
    assert "error" in result
    assert "Unknown skill" in result


def test_no_name_collisions_with_other_skills():
    from src.agent.skills.serenity import SERENITY_SKILL_DEFINITIONS

    rich_names = {d["name"] for d in RICH_SKILL_DEFINITIONS}
    serenity_names = {d["name"] for d in SERENITY_SKILL_DEFINITIONS}
    trading_names = {
        "get_account", "get_price", "get_orderbook", "scan_pairs",
        "place_order", "market_order", "cancel_orders", "close_position",
        "get_open_orders", "set_leverage", "get_funding_rates",
    }
    assert rich_names.isdisjoint(serenity_names)
    assert rich_names.isdisjoint(trading_names)


@patch("src.agent.skills.rich.LLMClient")
def test_fib_analysis_calls_llm(mock_llm_cls):
    mock_client = MagicMock()
    mock_client.chat.return_value = LLMResponse(text="fib analysis result")
    mock_llm_cls.return_value = mock_client

    skills = RichSkills()
    result = skills.execute("fib_analysis", {"symbol": "NVDA", "trend": "up"})

    assert result == "fib analysis result"
    mock_client.chat.assert_called_once()
    call_kwargs = mock_client.chat.call_args[1]
    assert "NVDA" in call_kwargs["system"]
    assert "Golden Zone" in call_kwargs["system"]


@patch("src.agent.skills.rich.LLMClient")
def test_drill_down_default_bias(mock_llm_cls):
    mock_client = MagicMock()
    mock_client.chat.return_value = LLMResponse(text="drill down result")
    mock_llm_cls.return_value = mock_client

    skills = RichSkills()
    result = skills.execute("drill_down", {"symbol": "AAPL"})

    assert result == "drill down result"
    call_kwargs = mock_client.chat.call_args[1]
    assert "中性" in call_kwargs["system"]


@patch("src.agent.skills.rich.LLMClient")
def test_risk_reward_calc_with_all_params(mock_llm_cls):
    mock_client = MagicMock()
    mock_client.chat.return_value = LLMResponse(text="rr calc")
    mock_llm_cls.return_value = mock_client

    skills = RichSkills()
    result = skills.execute("risk_reward_calc", {
        "symbol": "TSLA",
        "entry": 200.0,
        "stop_loss": 185.0,
        "target": 250.0,
        "capital": 10000.0,
    })

    assert result == "rr calc"
    call_kwargs = mock_client.chat.call_args[1]
    assert "$185.0" in call_kwargs["system"]
    assert "$250.0" in call_kwargs["system"]
    assert "$10,000" in call_kwargs["system"]


@patch("src.agent.skills.rich.LLMClient")
def test_risk_reward_calc_auto_levels(mock_llm_cls):
    mock_client = MagicMock()
    mock_client.chat.return_value = LLMResponse(text="auto calc")
    mock_llm_cls.return_value = mock_client

    skills = RichSkills()
    result = skills.execute("risk_reward_calc", {
        "symbol": "TSLA",
        "entry": 200.0,
    })

    assert result == "auto calc"
    call_kwargs = mock_client.chat.call_args[1]
    assert "自动计算" in call_kwargs["system"]


@patch("src.agent.skills.rich.LLMClient")
def test_rich_scorecard_calls_llm(mock_llm_cls):
    mock_client = MagicMock()
    mock_client.chat.return_value = LLMResponse(text="scorecard")
    mock_llm_cls.return_value = mock_client

    skills = RichSkills()
    result = skills.execute("rich_scorecard", {
        "symbol": "BESI.AS",
        "direction": "long",
    })

    assert result == "scorecard"
    call_kwargs = mock_client.chat.call_args[1]
    assert "BESI.AS" in call_kwargs["system"]
    assert "做多" in call_kwargs["system"]
    assert "一票否决" in call_kwargs["system"]


@patch("src.agent.skills.rich.LLMClient")
def test_theta_harvest_defaults(mock_llm_cls):
    mock_client = MagicMock()
    mock_client.chat.return_value = LLMResponse(text="theta result")
    mock_llm_cls.return_value = mock_client

    skills = RichSkills()
    result = skills.execute("theta_harvest", {"symbol": "AAPL"})

    assert result == "theta result"
    call_kwargs = mock_client.chat.call_args[1]
    assert "自动推荐" in call_kwargs["system"]
    assert "适中" in call_kwargs["system"]
