import logging
from openai import AsyncOpenAI, AsyncAzureOpenAI

from app.core.config import settings
import json

from app.services.providers.base import (
    ChatResponse,
    ChatStreamChunk,
    ChatToolCall,
    ChatToolSpec,
    ChatUsage,
)


class OpenAIProvider:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        reasoning_effort: str | None = None,
        prompt_cache_key: str | None = None,
        prompt_cache_retention: str | None = None,
    ) -> None:
        self.client = AsyncOpenAI(
            api_key=api_key or settings.openai_api_key,
            base_url=base_url or settings.openai_base_url,
        )
        self.reasoning_effort = reasoning_effort
        self.prompt_cache_key = prompt_cache_key
        self.prompt_cache_retention = (
            prompt_cache_retention or settings.openai_prompt_cache_retention
        )
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

    async def _create_chat_completion(self, payload: dict) -> object:
        try:
            return await self.client.chat.completions.create(**payload)
        except Exception as exc:
            retry = False
            if payload.get("reasoning_effort"):
                payload.pop("reasoning_effort", None)
                self.logger.error(
                    "chat.completions rejected reasoning_effort, retrying without it: %s",
                    exc,
                    exc_info=True,
                )
                retry = True
            if self._strip_prompt_cache(payload):
                self.logger.error(
                    "chat.completions rejected prompt_cache params, retrying without them: %s",
                    exc,
                    exc_info=True,
                )
                retry = True
            if retry:
                return await self.client.chat.completions.create(**payload)
            raise

    async def chat(self, model: str, messages: list[dict]) -> ChatResponse:
        payload = {"model": model, "messages": messages}
        self._apply_prompt_cache(payload)
        if self.reasoning_effort and self.reasoning_effort != "none":
            payload["reasoning_effort"] = self.reasoning_effort
        result = await self._create_chat_completion(payload)
        message = result.choices[0].message.content or ""
        usage = result.usage
        cached_tokens = 0
        thinking_tokens = 0
        if usage:
            prompt_details = getattr(usage, "prompt_tokens_details", None)
            cached_tokens = getattr(prompt_details, "cached_tokens", 0) or getattr(
                usage, "cached_prompt_tokens", 0
            )
            completion_details = getattr(usage, "completion_tokens_details", None)
            thinking_tokens = getattr(completion_details, "reasoning_tokens", 0) or getattr(
                usage, "reasoning_tokens", 0
            )
        prompt_tokens = usage.prompt_tokens or 0
        input_tokens = max(prompt_tokens - (cached_tokens or 0), 0)
        return ChatResponse(
            content=message,
            usage=ChatUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=usage.completion_tokens or 0,
                total_tokens=usage.total_tokens or 0,
                input_tokens=input_tokens,
                output_tokens=usage.completion_tokens or 0,
                cached_tokens=cached_tokens or 0,
                thinking_tokens=thinking_tokens or 0,
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
        if self.reasoning_effort and self.reasoning_effort != "none":
            payload["reasoning_effort"] = self.reasoning_effort
        result = await self._create_chat_completion(payload)
        usage = result.usage
        cached_tokens = 0
        thinking_tokens = 0
        if usage:
            prompt_details = getattr(usage, "prompt_tokens_details", None)
            cached_tokens = getattr(prompt_details, "cached_tokens", 0) or getattr(
                usage, "cached_prompt_tokens", 0
            )
            completion_details = getattr(usage, "completion_tokens_details", None)
            thinking_tokens = getattr(completion_details, "reasoning_tokens", 0) or getattr(
                usage, "reasoning_tokens", 0
            )
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
        prompt_tokens = usage.prompt_tokens or 0
        input_tokens = max(prompt_tokens - (cached_tokens or 0), 0)
        return ChatResponse(
            content=result.choices[0].message.content or "" if result.choices else "",
            usage=ChatUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=usage.completion_tokens or 0,
                total_tokens=usage.total_tokens or 0,
                input_tokens=input_tokens,
                output_tokens=usage.completion_tokens or 0,
                cached_tokens=cached_tokens or 0,
                thinking_tokens=thinking_tokens or 0,
            ),
            tool_calls=tool_calls or None,
            finish_reason=finish_reason,
        )

    async def chat_grounded(self, model: str, messages: list[dict]) -> ChatResponse:
        input_items = _to_responses_input(messages)
        payload = {
            "model": model,
            "input": input_items,
            "tools": [{"type": "web_search"}],
        }
        self._apply_prompt_cache(payload)
        try:
            result = await self.client.responses.create(**payload)
        except Exception as exc:
            if self._strip_prompt_cache(payload):
                self.logger.error(
                    "responses rejected prompt_cache params, retrying without them: %s",
                    exc,
                    exc_info=True,
                )
                result = await self.client.responses.create(**payload)
            else:
                raise
        content = getattr(result, "output_text", "") or ""
        if not content:
            output = getattr(result, "output", []) or []
            parts: list[str] = []
            for item in output:
                if getattr(item, "type", "") == "message":
                    for part in getattr(item, "content", []) or []:
                        if getattr(part, "type", "") == "output_text":
                            parts.append(getattr(part, "text", ""))
            content = "\n".join(part for part in parts if part)
        sources = _extract_openai_sources(result)
        return ChatResponse(
            content=content,
            usage=ChatUsage(0, 0, 0, 0, 0, 0, 0),
            sources=sources or None,
        )

    async def chat_stream(self, model: str, messages: list[dict]):
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        self._apply_prompt_cache(payload)
        if self.reasoning_effort and self.reasoning_effort != "none":
            payload["reasoning_effort"] = self.reasoning_effort
        stream = await self._create_chat_completion(payload)
        usage_sent = False
        async for event in stream:
            if event.choices:
                delta = event.choices[0].delta.content
                if delta:
                    yield ChatStreamChunk(content=delta)
            if event.usage:
                usage_sent = True
                cached_tokens = 0
                thinking_tokens = 0
                prompt_details = getattr(event.usage, "prompt_tokens_details", None)
                cached_tokens = getattr(prompt_details, "cached_tokens", 0) or getattr(
                    event.usage, "cached_prompt_tokens", 0
                )
                completion_details = getattr(event.usage, "completion_tokens_details", None)
                thinking_tokens = getattr(completion_details, "reasoning_tokens", 0) or getattr(
                    event.usage, "reasoning_tokens", 0
                )
                prompt_tokens = event.usage.prompt_tokens or 0
                input_tokens = max(prompt_tokens - (cached_tokens or 0), 0)
                yield ChatStreamChunk(
                    usage=ChatUsage(
                        prompt_tokens=prompt_tokens,
                        completion_tokens=event.usage.completion_tokens or 0,
                        total_tokens=event.usage.total_tokens or 0,
                        input_tokens=input_tokens,
                        output_tokens=event.usage.completion_tokens or 0,
                        cached_tokens=cached_tokens or 0,
                        thinking_tokens=thinking_tokens or 0,
                    )
                )
        if not usage_sent:
            yield ChatStreamChunk(usage=ChatUsage(0, 0, 0, 0, 0, 0, 0))


