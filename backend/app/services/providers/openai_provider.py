import logging
from openai import AsyncOpenAI, AsyncAzureOpenAI
from json_repair import loads as repair_json_loads

from app.core.config import settings
import json

from app.services.providers.base import (
    ChatResponse,
    ChatStreamChunk,
    ChatToolCall,
    ChatToolSpec,
    ChatUsage,
)


class NonChatModelError(Exception):
    pass


def _messages_to_prompt(messages: list[dict]) -> str:
    parts: list[str] = []
    for message in messages:
        content = message.get("content")
        if not content:
            continue
        if isinstance(content, list):
            text_parts: list[str] = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") in {"text", "input_text"} and item.get("text"):
                    text_parts.append(str(item.get("text")))
            content = "\n".join(text_parts).strip()
            if not content:
                continue
        role = message.get("role", "user")
        label = role.capitalize()
        parts.append(f"{label}: {content}")
    parts.append("Assistant:")
    return "\n\n".join(parts)


def _is_non_chat_model_error(exc: Exception) -> bool:
    message = str(exc).lower()
    status_code = getattr(exc, "status_code", None)
    if "not a chat model" in message:
        return True
    if "v1/chat/completions" in message and "v1/completions" in message:
        return True
    if "not supported in the v1/completions endpoint" in message:
        return True
    if "only supported in v1/responses" in message:
        return True
    if "not in v1/chat/completions" in message:
        return True
    if "chat/completions" in message and "v1/responses" in message:
        return True
    if status_code == 404 and (
        "chat/completions" in message or "only supported in v1/responses" in message
    ):
        return True
    return False


def _is_context_length_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "context_length_exceeded" in message or "input tokens exceed" in message


def _trim_messages_for_context(messages: list[dict], keep_tail: int = 20) -> list[dict]:
    if len(messages) <= keep_tail:
        return messages
    system_messages = [msg for msg in messages if msg.get("role") == "system"][:1]
    non_system_tail = [msg for msg in messages if msg.get("role") != "system"][-keep_tail:]
    return system_messages + non_system_tail


def _is_timeout_error(exc: Exception) -> bool:
    name = type(exc).__name__
    if name in {"APITimeoutError", "ReadTimeout", "TimeoutException", "ConnectTimeout"}:
        return True
    message = str(exc).lower()
    return "request timed out" in message or "read timeout" in message or "timed out" in message


def _is_unsupported_reasoning_effort_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        "unsupported value" in message
        and (
            "reasoning_effort" in message
            or "reasoning.effort" in message
            or "reasoning effort" in message
        )
    )


def _is_prompt_cache_param_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        "prompt_cache_key" in message
        or "prompt_cache_retention" in message
        or "prompt cache" in message
        or "prompt_cache" in message
    )


def _responses_input_text_size(items: list[dict]) -> int:
    size = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                text = part.get("text")
                if isinstance(text, str):
                    size += len(text)
        elif isinstance(content, str):
            size += len(content)
        for key in ("arguments", "output"):
            value = item.get(key)
            if isinstance(value, str):
                size += len(value)
    return size


def _extract_response_text(result: object) -> str:
    content = getattr(result, "output_text", "") or ""
    if content:
        return content
    output = getattr(result, "output", []) or []
    parts: list[str] = []
    for item in output:
        if getattr(item, "type", "") == "message":
            for part in getattr(item, "content", []) or []:
                if getattr(part, "type", "") == "output_text":
                    parts.append(getattr(part, "text", ""))
    return "\n".join(part for part in parts if part)


