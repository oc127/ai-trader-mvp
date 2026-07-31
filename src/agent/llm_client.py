"""Thin LLM provider abstraction.

Supports Anthropic (native SDK) and any OpenAI-compatible endpoint
(OpenAI, DeepSeek, OpenRouter, Ollama, etc.) through the OpenAI SDK.

Provider selection via environment variables:
    LLM_PROVIDER  — anthropic / openai / deepseek / openrouter / custom
    LLM_API_KEY   — API key for the chosen provider
    LLM_MODEL     — model name (defaults per provider)
    LLM_BASE_URL  — custom endpoint URL (required for 'custom' provider)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "end"
    content: list[dict] = field(default_factory=list)


_PROVIDER_DEFAULTS: dict[str, dict[str, str]] = {
    "anthropic": {"model": "claude-sonnet-4-20250514"},
    "openai": {"model": "gpt-4o", "base_url": "https://api.openai.com/v1"},
    "deepseek": {"model": "deepseek-chat", "base_url": "https://api.deepseek.com"},
    "openrouter": {
        "model": "anthropic/claude-sonnet-4",
        "base_url": "https://openrouter.ai/api/v1",
    },
}


def _convert_tools_to_openai(tools: list[dict]) -> list[dict]:
    """Convert Anthropic-format tool definitions to OpenAI function-calling format."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
            },
        }
        for t in tools
    ]


def _convert_messages_to_openai(system: str, messages: list[dict]) -> list[dict]:
    """Convert Anthropic-format message history to OpenAI chat format."""
    result: list[dict] = [{"role": "system", "content": system}]
    for msg in messages:
        role = msg["role"]
        content = msg["content"]

        if role == "user":
            if isinstance(content, str):
                result.append({"role": "user", "content": content})
            elif isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "tool_result":
                        result.append({
                            "role": "tool",
                            "tool_call_id": item["tool_use_id"],
                            "content": item.get("content", ""),
                        })
        elif role == "assistant":
            if isinstance(content, str):
                result.append({"role": "assistant", "content": content})
            elif isinstance(content, list):
                text_parts: list[str] = []
                oai_tool_calls: list[dict] = []
                for block in content:
                    if isinstance(block, dict):
                        btype = block.get("type")
                    else:
                        btype = getattr(block, "type", None)
                    if btype == "text":
                        text_parts.append(block["text"] if isinstance(block, dict) else block.text)
                    elif btype == "tool_use":
                        if isinstance(block, dict):
                            bid, bname, binput = block["id"], block["name"], block["input"]
                        else:
                            bid, bname, binput = block.id, block.name, block.input
                        oai_tool_calls.append({
                            "id": bid,
                            "type": "function",
                            "function": {
                                "name": bname,
                                "arguments": json.dumps(binput, ensure_ascii=False),
                            },
                        })
                oai_msg: dict = {"role": "assistant", "content": "\n".join(text_parts) or None}
                if oai_tool_calls:
                    oai_msg["tool_calls"] = oai_tool_calls
                result.append(oai_msg)
    return result


class LLMClient:
    """Unified LLM client supporting Anthropic and OpenAI-compatible providers."""

    def __init__(
        self,
        provider: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self._provider = provider or os.getenv("LLM_PROVIDER", "anthropic")
        self._api_key = api_key or os.getenv("LLM_API_KEY")
        defaults = _PROVIDER_DEFAULTS.get(self._provider, {})
        self._model = model or os.getenv("LLM_MODEL") or defaults.get("model", "gpt-4o")
        self._base_url = base_url or os.getenv("LLM_BASE_URL") or defaults.get("base_url")
        self._sdk_client = None

    @property
    def model(self) -> str:
        return self._model

    @property
    def provider(self) -> str:
        return self._provider

    def _get_client(self):
        if self._sdk_client is not None:
            return self._sdk_client
        if self._provider == "anthropic":
            import anthropic

            kwargs = {"api_key": self._api_key} if self._api_key else {}
            self._sdk_client = anthropic.Anthropic(**kwargs)
        else:
            import openai

            kwargs: dict = {}
            if self._api_key:
                kwargs["api_key"] = self._api_key
            if self._base_url:
                kwargs["base_url"] = self._base_url
            self._sdk_client = openai.OpenAI(**kwargs)
        return self._sdk_client

    def chat(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        if self._provider == "anthropic":
            return self._chat_anthropic(system, messages, tools, max_tokens)
        return self._chat_openai(system, messages, tools, max_tokens)

    def _chat_anthropic(self, system, messages, tools, max_tokens) -> LLMResponse:
        client = self._get_client()
        kwargs: dict = {
            "model": self._model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
        response = client.messages.create(**kwargs)

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        content_blocks: list[dict] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
                content_blocks.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name, arguments=block.input))
                content_blocks.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })

        return LLMResponse(
            text="\n".join(text_parts) if text_parts else None,
            tool_calls=tool_calls,
            stop_reason="tool_use" if response.stop_reason == "tool_use" else "end",
            content=content_blocks,
        )

    def _chat_openai(self, system, messages, tools, max_tokens) -> LLMResponse:
        client = self._get_client()
        oai_messages = _convert_messages_to_openai(system, messages)
        kwargs: dict = {
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": oai_messages,
        }
        if tools:
            kwargs["tools"] = _convert_tools_to_openai(tools)
        response = client.chat.completions.create(**kwargs)

        choice = response.choices[0]
        message = choice.message
        text = message.content
        tool_calls: list[ToolCall] = []
        content_blocks: list[dict] = []

        if text:
            content_blocks.append({"type": "text", "text": text})
        if message.tool_calls:
            for tc in message.tool_calls:
                args = json.loads(tc.function.arguments)
                tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))
                content_blocks.append({
                    "type": "tool_use",
                    "id": tc.id,
                    "name": tc.function.name,
                    "input": args,
                })

        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            stop_reason="tool_use" if choice.finish_reason == "tool_calls" else "end",
            content=content_blocks,
        )