class AzureOpenAIProvider:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        endpoint: str | None = None,
        reasoning_effort: str | None = None,
        prompt_cache_key: str | None = None,
        prompt_cache_retention: str | None = None,
    ) -> None:
        self.client = AsyncAzureOpenAI(
            api_key=api_key or settings.azure_openai_api_key,
            api_version=settings.azure_openai_api_version,
            azure_endpoint=endpoint or settings.azure_openai_endpoint,
        )
        self.reasoning_effort = reasoning_effort
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
        deployment = settings.azure_openai_deployment or model
        payload = {"model": deployment, "messages": messages}
        self._apply_prompt_cache(payload)
        try:
            result = await self.client.chat.completions.create(**payload)
        except Exception as exc:
            if self._strip_prompt_cache(payload):
                self.logger.error(
                    "azure chat.completions rejected prompt_cache params, retrying without them: %s",
                    exc,
                    exc_info=True,
                )
                result = await self.client.chat.completions.create(**payload)
            else:
                raise
        message = result.choices[0].message.content or ""
        usage = result.usage
        cached_tokens = 0
        thinking_tokens = 0
        if usage:
            prompt_details = getattr(usage, "prompt_tokens_details", None)
            cached_tokens = getattr(prompt_details, "cached_tokens", 0) or getattr(
                usage, "cached_prompt_tokens", 0
            )
            completion_details = getattr(usage, "completion_tokens_details", None)
            thinking_tokens = getattr(completion_details, "reasoning_tokens", 0) or getattr(
                usage, "reasoning_tokens", 0
            )
        prompt_tokens = usage.prompt_tokens or 0
        input_tokens = max(prompt_tokens - (cached_tokens or 0), 0)
        return ChatResponse(
            content=message,
            usage=ChatUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=usage.completion_tokens or 0,
                total_tokens=usage.total_tokens or 0,
                input_tokens=input_tokens,
                output_tokens=usage.completion_tokens or 0,
                cached_tokens=cached_tokens or 0,
                thinking_tokens=thinking_tokens or 0,
            ),
        )

    async def chat_with_tools(
        self,
        model: str,
        messages: list[dict],
        tools: list[ChatToolSpec],
        tool_choice: object | None = None,
    ) -> ChatResponse:
        deployment = settings.azure_openai_deployment or model
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
            "model": deployment,
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
                    "azure chat.completions rejected prompt_cache params, retrying without them: %s",
                    exc,
                    exc_info=True,
                )
                result = await self.client.chat.completions.create(**payload)
            else:
                raise
        usage = result.usage
        cached_tokens = 0
        thinking_tokens = 0
        if usage:
            prompt_details = getattr(usage, "prompt_tokens_details", None)
            cached_tokens = getattr(prompt_details, "cached_tokens", 0) or getattr(
                usage, "cached_prompt_tokens", 0
            )
            completion_details = getattr(usage, "completion_tokens_details", None)
            thinking_tokens = getattr(completion_details, "reasoning_tokens", 0) or getattr(
                usage, "reasoning_tokens", 0
            )
        tool_calls: list[ChatToolCall] = []
        finish_reason = None
        if result.choices:
            choice = result.choices[0]
            finish_reason = getattr(choice, "finish_reason", None)
            for call in choice.message.tool_calls or []:
                tool_calls.append(
                    ChatToolCall(
                        id=call.id,
                        name=call.function.name,
                        arguments=call.function.arguments or {},
                    )
                )
        prompt_tokens = usage.prompt_tokens or 0
        input_tokens = max(prompt_tokens - (cached_tokens or 0), 0)
        return ChatResponse(
            content=result.choices[0].message.content or "" if result.choices else "",
            usage=ChatUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=usage.completion_tokens or 0,
                total_tokens=usage.total_tokens or 0,
                input_tokens=input_tokens,
                output_tokens=usage.completion_tokens or 0,
                cached_tokens=cached_tokens or 0,
                thinking_tokens=thinking_tokens or 0,
            ),
            tool_calls=tool_calls or None,
            finish_reason=finish_reason,
        )