def _extract_response_tool_calls(result: object) -> list[ChatToolCall]:
    output = getattr(result, "output", []) or []
    tool_calls: list[ChatToolCall] = []
    for item in output:
        item_type = getattr(item, "type", "") or (item.get("type") if isinstance(item, dict) else "")
        if item_type != "function_call":
            continue
        call_id = getattr(item, "call_id", None) or (
            item.get("call_id") if isinstance(item, dict) else None
        )
        name = getattr(item, "name", None) or (item.get("name") if isinstance(item, dict) else None)
        arguments_raw = getattr(item, "arguments", None) or (
            item.get("arguments") if isinstance(item, dict) else None
        )
        arguments: dict = {}
        if isinstance(arguments_raw, dict):
            arguments = arguments_raw
        elif isinstance(arguments_raw, str) and arguments_raw.strip():
            try:
                parsed = repair_json_loads(arguments_raw)
                if isinstance(parsed, dict):
                    arguments = parsed
            except Exception:
                arguments = {}
        if call_id and name:
            tool_calls.append(
                ChatToolCall(
                    id=str(call_id),
                    name=str(name),
                    arguments=arguments,
                )
            )
    return tool_calls


def _coalesce_usage_tokens(usage: object | None) -> tuple[int, int, int, int, int]:
    if not usage:
        return 0, 0, 0, 0, 0
    prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
    completion_tokens = getattr(usage, "completion_tokens", 0) or 0
    total_tokens = getattr(usage, "total_tokens", 0) or 0
    input_tokens = getattr(usage, "input_tokens", 0) or 0
    output_tokens = getattr(usage, "output_tokens", 0) or 0
    if prompt_tokens == 0 and input_tokens:
        prompt_tokens = input_tokens
    if completion_tokens == 0 and output_tokens:
        completion_tokens = output_tokens
    if total_tokens == 0:
        total_tokens = prompt_tokens + completion_tokens
    return prompt_tokens, completion_tokens, total_tokens, input_tokens, output_tokens


def _extract_usage_details(usage: object | None) -> tuple[int, int]:
    if not usage:
        return 0, 0
    prompt_details = getattr(usage, "prompt_tokens_details", None)
    cached_tokens = getattr(prompt_details, "cached_tokens", 0) or getattr(
        usage, "cached_prompt_tokens", 0
    )
    completion_details = getattr(usage, "completion_tokens_details", None)
    thinking_tokens = getattr(completion_details, "reasoning_tokens", 0) or getattr(
        usage, "reasoning_tokens", 0
    )
    return cached_tokens or 0, thinking_tokens or 0


def _looks_like_responses_only_model(model: str) -> bool:
    normalized = (model or "").strip().lower()
    return normalized.endswith("-pro")


