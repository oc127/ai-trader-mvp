"""Tests for Berkshire four-masters value investing skills."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.agent.llm_client import LLMResponse
from src.agent.skills.berkshire import _SKILL_PROMPTS, BERKSHIRE_SKILL_DEFINITIONS, BerkshireSkills


def test_all_skills_have_definitions():
    names = {d["name"] for d in BERKSHIRE_SKILL_DEFINITIONS}
    expected = {
        "value_checklist",
        "four_masters",
        "quality_screen",
        "devils_advocate",
    }
    assert names == expected


def test_all_skills_have_prompts():
    for defn in BERKSHIRE_SKILL_DEFINITIONS:
        assert defn["name"] in _SKILL_PROMPTS, f"Missing prompt for {defn['name']}"


def test_all_skills_have_handlers():
    skills = BerkshireSkills.__new__(BerkshireSkills)
    for defn in BERKSHIRE_SKILL_DEFINITIONS:
        handler = getattr(skills, f"_skill_{defn['name']}", None)
        assert handler is not None, f"Missing handler for {defn['name']}"


def test_definitions_have_required_fields():
    for defn in BERKSHIRE_SKILL_DEFINITIONS:
        assert "name" in defn
        assert "description" in defn
        assert "input_schema" in defn
        schema = defn["input_schema"]
        assert schema["type"] == "object"
        assert "properties" in schema
        assert "required" in schema


def test_execute_unknown_skill():
    skills = BerkshireSkills.__new__(BerkshireSkills)
    result = skills.execute("nonexistent_skill", {})
    assert "error" in result
    assert "Unknown skill" in result


def test_prompts_contain_value_investing_philosophy():
    assert "能力圈" in _SKILL_PROMPTS["value_checklist"]
    assert "护城河" in _SKILL_PROMPTS["value_checklist"]
    assert "安全边际" in _SKILL_PROMPTS["value_checklist"]
    assert "镜子测试" in _SKILL_PROMPTS["value_checklist"]

    assert "段永平" in _SKILL_PROMPTS["four_masters"]
    assert "巴菲特" in _SKILL_PROMPTS["four_masters"]
    assert "芒格" in _SKILL_PROMPTS["four_masters"]
    assert "李录" in _SKILL_PROMPTS["four_masters"]

    assert "ROE" in _SKILL_PROMPTS["quality_screen"]
    assert "自由现金流" in _SKILL_PROMPTS["quality_screen"]
    assert "毛利率" in _SKILL_PROMPTS["quality_screen"]
    assert "豁免" in _SKILL_PROMPTS["quality_screen"]

    assert "失败" in _SKILL_PROMPTS["devils_advocate"]
    assert "反过来想" in _SKILL_PROMPTS["devils_advocate"] or "逆向" in _SKILL_PROMPTS["devils_advocate"]


@patch("src.agent.skills.berkshire.LLMClient")
def test_value_checklist_calls_llm(mock_llm_cls):
    mock_client = MagicMock()
    mock_client.chat.return_value = LLMResponse(text="checklist result")
    mock_llm_cls.return_value = mock_client

    skills = BerkshireSkills()
    result = skills.execute("value_checklist", {"company": "腾讯"})

    assert result == "checklist result"
    mock_client.chat.assert_called_once()
    call_kwargs = mock_client.chat.call_args[1]
    assert "腾讯" in call_kwargs["system"]


@patch("src.agent.skills.berkshire.LLMClient")
def test_value_checklist_with_market(mock_llm_cls):
    mock_client = MagicMock()
    mock_client.chat.return_value = LLMResponse(text="hk checklist")
    mock_llm_cls.return_value = mock_client

    skills = BerkshireSkills()
    result = skills.execute("value_checklist", {"company": "腾讯", "market": "HK"})

    assert result == "hk checklist"
    call_kwargs = mock_client.chat.call_args[1]
    assert "香港" in call_kwargs["system"]


@patch("src.agent.skills.berkshire.LLMClient")
def test_four_masters_calls_llm(mock_llm_cls):
    mock_client = MagicMock()
    mock_client.chat.return_value = LLMResponse(text="four masters analysis")
    mock_llm_cls.return_value = mock_client

    skills = BerkshireSkills()
    result = skills.execute("four_masters", {"company": "拼多多"})

    assert result == "four masters analysis"
    call_kwargs = mock_client.chat.call_args[1]
    assert "拼多多" in call_kwargs["system"]


@patch("src.agent.skills.berkshire.LLMClient")
def test_quality_screen_calls_llm(mock_llm_cls):
    mock_client = MagicMock()
    mock_client.chat.return_value = LLMResponse(text="screening results")
    mock_llm_cls.return_value = mock_client

    skills = BerkshireSkills()
    result = skills.execute(
        "quality_screen",
        {"companies": "腾讯,美团,拼多多"},
    )

    assert result == "screening results"
    call_kwargs = mock_client.chat.call_args[1]
    assert "腾讯" in call_kwargs["system"]


@patch("src.agent.skills.berkshire.LLMClient")
def test_devils_advocate_calls_llm(mock_llm_cls):
    mock_client = MagicMock()
    mock_client.chat.return_value = LLMResponse(text="bear case analysis")
    mock_llm_cls.return_value = mock_client

    skills = BerkshireSkills()
    result = skills.execute(
        "devils_advocate",
        {"company": "NVDA", "bull_thesis": "AI算力需求持续爆发"},
    )

    assert result == "bear case analysis"
    call_kwargs = mock_client.chat.call_args[1]
    assert "NVDA" in call_kwargs["system"]
    assert "AI算力需求持续爆发" in call_kwargs["system"]


def test_no_name_collisions():
    from src.agent.skills.serenity import SERENITY_SKILL_DEFINITIONS
    from src.agent.skills.rich import RICH_SKILL_DEFINITIONS
    from src.agent.skills.unified import UNIFIED_SKILL_DEFINITIONS
    from src.agent.tools import TOOL_DEFINITIONS

    berkshire_names = {d["name"] for d in BERKSHIRE_SKILL_DEFINITIONS}
    other_names = (
        {d["name"] for d in SERENITY_SKILL_DEFINITIONS}
        | {d["name"] for d in RICH_SKILL_DEFINITIONS}
        | {d["name"] for d in UNIFIED_SKILL_DEFINITIONS}
        | {d["name"] for d in TOOL_DEFINITIONS}
    )
    assert berkshire_names.isdisjoint(other_names), (
        f"Name collision: {berkshire_names & other_names}"
    )