def _to_responses_input(messages: list[dict]) -> list[dict]:
    items: list[dict] = []
    for message in messages:
        role = message.get("role")
        if role not in {"system", "user", "assistant"}:
            continue
        content = message.get("content")
        parts: list[dict] = []
        text_type = "output_text" if role == "assistant" else "input_text"
        if isinstance(content, list):
            for part in content:
                if part.get("type") == "text":
                    text = part.get("text")
                    if text:
                        parts.append({"type": text_type, "text": text})
                elif part.get("type") == "image_url":
                    if role != "user":
                        continue
                    url = part.get("image_url", {}).get("url")
                    if url:
                        parts.append({"type": "input_image", "image_url": url})
        elif isinstance(content, str):
            parts.append({"type": text_type, "text": content})
        if parts:
            items.append({"role": role, "content": parts})
    return items


def _extract_openai_sources(response) -> list[str]:
    sources: list[str] = []
    output = getattr(response, "output", []) or []
    for item in output:
        citations = getattr(item, "citations", None)
        if citations:
            for citation in citations:
                url = getattr(citation, "url", None) or citation.get("url") if isinstance(citation, dict) else None
                if url:
                    sources.append(url)
        results = getattr(item, "results", None)
        if results:
            for result in results:
                url = getattr(result, "url", None) or result.get("url") if isinstance(result, dict) else None
                if url:
                    sources.append(url)
        content = getattr(item, "content", None)
        if content:
            for part in content:
                annotations = getattr(part, "annotations", None)
                if annotations:
                    for annotation in annotations:
                        url = getattr(annotation, "url", None) or annotation.get("url") if isinstance(annotation, dict) else None
                        if url:
                            sources.append(url)
    return list(dict.fromkeys(sources))

    async def chat_stream(self, model: str, messages: list[dict]):
        deployment = settings.azure_openai_deployment or model
        payload = {
            "model": deployment,
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
                    "azure chat.completions rejected prompt_cache params, retrying without them: %s",
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
                cached_tokens = 0
                thinking_tokens = 0
                prompt_details = getattr(event.usage, "prompt_tokens_details", None)
                cached_tokens = getattr(prompt_details, "cached_tokens", 0) or getattr(
                    event.usage, "cached_prompt_tokens", 0
                )
                completion_details = getattr(event.usage, "completion_tokens_details", None)
                thinking_tokens = getattr(completion_details, "reasoning_tokens", 0) or getattr(
                    event.usage, "reasoning_tokens", 0
                )
                prompt_tokens = event.usage.prompt_tokens or 0
                input_tokens = max(prompt_tokens - (cached_tokens or 0), 0)
                yield ChatStreamChunk(
                    usage=ChatUsage(
                        prompt_tokens=prompt_tokens,
                        completion_tokens=event.usage.completion_tokens or 0,
                        total_tokens=event.usage.total_tokens or 0,
                        input_tokens=input_tokens,
                        output_tokens=event.usage.completion_tokens or 0,
                        cached_tokens=cached_tokens or 0,
                        thinking_tokens=thinking_tokens or 0,
                    )
                )
        if not usage_sent:
            yield ChatStreamChunk(usage=ChatUsage(0, 0, 0, 0, 0, 0, 0))