def _to_responses_tool_choice(tool_choice: object | None) -> object | None:
    if tool_choice is None:
        return None
    if isinstance(tool_choice, str):
        return tool_choice
    if not isinstance(tool_choice, dict):
        return None
    fn = tool_choice.get("function")
    if isinstance(fn, dict):
        name = fn.get("name")
        if name:
            return {"type": "function", "name": name}
    return tool_choice


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
        self.chat_timeout_seconds = 120.0
        # /v1/responses calls (used for *-pro reasoning models) can legitimately
        # take several minutes when reasoning.effort is high. Match the OpenAI
        # SDK default of 600s here so we don't kill connections that the server
        # is still working on. Celery soft/hard limits (15m/20m) bound this.
        self.responses_timeout_seconds = 600.0
        self.client = AsyncOpenAI(
            api_key=api_key or settings.openai_api_key,
            base_url=base_url or settings.openai_base_url,
            timeout=self.chat_timeout_seconds,
            max_retries=0,
        )
        self.reasoning_effort = reasoning_effort
        self.prompt_cache_key = prompt_cache_key
        self.prompt_cache_retention = (
            prompt_cache_retention or settings.openai_prompt_cache_retention
        )
        self.logger = logging.getLogger(__name__)
        self._responses_only_models: set[str] = set()

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

    def _mark_responses_only_model(self, model: str) -> None:
        normalized = (model or "").strip().lower()
        if normalized:
            self._responses_only_models.add(normalized)

    def _should_use_responses(self, model: str) -> bool:
        normalized = (model or "").strip().lower()
        if not normalized:
            return False
        if normalized in self._responses_only_models:
            return True
        return _looks_like_responses_only_model(normalized)

    def _responses_reasoning_effort(self, model: str) -> str | None:
        if self.reasoning_effort and self.reasoning_effort != "none":
            return self.reasoning_effort
        return None

    async def _create_chat_completion(self, payload: dict) -> object:
        try:
            return await self.client.with_options(
                timeout=self.chat_timeout_seconds,
                max_retries=0,
            ).chat.completions.create(**payload)
        except Exception as exc:
            if _is_non_chat_model_error(exc):
                raise NonChatModelError(str(exc)) from exc
            retry = False
            if _is_context_length_error(exc) and isinstance(payload.get("messages"), list):
                payload["messages"] = _trim_messages_for_context(payload["messages"])
                self._strip_prompt_cache(payload)
                self.logger.warning(
                    "chat.completions context limit exceeded, retrying with aggressively trimmed history"
                )
                retry = True
            elif _is_unsupported_reasoning_effort_error(exc) and payload.get(
                "reasoning_effort"
            ):
                payload.pop("reasoning_effort", None)
                self.logger.warning(
                    "chat.completions rejected reasoning_effort for model=%s; retrying without reasoning override: %s",
                    payload.get("model"),
                    exc,
                )
                retry = True
            elif _is_prompt_cache_param_error(exc):
                if self._strip_prompt_cache(payload):
                    self.logger.warning(
                        "chat.completions rejected prompt_cache params, retrying without them: %s",
                        exc,
                    )
                    retry = True
            if retry:
                return await self.client.with_options(
                    timeout=self.chat_timeout_seconds,
                    max_retries=0,
                ).chat.completions.create(**payload)
            raise

    async def _create_response(self, payload: dict) -> object:
        try:
            return await self.client.with_options(
                timeout=self.responses_timeout_seconds,
                max_retries=0,
            ).responses.create(**payload)
        except Exception as exc:
            if _is_timeout_error(exc):
                item_count = 0
                input_text_chars = 0
                if isinstance(payload.get("input"), list):
                    item_count = len(payload["input"])
                    input_text_chars = _responses_input_text_size(payload["input"])
                self.logger.warning(
                    "responses timeout model=%s input_items=%s input_chars=%s",
                    payload.get("model"),
                    item_count,
                    input_text_chars,
                )
                raise
            retry = False
            if _is_unsupported_reasoning_effort_error(exc):
                if payload.pop("reasoning", None) is not None:
                    self.logger.warning(
                        "responses rejected reasoning.effort for model=%s; retrying without reasoning override",
                        payload.get("model"),
                    )
                    retry = True
            elif _is_prompt_cache_param_error(exc):
                if self._strip_prompt_cache(payload):
                    self.logger.warning(
                        "responses rejected prompt_cache params, retrying without them: %s",
                        exc,
                    )
                    retry = True
            if retry:
                return await self.client.with_options(
                    timeout=self.responses_timeout_seconds,
                    max_retries=0,
                ).responses.create(**payload)
            raise

    async def _chat_via_responses(self, model: str, messages: list[dict]) -> object:
        input_items = _to_responses_input(messages)
        payload: dict[str, object] = {"model": model, "input": input_items}
        reasoning_effort = self._responses_reasoning_effort(model)
        if reasoning_effort:
            payload["reasoning"] = {"effort": reasoning_effort}
        self._apply_prompt_cache(payload)
        return await self._create_response(payload)

    async def _chat_with_tools_via_responses(
        self,
        model: str,
        messages: list[dict],
        tools: list[ChatToolSpec],
        tool_choice: object | None = None,
    ) -> object:
        input_items = _to_responses_input(messages)
        payload: dict[str, object] = {
            "model": model,
            "input": input_items,
            "tools": [
                {
                    "type": "function",
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                }
                for tool in tools
            ],
        }
        reasoning_effort = self._responses_reasoning_effort(model)
        if reasoning_effort:
            payload["reasoning"] = {"effort": reasoning_effort}
        mapped_tool_choice = _to_responses_tool_choice(tool_choice)
        if mapped_tool_choice is not None:
            payload["tool_choice"] = mapped_tool_choice
        self._apply_prompt_cache(payload)
        return await self._create_response(payload)

    async def _create_text_completion(self, payload: dict) -> object:
        try:
            return await self.client.completions.create(**payload)
        except Exception as exc:
            if _is_non_chat_model_error(exc):
                raise NonChatModelError(str(exc)) from exc
            raise

    async def chat(self, model: str, messages: list[dict]) -> ChatResponse:
        if self._should_use_responses(model):
            response = await self._chat_via_responses(model, messages)
            message = _extract_response_text(response)
            usage = getattr(response, "usage", None)
        else:
            payload = {"model": model, "messages": messages}
            self._apply_prompt_cache(payload)
            if self.reasoning_effort and self.reasoning_effort != "none":
                payload["reasoning_effort"] = self.reasoning_effort
            try:
                result = await self._create_chat_completion(payload)
                message = result.choices[0].message.content or "" if result.choices else ""
                usage = result.usage
            except NonChatModelError:
                self._mark_responses_only_model(model)
                response = await self._chat_via_responses(model, messages)
                message = _extract_response_text(response)
                usage = getattr(response, "usage", None)
        cached_tokens, thinking_tokens = _extract_usage_details(usage)
        prompt_tokens, completion_tokens, total_tokens, input_tokens, output_tokens = (
            _coalesce_usage_tokens(usage)
        )
        if input_tokens == 0:
            input_tokens = max(prompt_tokens - (cached_tokens or 0), 0)
        return ChatResponse(
            content=message,
            usage=ChatUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                input_tokens=input_tokens,
                output_tokens=output_tokens or completion_tokens,
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
        if self._should_use_responses(model):
            response = await self._chat_with_tools_via_responses(
                model=model,
                messages=normalized_messages,
                tools=tools,
                tool_choice=tool_choice,
            )
            content = _extract_response_text(response)
            usage = getattr(response, "usage", None)
            tool_calls = _extract_response_tool_calls(response)
            finish_reason = "tool_calls" if tool_calls else "stop"
            cached_tokens, thinking_tokens = _extract_usage_details(usage)
            prompt_tokens, completion_tokens, total_tokens, input_tokens, output_tokens = (
                _coalesce_usage_tokens(usage)
            )
            if input_tokens == 0:
                input_tokens = max(prompt_tokens - (cached_tokens or 0), 0)
            return ChatResponse(
                content=content or "",
                usage=ChatUsage(
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens or completion_tokens,
                    cached_tokens=cached_tokens or 0,
                    thinking_tokens=thinking_tokens or 0,
                ),
                tool_calls=tool_calls or None,
                finish_reason=finish_reason,
            )
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
        try:
            result = await self._create_chat_completion(payload)
            usage = result.usage
        except NonChatModelError:
            self._mark_responses_only_model(model)
            self.logger.warning(
                "Model %s does not support chat tools; falling back to responses.",
                model,
            )
            response = await self._chat_with_tools_via_responses(
                model=model,
                messages=normalized_messages,
                tools=tools,
                tool_choice=tool_choice,
            )
            content = _extract_response_text(response)
            usage = getattr(response, "usage", None)
            tool_calls = _extract_response_tool_calls(response)
            finish_reason = "tool_calls" if tool_calls else "stop"
            cached_tokens, thinking_tokens = _extract_usage_details(usage)
            prompt_tokens, completion_tokens, total_tokens, input_tokens, output_tokens = (
                _coalesce_usage_tokens(usage)
            )
            if input_tokens == 0:
                input_tokens = max(prompt_tokens - (cached_tokens or 0), 0)
            return ChatResponse(
                content=content or "",
                usage=ChatUsage(
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens or completion_tokens,
                    cached_tokens=cached_tokens or 0,
                    thinking_tokens=thinking_tokens or 0,
                ),
                tool_calls=tool_calls or None,
                finish_reason=finish_reason,
            )
        cached_tokens, thinking_tokens = _extract_usage_details(usage)
        tool_calls: list[ChatToolCall] = []
        finish_reason = None
        if result.choices:
            choice = result.choices[0]
            finish_reason = getattr(choice, "finish_reason", None)
            for call in choice.message.tool_calls or []:
                arguments = {}
                if call.function.arguments:
                    try:
                        parsed_arguments = repair_json_loads(call.function.arguments)
                        arguments = parsed_arguments if isinstance(parsed_arguments, dict) else {}
                    except Exception:
                        arguments = {}
                tool_calls.append(
                    ChatToolCall(
                        id=call.id,
                        name=call.function.name,
                        arguments=arguments,
                    )
                )
        prompt_tokens, completion_tokens, total_tokens, input_tokens, output_tokens = (
            _coalesce_usage_tokens(usage)
        )
        if input_tokens == 0:
            input_tokens = max(prompt_tokens - (cached_tokens or 0), 0)
        return ChatResponse(
            content=result.choices[0].message.content or "" if result.choices else "",
            usage=ChatUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                input_tokens=input_tokens,
                output_tokens=output_tokens or completion_tokens,
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
        result = await self._create_response(payload)
        content = _extract_response_text(result)
        sources = _extract_openai_sources(result)
        usage = getattr(result, "usage", None)
        prompt_tokens, completion_tokens, total_tokens, input_tokens, output_tokens = (
            _coalesce_usage_tokens(usage)
        )
        cached_tokens, thinking_tokens = _extract_usage_details(usage)
        if input_tokens == 0:
            input_tokens = max(prompt_tokens - (cached_tokens or 0), 0)
        return ChatResponse(
            content=content,
            usage=ChatUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                input_tokens=input_tokens,
                output_tokens=output_tokens or completion_tokens,
                cached_tokens=cached_tokens or 0,
                thinking_tokens=thinking_tokens or 0,
            ),
            sources=sources or None,
        )

    async def chat_stream(self, model: str, messages: list[dict]):
        if self._should_use_responses(model):
            response = await self._chat_via_responses(model, messages)
            content = _extract_response_text(response)
            if content:
                yield ChatStreamChunk(content=content)
            usage = getattr(response, "usage", None)
            prompt_tokens, completion_tokens, total_tokens, input_tokens, output_tokens = (
                _coalesce_usage_tokens(usage)
            )
            yield ChatStreamChunk(
                usage=ChatUsage(
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    input_tokens=input_tokens or prompt_tokens,
                    output_tokens=output_tokens or completion_tokens,
                    cached_tokens=0,
                    thinking_tokens=0,
                )
            )
            return
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        self._apply_prompt_cache(payload)
        if self.reasoning_effort and self.reasoning_effort != "none":
            payload["reasoning_effort"] = self.reasoning_effort
        try:
            stream = await self._create_chat_completion(payload)
        except NonChatModelError:
            self._mark_responses_only_model(model)
            response = await self._chat_via_responses(model, messages)
            content = _extract_response_text(response)
            if content:
                yield ChatStreamChunk(content=content)
            usage = getattr(response, "usage", None)
            prompt_tokens, completion_tokens, total_tokens, input_tokens, output_tokens = (
                _coalesce_usage_tokens(usage)
            )
            yield ChatStreamChunk(
                usage=ChatUsage(
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    input_tokens=input_tokens or prompt_tokens,
                    output_tokens=output_tokens or completion_tokens,
                    cached_tokens=0,
                    thinking_tokens=0,
                )
            )
            return
        usage_sent = False
        async for event in stream:
            if event.choices:
                delta = event.choices[0].delta.content
                if delta:
                    yield ChatStreamChunk(content=delta)
            if event.usage:
                usage_sent = True
                cached_tokens, thinking_tokens = _extract_usage_details(event.usage)
                prompt_tokens, completion_tokens, total_tokens, input_tokens, output_tokens = (
                    _coalesce_usage_tokens(event.usage)
                )
                if input_tokens == 0:
                    input_tokens = max(prompt_tokens - (cached_tokens or 0), 0)
                yield ChatStreamChunk(
                    usage=ChatUsage(
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        total_tokens=total_tokens,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens or completion_tokens,
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
        self.chat_timeout_seconds = 180.0
        self.client = AsyncAzureOpenAI(
            api_key=api_key or settings.azure_openai_api_key,
            api_version=settings.azure_openai_api_version,
            azure_endpoint=endpoint or settings.azure_openai_endpoint,
            timeout=self.chat_timeout_seconds,
            max_retries=0,
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
        cached_tokens, thinking_tokens = _extract_usage_details(usage)
        prompt_tokens, completion_tokens, total_tokens, input_tokens, output_tokens = (
            _coalesce_usage_tokens(usage)
        )
        if input_tokens == 0:
            input_tokens = max(prompt_tokens - (cached_tokens or 0), 0)
        return ChatResponse(
            content=message,
            usage=ChatUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                input_tokens=input_tokens,
                output_tokens=output_tokens or completion_tokens,
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
        cached_tokens, thinking_tokens = _extract_usage_details(usage)
        tool_calls: list[ChatToolCall] = []
        finish_reason = None
        if result.choices:
            choice = result.choices[0]
            finish_reason = getattr(choice, "finish_reason", None)
            for call in choice.message.tool_calls or []:
                arguments = {}
                if call.function.arguments:
                    try:
                        parsed_arguments = repair_json_loads(call.function.arguments)
                        arguments = parsed_arguments if isinstance(parsed_arguments, dict) else {}
                    except Exception:
                        arguments = {}
                tool_calls.append(
                    ChatToolCall(
                        id=call.id,
                        name=call.function.name,
                        arguments=arguments,
                    )
                )
        prompt_tokens, completion_tokens, total_tokens, input_tokens, output_tokens = (
            _coalesce_usage_tokens(usage)
        )
        if input_tokens == 0:
            input_tokens = max(prompt_tokens - (cached_tokens or 0), 0)
        return ChatResponse(
            content=result.choices[0].message.content or "" if result.choices else "",
            usage=ChatUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                input_tokens=input_tokens,
                output_tokens=output_tokens or completion_tokens,
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
        if role == "assistant" and isinstance(message.get("tool_calls"), list):
            # Preserve prior assistant tool calls so subsequent function_call_output
            # items can reference a known call_id in Responses API.
            for call in message.get("tool_calls", []):
                if not isinstance(call, dict):
                    continue
                call_id = call.get("id")
                function_block = call.get("function") if isinstance(call.get("function"), dict) else {}
                name = call.get("name") or function_block.get("name")
                raw_args = call.get("arguments", function_block.get("arguments", {}))
                if not call_id or not name:
                    continue
                if isinstance(raw_args, str):
                    arguments = raw_args
                else:
                    try:
                        arguments = json.dumps(raw_args or {})
                    except TypeError:
                        arguments = "{}"
                items.append(
                    {
                        "type": "function_call",
                        "call_id": str(call_id),
                        "name": str(name),
                        "arguments": arguments,
                    }
                )
        if role == "tool":
            tool_output = message.get("content")
            if isinstance(tool_output, list):
                text_parts: list[str] = []
                for part in tool_output:
                    if isinstance(part, dict) and part.get("type") in {"text", "input_text", "output_text"}:
                        text_parts.append(str(part.get("text") or ""))
                tool_output = "\n".join(text_parts).strip()
            if isinstance(tool_output, str) and tool_output.strip():
                item: dict[str, object] = {
                    "type": "function_call_output",
                    "output": tool_output,
                }
                tool_call_id = message.get("tool_call_id")
                if tool_call_id:
                    item["call_id"] = tool_call_id
                items.append(item)
            continue
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
                cached_tokens, thinking_tokens = _extract_usage_details(event.usage)
                prompt_tokens, completion_tokens, total_tokens, input_tokens, output_tokens = (
                    _coalesce_usage_tokens(event.usage)
                )
                if input_tokens == 0:
                    input_tokens = max(prompt_tokens - (cached_tokens or 0), 0)
                yield ChatStreamChunk(
                    usage=ChatUsage(
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        total_tokens=total_tokens,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens or completion_tokens,
                        cached_tokens=cached_tokens or 0,
                        thinking_tokens=thinking_tokens or 0,
                    )
                )
        if not usage_sent:
            yield ChatStreamChunk(usage=ChatUsage(0, 0, 0, 0, 0, 0, 0))
