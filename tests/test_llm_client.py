"""Tests for the multi-provider LLM client."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from src.agent.llm_client import (
    LLMClient,
    LLMResponse,
    ToolCall,
    _convert_messages_to_openai,
    _convert_tools_to_openai,
)


def test_convert_tools_to_openai():
    anthropic_tools = [
        {
            "name": "get_price",
            "description": "Get asset price",
            "input_schema": {
                "type": "object",
                "properties": {"symbol": {"type": "string"}},
                "required": ["symbol"],
            },
        },
    ]
    result = _convert_tools_to_openai(anthropic_tools)
    assert len(result) == 1
    assert result[0]["type"] == "function"
    assert result[0]["function"]["name"] == "get_price"
    assert result[0]["function"]["description"] == "Get asset price"
    assert result[0]["function"]["parameters"]["type"] == "object"
    assert "symbol" in result[0]["function"]["parameters"]["properties"]


def test_convert_messages_basic():
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]
    result = _convert_messages_to_openai("system prompt", messages)
    assert result[0] == {"role": "system", "content": "system prompt"}
    assert result[1] == {"role": "user", "content": "hello"}
    assert result[2] == {"role": "assistant", "content": "hi there"}


def test_convert_messages_with_tool_use():
    messages = [
        {"role": "user", "content": "get BTC price"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Let me check."},
                {
                    "type": "tool_use",
                    "id": "call_123",
                    "name": "get_price",
                    "input": {"symbol": "BTC"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call_123", "content": "$50000"},
            ],
        },
    ]
    result = _convert_messages_to_openai("sys", messages)
    assert result[0]["role"] == "system"
    assert result[1]["role"] == "user"
    assert result[2]["role"] == "assistant"
    assert result[2]["content"] == "Let me check."
    assert len(result[2]["tool_calls"]) == 1
    tc = result[2]["tool_calls"][0]
    assert tc["id"] == "call_123"
    assert tc["function"]["name"] == "get_price"
    assert json.loads(tc["function"]["arguments"]) == {"symbol": "BTC"}
    assert result[3]["role"] == "tool"
    assert result[3]["tool_call_id"] == "call_123"
    assert result[3]["content"] == "$50000"


def test_provider_defaults_anthropic():
    client = LLMClient(provider="anthropic")
    assert client.provider == "anthropic"
    assert client.model == "claude-sonnet-4-20250514"


def test_provider_defaults_openai():
    client = LLMClient(provider="openai", api_key="sk-test")
    assert client.provider == "openai"
    assert client.model == "gpt-4o"


def test_provider_defaults_deepseek():
    client = LLMClient(provider="deepseek", api_key="sk-test")
    assert client.model == "deepseek-chat"


def test_model_override():
    client = LLMClient(provider="openai", api_key="sk-test", model="gpt-4o-mini")
    assert client.model == "gpt-4o-mini"


@patch.dict("os.environ", {"LLM_PROVIDER": "openai", "LLM_MODEL": "gpt-4o-mini"})
def test_env_var_config():
    client = LLMClient(api_key="sk-test")
    assert client.provider == "openai"
    assert client.model == "gpt-4o-mini"


def test_chat_anthropic_routes_correctly():
    mock_sdk_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(type="text", text="hello")]
    mock_response.stop_reason = "end_turn"
    mock_sdk_client.messages.create.return_value = mock_response

    client = LLMClient(provider="anthropic")
    client._sdk_client = mock_sdk_client
    resp = client.chat(system="sys", messages=[{"role": "user", "content": "hi"}])

    assert isinstance(resp, LLMResponse)
    assert resp.text == "hello"
    assert resp.stop_reason == "end"
    assert resp.tool_calls == []
    mock_sdk_client.messages.create.assert_called_once()


def test_chat_openai_routes_correctly():
    mock_sdk_client = MagicMock()
    mock_message = MagicMock()
    mock_message.content = "hello from gpt"
    mock_message.tool_calls = None
    mock_choice = MagicMock()
    mock_choice.message = mock_message
    mock_choice.finish_reason = "stop"
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_sdk_client.chat.completions.create.return_value = mock_response

    client = LLMClient(provider="openai", api_key="sk-test")
    client._sdk_client = mock_sdk_client
    resp = client.chat(system="sys", messages=[{"role": "user", "content": "hi"}])

    assert resp.text == "hello from gpt"
    assert resp.stop_reason == "end"
    mock_sdk_client.chat.completions.create.assert_called_once()


def test_anthropic_tool_use_response():
    mock_sdk_client = MagicMock()
    text_block = MagicMock(type="text", text="Checking price...")
    tool_block = MagicMock(type="tool_use", id="call_1", input={"symbol": "BTC"})
    tool_block.name = "get_price"
    mock_response = MagicMock()
    mock_response.content = [text_block, tool_block]
    mock_response.stop_reason = "tool_use"
    mock_sdk_client.messages.create.return_value = mock_response

    client = LLMClient(provider="anthropic")
    client._sdk_client = mock_sdk_client
    resp = client.chat(
        system="sys",
        messages=[{"role": "user", "content": "price?"}],
        tools=[{"name": "get_price", "description": "x", "input_schema": {"type": "object", "properties": {}}}],
    )

    assert resp.stop_reason == "tool_use"
    assert resp.text == "Checking price..."
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "get_price"
    assert resp.tool_calls[0].arguments == {"symbol": "BTC"}
    assert len(resp.content) == 2
    assert resp.content[0] == {"type": "text", "text": "Checking price..."}
    assert resp.content[1]["type"] == "tool_use"
    assert resp.content[1]["name"] == "get_price"


def test_openai_tool_use_response():
    mock_sdk_client = MagicMock()
    mock_tc = MagicMock()
    mock_tc.id = "call_1"
    mock_tc.function.name = "get_price"
    mock_tc.function.arguments = '{"symbol": "BTC"}'
    mock_message = MagicMock()
    mock_message.content = None
    mock_message.tool_calls = [mock_tc]
    mock_choice = MagicMock()
    mock_choice.message = mock_message
    mock_choice.finish_reason = "tool_calls"
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_sdk_client.chat.completions.create.return_value = mock_response

    client = LLMClient(provider="openai", api_key="sk-test")
    client._sdk_client = mock_sdk_client
    resp = client.chat(
        system="sys",
        messages=[{"role": "user", "content": "price?"}],
        tools=[{"name": "get_price", "description": "x", "input_schema": {"type": "object", "properties": {}}}],
    )

    assert resp.stop_reason == "tool_use"
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "get_price"
    assert resp.tool_calls[0].arguments == {"symbol": "BTC"}
    assert resp.content[0]["type"] == "tool_use"

    call_kwargs = mock_sdk_client.chat.completions.create.call_args[1]
    assert call_kwargs["tools"][0]["type"] == "function"
    assert call_kwargs["tools"][0]["function"]["name"] == "get_price"


def test_llm_response_defaults():
    resp = LLMResponse()
    assert resp.text is None
    assert resp.tool_calls == []
    assert resp.stop_reason == "end"
    assert resp.content == []


def test_tool_call_dataclass():
    tc = ToolCall(id="1", name="test", arguments={"a": 1})
    assert tc.id == "1"
    assert tc.name == "test"
    assert tc.arguments == {"a": 1}
