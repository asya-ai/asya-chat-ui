import logging
from typing import Any

from anthropic import AsyncAnthropic

from app.core.config import settings
from app.services.providers.base import (
    ChatResponse,
    ChatStreamChunk,
    ChatToolCall,
    ChatToolSpec,
    ChatUsage,
)

DEFAULT_MAX_TOKENS = 1024


def _extract_system(messages: list[dict]) -> str | None:
    parts: list[str] = []
    for message in messages:
        if message.get("role") != "system":
            continue
        content = message.get("content")
        if isinstance(content, str) and content:
            parts.append(content)
    return "\n\n".join(parts) if parts else None


def _text_blocks_from_content(content: Any) -> list[dict]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        blocks: list[dict] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text" and item.get("text"):
                blocks.append({"type": "text", "text": item.get("text")})
            if item.get("type") == "image_url":
                raise ValueError("Images are not supported for Anthropic provider.")
        return blocks
    return []


def _to_anthropic_messages(messages: list[dict]) -> list[dict]:
    converted: list[dict] = []
    for message in messages:
        role = message.get("role")
        if role == "system":
            continue
        if role == "tool":
            tool_call_id = message.get("tool_call_id")
            content = message.get("content") or ""
            converted.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_call_id,
                            "content": content,
                        }
                    ],
                }
            )
            continue
        content_blocks = _text_blocks_from_content(message.get("content"))
        tool_calls = message.get("tool_calls") or []
        if tool_calls:
            tool_blocks = [
                {
                    "type": "tool_use",
                    "id": call.get("id"),
                    "name": call.get("name"),
                    "input": call.get("arguments") or {},
                }
                for call in tool_calls
            ]
            converted.append(
                {
                    "role": "assistant",
                    "content": [*content_blocks, *tool_blocks],
                }
            )
            continue
        converted.append(
            {
                "role": role,
                "content": content_blocks or [{"type": "text", "text": ""}],
            }
        )
    return converted


class AnthropicProvider:
    def __init__(self, *, api_key: str | None = None, base_url: str | None = None) -> None:
        self.client = AsyncAnthropic(
            api_key=api_key or settings.anthropic_api_key,
            base_url=base_url or settings.anthropic_base_url,
        )
        self.logger = logging.getLogger(__name__)

    async def chat(self, model: str, messages: list[dict]) -> ChatResponse:
        system = _extract_system(messages)
        payload = {
            "model": model,
            "messages": _to_anthropic_messages(messages),
            "max_tokens": DEFAULT_MAX_TOKENS,
        }
        if system:
            payload["system"] = system
        result = await self.client.messages.create(**payload)
        text_parts: list[str] = []
        for block in result.content or []:
            if getattr(block, "type", None) == "text":
                text_parts.append(getattr(block, "text", ""))
        content = "".join(text_parts)
        usage = result.usage
        input_tokens = getattr(usage, "input_tokens", 0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or 0
        return ChatResponse(
            content=content,
            usage=ChatUsage(
                prompt_tokens=input_tokens,
                completion_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_tokens=0,
                thinking_tokens=0,
            ),
        )

    async def chat_with_tools(
        self,
        model: str,
        messages: list[dict],
        tools: list[ChatToolSpec],
        tool_choice: object | None = None,
    ) -> ChatResponse:
        system = _extract_system(messages)
        payload = {
            "model": model,
            "messages": _to_anthropic_messages(messages),
            "max_tokens": DEFAULT_MAX_TOKENS,
            "tools": [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.parameters,
                }
                for tool in tools
            ],
        }
        if system:
            payload["system"] = system
        if tool_choice:
            payload["tool_choice"] = tool_choice
        result = await self.client.messages.create(**payload)
        tool_calls: list[ChatToolCall] = []
        text_parts: list[str] = []
        for block in result.content or []:
            if getattr(block, "type", None) == "text":
                text_parts.append(getattr(block, "text", ""))
            if getattr(block, "type", None) == "tool_use":
                tool_calls.append(
                    ChatToolCall(
                        id=getattr(block, "id", ""),
                        name=getattr(block, "name", ""),
                        arguments=getattr(block, "input", {}) or {},
                    )
                )
        usage = result.usage
        input_tokens = getattr(usage, "input_tokens", 0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or 0
        return ChatResponse(
            content="".join(text_parts),
            usage=ChatUsage(
                prompt_tokens=input_tokens,
                completion_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_tokens=0,
                thinking_tokens=0,
            ),
            tool_calls=tool_calls or None,
            finish_reason=getattr(result, "stop_reason", None),
        )

    async def chat_stream(self, model: str, messages: list[dict]):
        system = _extract_system(messages)
        payload = {
            "model": model,
            "messages": _to_anthropic_messages(messages),
            "max_tokens": DEFAULT_MAX_TOKENS,
        }
        if system:
            payload["system"] = system
        async with self.client.messages.stream(**payload) as stream:
            async for text in stream.text_stream:
                if text:
                    yield ChatStreamChunk(content=text)
            final = await stream.get_final_message()
        usage = getattr(final, "usage", None)
        input_tokens = getattr(usage, "input_tokens", 0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or 0
        yield ChatStreamChunk(
            usage=ChatUsage(
                prompt_tokens=input_tokens,
                completion_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_tokens=0,
                thinking_tokens=0,
            )
        )
