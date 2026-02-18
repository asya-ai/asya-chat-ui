import logging
from groq import AsyncGroq

from app.core.config import settings
import json

from app.services.providers.base import (
    ChatResponse,
    ChatStreamChunk,
    ChatToolCall,
    ChatToolSpec,
    ChatUsage,
)


class GroqProvider:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        prompt_cache_key: str | None = None,
        prompt_cache_retention: str | None = None,
    ) -> None:
        self.client = AsyncGroq(
            api_key=api_key or settings.groq_api_key,
            base_url=base_url or settings.groq_base_url,
        )
        self.prompt_cache_key = prompt_cache_key
        self.prompt_cache_retention = prompt_cache_retention
        self.logger = logging.getLogger(__name__)

    def _apply_prompt_cache(self, payload: dict) -> None:
        if self.prompt_cache_key:
            payload["prompt_cache_key"] = self.prompt_cache_key
        if self.prompt_cache_retention:
            payload["prompt_cache_retention"] = self.prompt_cache_retention

    def _strip_prompt_cache(self, payload: dict) -> bool:
        removed = False
        if payload.pop("prompt_cache_key", None) is not None:
            removed = True
        if payload.pop("prompt_cache_retention", None) is not None:
            removed = True
        return removed

    async def chat(self, model: str, messages: list[dict]) -> ChatResponse:
        payload = {"model": model, "messages": messages}
        self._apply_prompt_cache(payload)
        try:
            result = await self.client.chat.completions.create(**payload)
        except Exception as exc:
            if self._strip_prompt_cache(payload):
                self.logger.error(
                    "groq chat.completions rejected prompt_cache params, retrying without them: %s",
                    exc,
                    exc_info=True,
                )
                result = await self.client.chat.completions.create(**payload)
            else:
                raise
        message = result.choices[0].message.content or ""
        usage = result.usage
        return ChatResponse(
            content=message,
            usage=ChatUsage(
                prompt_tokens=usage.prompt_tokens or 0,
                completion_tokens=usage.completion_tokens or 0,
                total_tokens=usage.total_tokens or 0,
                input_tokens=usage.prompt_tokens or 0,
                output_tokens=usage.completion_tokens or 0,
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
        normalized_messages = []
        for message in messages:
            tool_calls = message.get("tool_calls")
            if tool_calls:
                normalized_messages.append(
                    {
                        **{k: v for k, v in message.items() if k != "tool_calls"},
                        "tool_calls": [
                            {
                                "id": call.get("id"),
                                "type": "function",
                                "function": {
                                    "name": call.get("name"),
                                    "arguments": json.dumps(call.get("arguments", {})),
                                },
                            }
                            for call in tool_calls
                        ],
                    }
                )
            else:
                normalized_messages.append(message)
        payload = {
            "model": model,
            "messages": normalized_messages,
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
                for tool in tools
            ],
            "tool_choice": tool_choice or "auto",
        }
        self._apply_prompt_cache(payload)
        try:
            result = await self.client.chat.completions.create(**payload)
        except Exception as exc:
            if self._strip_prompt_cache(payload):
                self.logger.error(
                    "groq chat.completions rejected prompt_cache params, retrying without them: %s",
                    exc,
                    exc_info=True,
                )
                result = await self.client.chat.completions.create(**payload)
            else:
                raise
        usage = result.usage
        tool_calls: list[ChatToolCall] = []
        finish_reason = None
        if result.choices:
            choice = result.choices[0]
            finish_reason = getattr(choice, "finish_reason", None)
            for call in choice.message.tool_calls or []:
                arguments = {}
                if call.function.arguments:
                    try:
                        arguments = json.loads(call.function.arguments)
                    except json.JSONDecodeError:
                        arguments = {}
                tool_calls.append(
                    ChatToolCall(
                        id=call.id,
                        name=call.function.name,
                        arguments=arguments,
                    )
                )
        return ChatResponse(
            content=result.choices[0].message.content or "" if result.choices else "",
            usage=ChatUsage(
                prompt_tokens=usage.prompt_tokens or 0,
                completion_tokens=usage.completion_tokens or 0,
                total_tokens=usage.total_tokens or 0,
                input_tokens=usage.prompt_tokens or 0,
                output_tokens=usage.completion_tokens or 0,
                cached_tokens=0,
                thinking_tokens=0,
            ),
            tool_calls=tool_calls or None,
            finish_reason=finish_reason,
        )

    async def chat_stream(self, model: str, messages: list[dict]):
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        self._apply_prompt_cache(payload)
        try:
            stream = await self.client.chat.completions.create(**payload)
        except Exception as exc:
            if self._strip_prompt_cache(payload):
                self.logger.error(
                    "groq chat.completions rejected prompt_cache params, retrying without them: %s",
                    exc,
                    exc_info=True,
                )
                stream = await self.client.chat.completions.create(**payload)
            else:
                raise
        usage_sent = False
        async for event in stream:
            if event.choices:
                delta = event.choices[0].delta.content
                if delta:
                    yield ChatStreamChunk(content=delta)
            if event.usage:
                usage_sent = True
                yield ChatStreamChunk(
                    usage=ChatUsage(
                        prompt_tokens=event.usage.prompt_tokens or 0,
                        completion_tokens=event.usage.completion_tokens or 0,
                        total_tokens=event.usage.total_tokens or 0,
                        input_tokens=event.usage.prompt_tokens or 0,
                        output_tokens=event.usage.completion_tokens or 0,
                        cached_tokens=0,
                        thinking_tokens=0,
                    )
                )
        if not usage_sent:
            yield ChatStreamChunk(usage=ChatUsage(0, 0, 0, 0, 0, 0, 0))
