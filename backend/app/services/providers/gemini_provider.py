import anyio
import hashlib
import json
import time
from dataclasses import dataclass
from google import genai
from google.genai import types

from app.core.config import settings
import base64
import logging
from app.services.providers.base import (
    ChatResponse,
    ChatStreamChunk,
    ChatToolCall,
    ChatToolSpec,
    ChatUsage,
)


@dataclass
class _GeminiCachedContentEntry:
    name: str
    expires_at: float


_GEMINI_CACHED_CONTENT: dict[str, _GeminiCachedContentEntry] = {}


class GeminiProvider:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        prompt_cache_key: str | None = None,
        prompt_cache_enabled: bool = True,
    ) -> None:
        self.client = genai.Client(
            api_key=api_key or settings.gemini_api_key,
            http_options=types.HttpOptions(timeout=180_000),
        )
        self.logger = logging.getLogger(__name__)
        self.prompt_cache_key = prompt_cache_key
        self.prompt_cache_enabled = prompt_cache_enabled

    @staticmethod
    def _tools_fingerprint(tools: list | None) -> str:
        if not tools:
            return ""
        serialized = []
        for tool in tools:
            if isinstance(tool, dict):
                serialized.append(tool)
            elif hasattr(tool, "model_dump"):
                serialized.append(tool.model_dump(mode="json", exclude_none=True))
            else:
                serialized.append(str(tool))
        return json.dumps(serialized, sort_keys=True, ensure_ascii=False)

    def _cache_key_for_contents(
        self,
        model: str,
        contents: list[dict],
        *,
        system_instruction: str | None = None,
        tools: list | None = None,
    ) -> str:
        base = json.dumps(contents, sort_keys=True, ensure_ascii=False)
        prefix = self.prompt_cache_key or ""
        tools_fp = self._tools_fingerprint(tools)
        digest = hashlib.sha256(
            f"{model}:{prefix}:{system_instruction or ''}:{tools_fp}:{base}".encode(
                "utf-8"
            )
        ).hexdigest()
        return digest

    def _prune_cached_content(self) -> None:
        now = time.time()
        expired = [key for key, entry in _GEMINI_CACHED_CONTENT.items() if entry.expires_at <= now]
        for key in expired:
            _GEMINI_CACHED_CONTENT.pop(key, None)
        max_items = settings.gemini_cached_content_max_items
        if len(_GEMINI_CACHED_CONTENT) > max_items:
            for key in list(_GEMINI_CACHED_CONTENT.keys())[:-max_items]:
                _GEMINI_CACHED_CONTENT.pop(key, None)

    def _get_cached_content_name(self, cache_key: str) -> str | None:
        self._prune_cached_content()
        entry = _GEMINI_CACHED_CONTENT.get(cache_key)
        if not entry:
            return None
        if entry.expires_at <= time.time():
            _GEMINI_CACHED_CONTENT.pop(cache_key, None)
            return None
        return entry.name

    def _set_cached_content_name(self, cache_key: str, name: str) -> None:
        ttl_seconds = max(60, settings.gemini_cached_content_ttl_seconds)
        expires_at = time.time() + ttl_seconds
        _GEMINI_CACHED_CONTENT[cache_key] = _GeminiCachedContentEntry(
            name=name, expires_at=expires_at
        )

    def _is_cacheable_content(self, item: dict) -> bool:
        role = item.get("role")
        if role not in {"user", "model"}:
            return False
        parts = item.get("parts")
        if not isinstance(parts, list) or not parts:
            return False
        for part in parts:
            if not isinstance(part, dict):
                return False
            if "text" in part or "inline_data" in part:
                continue
            return False
        return True

    def _maybe_cached_content_config(
        self,
        model: str,
        contents: list[dict],
        *,
        system_instruction: str | None = None,
        tools: list | None = None,
    ) -> tuple[list[dict], types.GenerateContentConfig | None]:
        if not self.prompt_cache_enabled or not settings.gemini_cached_content_enabled:
            return contents, None
        if len(contents) < 2:
            return contents, None
        prefix = contents[:-1]
        suffix = contents[-1:]
        if not all(self._is_cacheable_content(item) for item in prefix):
            return contents, None
        total_chars = sum(
            len(part.get("text", ""))
            for item in prefix
            for part in item.get("parts", [])
            if isinstance(part, dict) and "text" in part
        )
        if total_chars < 4096:
            return contents, None
        cache_key = self._cache_key_for_contents(
            model,
            prefix,
            system_instruction=system_instruction,
            tools=tools,
        )
        cached_name = self._get_cached_content_name(cache_key)
        if not cached_name:
            try:
                prefix_contents = []
                for item in prefix:
                    role = item.get("role")
                    parts = item.get("parts")
                    if not role or not parts:
                        return contents, None
                    prefix_contents.append(types.Content(role=role, parts=parts))
                # Tools/system must live on the cache; generate_content rejects them
                # when cached_content is set.
                create_kwargs: dict = {
                    "contents": prefix_contents,
                    "ttl": f"{settings.gemini_cached_content_ttl_seconds}s",
                }
                if system_instruction:
                    create_kwargs["system_instruction"] = system_instruction
                if tools:
                    create_kwargs["tools"] = tools
                cached = self.client.caches.create(
                    model=model,
                    config=types.CreateCachedContentConfig(**create_kwargs),
                )
                cached_name = cached.name
                self._set_cached_content_name(cache_key, cached_name)
            except Exception as exc:
                self.logger.error(
                    "Gemini cached content create failed, continuing without cache: %s",
                    exc,
                    exc_info=True,
                )
                return contents, None
        if not cached_name:
            return contents, None
        return suffix, types.GenerateContentConfig(cached_content=cached_name)

    def _extract_thought_signature(self, part: object, function_call: object) -> str | None:
        def _normalize_signature(raw: object) -> str | None:
            if raw is None:
                return None
            if isinstance(raw, bytes):
                return base64.b64encode(raw).decode("ascii")
            if isinstance(raw, str):
                return raw
            try:
                return str(raw)
            except Exception:
                return None

        def _extract_from_mapping(data: dict) -> str | None:
            direct = _normalize_signature(
                data.get("thought_signature") or data.get("thoughtSignature")
            )
            if direct:
                return direct
            nested = data.get("function_call") or data.get("functionCall")
            if isinstance(nested, dict):
                nested_value = _normalize_signature(
                    nested.get("thought_signature") or nested.get("thoughtSignature")
                )
                if nested_value:
                    return nested_value
            return None

        for obj in (function_call, part):
            value = _normalize_signature(
                getattr(obj, "thought_signature", None)
                or getattr(obj, "thoughtSignature", None)
            )
            if value:
                return value
            if isinstance(obj, dict):
                mapped = _extract_from_mapping(obj)
                if mapped:
                    return mapped
            for attr in ("model_dump", "dict"):
                fn = getattr(obj, attr, None)
                if not callable(fn):
                    continue
                data = fn()
                if isinstance(data, dict):
                    mapped = _extract_from_mapping(data)
                    if mapped:
                        return mapped
        return None

    @staticmethod
    def _build_function_call_part(call: dict) -> dict:
        thought_signature = call.get("thought_signature") or call.get("thoughtSignature")
        arguments = call.get("arguments", {})
        if isinstance(arguments, dict) and "thought_signature" in arguments:
            arguments = {
                key: value for key, value in arguments.items() if key != "thought_signature"
            }
        part = {
            "function_call": {
                "name": call.get("name"),
                "args": arguments,
                "id": call.get("id"),
            }
        }
        if thought_signature:
            # Must be on the Part, not nested under function_call.
            part["thought_signature"] = thought_signature
        return part

    @staticmethod
    def _model_parts_for_assistant_tool_message(message: dict) -> list[dict]:
        """Keep preamble text with tool calls so later turns still see narration."""
        parts: list[dict] = []
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            parts.append({"text": content})
        elif isinstance(content, list):
            for item in content:
                if not isinstance(item, dict) or item.get("type") != "text":
                    continue
                text = item.get("text")
                if text:
                    parts.append({"text": text})
        for call in message.get("tool_calls") or []:
            if isinstance(call, dict):
                parts.append(GeminiProvider._build_function_call_part(call))
        return parts

    @staticmethod
    def _extract_response_text(response: object) -> str:
        """Collect visible text even when the response also contains function calls."""
        text_parts: list[str] = []
        for candidate in getattr(response, "candidates", None) or []:
            content = getattr(candidate, "content", None)
            for part in getattr(content, "parts", None) or []:
                if getattr(part, "thought", None):
                    continue
                if getattr(part, "function_call", None):
                    continue
                text = getattr(part, "text", None)
                if text:
                    text_parts.append(text)
        if text_parts:
            return "".join(text_parts)
        try:
            return getattr(response, "text", None) or ""
        except Exception:
            return ""

    @staticmethod
    def _extract_reasoning_content(response: object) -> str | None:
        """Collect Gemini thought parts when the model exposes them."""
        thought_parts: list[str] = []
        for candidate in getattr(response, "candidates", None) or []:
            content = getattr(candidate, "content", None)
            for part in getattr(content, "parts", None) or []:
                if not getattr(part, "thought", None):
                    continue
                text = getattr(part, "text", None)
                if text:
                    thought_parts.append(text)
        if not thought_parts:
            return None
        return "".join(thought_parts)

    @staticmethod
    def _extract_system_instruction(messages: list[dict]) -> str | None:
        parts: list[str] = []
        for msg in messages:
            if msg.get("role") != "system":
                continue
            content = msg.get("content")
            if isinstance(content, str) and content:
                parts.append(content)
            elif isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        text = item.get("text")
                        if text:
                            parts.append(text)
        return "\n\n".join(parts) if parts else None

    def _build_config(
        self,
        *,
        system_instruction: str | None = None,
        cache_config: types.GenerateContentConfig | None = None,
        tools: list | None = None,
    ) -> types.GenerateContentConfig | None:
        cached_content = (
            getattr(cache_config, "cached_content", None) if cache_config else None
        )
        if cached_content:
            # Mutually exclusive with tools/system_instruction on the request.
            return types.GenerateContentConfig(cached_content=cached_content)
        if not system_instruction and not tools:
            return None
        config = types.GenerateContentConfig()
        if system_instruction:
            config.system_instruction = system_instruction
        if tools:
            config.tools = tools
        return config

    @staticmethod
    def _usage_from_metadata(usage: object | None) -> ChatUsage:
        def _token_count(name: str) -> int:
            value = getattr(usage, name, 0) if usage else 0
            if value is None:
                return 0
            try:
                return int(value)
            except (TypeError, ValueError):
                return 0

        prompt_tokens = _token_count("prompt_token_count")
        completion_tokens = _token_count("candidates_token_count")
        total_tokens = _token_count("total_token_count")
        cached_tokens = _token_count("cached_content_token_count")
        thinking_tokens = _token_count("thoughts_token_count")
        return ChatUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            input_tokens=max(prompt_tokens - cached_tokens, 0),
            output_tokens=completion_tokens,
            cached_tokens=cached_tokens,
            thinking_tokens=thinking_tokens,
        )

    def _contents_from_messages(self, messages: list[dict]) -> list[dict]:
        contents: list[dict] = []
        for message in messages:
            role = message.get("role")
            if role == "system":
                continue
            if role == "assistant":
                role = "model"
            tool_calls = message.get("tool_calls")
            if tool_calls:
                parts = self._model_parts_for_assistant_tool_message(message)
                if parts:
                    contents.append({"role": "model", "parts": parts})
                continue
            if role == "tool":
                fn_response_part = {
                    "function_response": {
                        "name": message.get("name"),
                        "response": {"content": message.get("content", "")},
                        "id": message.get("tool_call_id"),
                    }
                }
                if (
                    contents
                    and contents[-1].get("role") == "user"
                    and contents[-1].get("parts")
                    and all("function_response" in p for p in contents[-1]["parts"])
                ):
                    contents[-1]["parts"].append(fn_response_part)
                else:
                    contents.append({"role": "user", "parts": [fn_response_part]})
                continue
            content = message.get("content")
            if content is None:
                continue
            if isinstance(content, list):
                parts: list[dict] = []
                for part in content:
                    if part.get("type") == "text":
                        text = part.get("text")
                        if text:
                            parts.append({"text": text})
                    elif part.get("type") == "image_url":
                        url = part.get("image_url", {}).get("url", "")
                        if url.startswith("data:") and ";base64," in url:
                            header, data = url.split(";base64,", 1)
                            mime_type = header.replace("data:", "")
                            if data:
                                parts.append(
                                    {
                                        "inline_data": {
                                            "mime_type": mime_type,
                                            "data": data,
                                        }
                                    }
                                )
                if parts:
                    contents.append({"role": role, "parts": parts})
            elif isinstance(content, str):
                contents.append({"role": role, "parts": [{"text": content}]})
        return contents

    def _tool_calls_from_response(self, response: object) -> list[ChatToolCall]:
        tool_calls: list[ChatToolCall] = []
        for candidate in getattr(response, "candidates", []) or []:
            content = getattr(candidate, "content", None)
            for part in getattr(content, "parts", []) or []:
                function_call = getattr(part, "function_call", None)
                if not function_call:
                    continue
                thought_signature = self._extract_thought_signature(part, function_call)
                if not thought_signature:
                    self.logger.info(
                        "Gemini tool call missing thought_signature for %s",
                        getattr(function_call, "name", ""),
                    )
                tool_calls.append(
                    ChatToolCall(
                        id=(
                            getattr(function_call, "id", None)
                            or getattr(function_call, "name", "")
                        ),
                        name=function_call.name,
                        arguments=function_call.args or {},
                        thought_signature=thought_signature,
                    )
                )
        return tool_calls

    def _tool_declarations(self, tools: list[ChatToolSpec]) -> list:
        return [
            types.Tool(
                function_declarations=[
                    types.FunctionDeclaration(
                        name=tool.name,
                        description=tool.description,
                        parameters=tool.parameters,
                    )
                ]
            )
            for tool in tools
        ]

    async def _generate_content_stream_with_cache_fallback(
        self,
        *,
        model: str,
        contents: list[dict],
        system_instruction: str | None,
        tools: list | None = None,
    ):
        contents_to_send, cache_config = self._maybe_cached_content_config(
            model,
            contents,
            system_instruction=system_instruction,
            tools=tools,
        )
        config = self._build_config(
            system_instruction=system_instruction,
            cache_config=cache_config,
            tools=tools,
        )
        try:
            stream = await self.client.aio.models.generate_content_stream(
                model=model,
                contents=contents_to_send,
                config=config,
            )
            async for chunk in stream:
                yield chunk
            return
        except Exception as exc:
            if not (cache_config and getattr(cache_config, "cached_content", None)):
                raise
            self.logger.error(
                "Gemini generate_content_stream rejected cached_content, retrying without cache: %s",
                exc,
                exc_info=True,
            )
            fallback_config = self._build_config(
                system_instruction=system_instruction,
                tools=tools,
            )
            stream = await self.client.aio.models.generate_content_stream(
                model=model,
                contents=contents,
                config=fallback_config,
            )
            async for chunk in stream:
                yield chunk

    async def chat(self, model: str, messages: list[dict]) -> ChatResponse:
        def _run() -> ChatResponse:
            system_instruction = self._extract_system_instruction(messages)
            contents = self._contents_from_messages(messages)
            contents_to_send, cache_config = self._maybe_cached_content_config(
                model,
                contents,
                system_instruction=system_instruction,
            )
            config = self._build_config(
                system_instruction=system_instruction,
                cache_config=cache_config,
            )
            try:
                response = self.client.models.generate_content(
                    model=model,
                    contents=contents_to_send,
                    config=config,
                )
            except Exception as exc:
                if cache_config and getattr(cache_config, "cached_content", None):
                    self.logger.error(
                        "Gemini generate_content rejected cached_content, retrying without cache: %s",
                        exc,
                        exc_info=True,
                    )
                    fallback_config = self._build_config(
                        system_instruction=system_instruction,
                    )
                    response = self.client.models.generate_content(
                        model=model,
                        contents=contents,
                        config=fallback_config,
                    )
                else:
                    raise
            text = self._extract_response_text(response)
            usage = getattr(response, "usage_metadata", None)
            return ChatResponse(
                content=text,
                reasoning_content=self._extract_reasoning_content(response),
                usage=self._usage_from_metadata(usage),
            )

        return await anyio.to_thread.run_sync(_run)

    async def chat_stream(self, model: str, messages: list[dict]):
        system_instruction = self._extract_system_instruction(messages)
        contents = self._contents_from_messages(messages)
        usage_sent = False
        async for chunk in self._generate_content_stream_with_cache_fallback(
            model=model,
            contents=contents,
            system_instruction=system_instruction,
        ):
            text = self._extract_response_text(chunk)
            if text:
                yield ChatStreamChunk(content=text)
            reasoning = self._extract_reasoning_content(chunk)
            if reasoning:
                yield ChatStreamChunk(reasoning_content=reasoning)
            usage = getattr(chunk, "usage_metadata", None)
            if usage is not None:
                usage_sent = True
                yield ChatStreamChunk(usage=self._usage_from_metadata(usage))
        if not usage_sent:
            yield ChatStreamChunk(usage=ChatUsage(0, 0, 0, 0, 0, 0, 0))

    async def chat_with_tools(
        self,
        model: str,
        messages: list[dict],
        tools: list[ChatToolSpec],
        tool_choice: object | None = None,
    ) -> ChatResponse:
        def _run() -> ChatResponse:
            system_instruction = self._extract_system_instruction(messages)
            contents = self._contents_from_messages(messages)
            tool_declarations = self._tool_declarations(tools)
            contents_to_send, cache_config = self._maybe_cached_content_config(
                model,
                contents,
                system_instruction=system_instruction,
                tools=tool_declarations,
            )
            config = self._build_config(
                system_instruction=system_instruction,
                cache_config=cache_config,
                tools=tool_declarations,
            )
            try:
                response = self.client.models.generate_content(
                    model=model,
                    contents=contents_to_send,
                    config=config,
                )
            except Exception as exc:
                if cache_config and getattr(cache_config, "cached_content", None):
                    self.logger.error(
                        "Gemini generate_content rejected cached_content, retrying without cache: %s",
                        exc,
                        exc_info=True,
                    )
                    fallback_config = self._build_config(
                        system_instruction=system_instruction,
                        tools=tool_declarations,
                    )
                    response = self.client.models.generate_content(
                        model=model,
                        contents=contents,
                        config=fallback_config,
                    )
                else:
                    raise
            text = self._extract_response_text(response)
            tool_calls = self._tool_calls_from_response(response)
            usage = getattr(response, "usage_metadata", None)
            return ChatResponse(
                content=text,
                reasoning_content=self._extract_reasoning_content(response),
                usage=self._usage_from_metadata(usage),
                tool_calls=tool_calls or None,
                finish_reason=None,
            )

        return await anyio.to_thread.run_sync(_run)

    async def chat_stream_with_tools(
        self,
        model: str,
        messages: list[dict],
        tools: list[ChatToolSpec],
        tool_choice: object | None = None,
    ):
        _ = tool_choice
        system_instruction = self._extract_system_instruction(messages)
        contents = self._contents_from_messages(messages)
        tool_declarations = self._tool_declarations(tools)
        pending_tool_calls: dict[str, ChatToolCall] = {}
        signaled_tool_calls = False
        usage_sent = False

        async for chunk in self._generate_content_stream_with_cache_fallback(
            model=model,
            contents=contents,
            system_instruction=system_instruction,
            tools=tool_declarations,
        ):
            text = self._extract_response_text(chunk)
            if text:
                yield ChatStreamChunk(content=text)
            reasoning = self._extract_reasoning_content(chunk)
            if reasoning:
                yield ChatStreamChunk(reasoning_content=reasoning)
            for call in self._tool_calls_from_response(chunk):
                key = f"{call.id}:{call.name}"
                if key not in pending_tool_calls and not signaled_tool_calls:
                    signaled_tool_calls = True
                    yield ChatStreamChunk(finish_reason="tool_calls")
                pending_tool_calls[key] = call
            usage = getattr(chunk, "usage_metadata", None)
            if usage is not None:
                usage_sent = True
                yield ChatStreamChunk(usage=self._usage_from_metadata(usage))

        if not usage_sent:
            yield ChatStreamChunk(usage=ChatUsage(0, 0, 0, 0, 0, 0, 0))
        tool_calls = list(pending_tool_calls.values())
        yield ChatStreamChunk(
            tool_calls=tool_calls or None,
            finish_reason="tool_calls" if tool_calls else "stop",
        )

    async def chat_grounded(self, model: str, messages: list[dict]) -> ChatResponse:
        def _run() -> ChatResponse:
            system_instruction = self._extract_system_instruction(messages)
            contents: list[dict] = []
            for message in messages:
                role = message.get("role")
                if role == "assistant":
                    role = "model"
                if role not in {"user", "model"}:
                    continue
                content = message.get("content")
                if content is None:
                    continue
                if isinstance(content, list):
                    parts: list[dict] = []
                    for part in content:
                        if part.get("type") == "text":
                            text = part.get("text")
                            if text:
                                parts.append({"text": text})
                        elif part.get("type") == "image_url":
                            url = part.get("image_url", {}).get("url", "")
                            if url.startswith("data:") and ";base64," in url:
                                header, data = url.split(";base64,", 1)
                                mime_type = header.replace("data:", "")
                                if data:
                                    parts.append(
                                        {
                                            "inline_data": {
                                                "mime_type": mime_type,
                                                "data": data,
                                            }
                                        }
                                    )
                    if parts:
                        contents.append({"role": role, "parts": parts})
                elif isinstance(content, str):
                    contents.append({"role": role, "parts": [{"text": content}]})
            google_search_tools = [{"google_search": {}}]
            contents_to_send, cache_config = self._maybe_cached_content_config(
                model,
                contents,
                system_instruction=system_instruction,
                tools=google_search_tools,
            )
            config = self._build_config(
                system_instruction=system_instruction,
                cache_config=cache_config,
                tools=google_search_tools,
            )
            try:
                response = self.client.models.generate_content(
                    model=model,
                    contents=contents_to_send,
                    config=config,
                )
            except Exception as exc:
                if cache_config and getattr(cache_config, "cached_content", None):
                    self.logger.error(
                        "Gemini generate_content rejected cached_content, retrying without cache: %s",
                        exc,
                        exc_info=True,
                    )
                    fallback_config = self._build_config(
                        system_instruction=system_instruction,
                        tools=google_search_tools,
                    )
                    response = self.client.models.generate_content(
                        model=model,
                        contents=contents,
                        config=fallback_config,
                    )
                else:
                    raise
            text = getattr(response, "text", "") or ""
            sources = _extract_gemini_sources(response)
            usage = getattr(response, "usage_metadata", None)
            return ChatResponse(
                content=text,
                usage=self._usage_from_metadata(usage),
                sources=sources or None,
            )

        return await anyio.to_thread.run_sync(_run)


def _extract_gemini_sources(response) -> list[str]:
    sources: list[str] = []
    candidates = getattr(response, "candidates", []) or []
    for candidate in candidates:
        grounding = getattr(candidate, "grounding_metadata", None)
        if grounding:
            chunks = getattr(grounding, "grounding_chunks", None) or getattr(
                grounding, "groundingChunks", None
            )
            if chunks:
                for chunk in chunks:
                    web = getattr(chunk, "web", None) or (chunk.get("web") if isinstance(chunk, dict) else None)
                    if web:
                        url = getattr(web, "uri", None) or getattr(web, "url", None)
                        if not url and isinstance(web, dict):
                            url = web.get("uri") or web.get("url")
                        if url:
                            sources.append(url)
        citations = getattr(candidate, "citation_metadata", None) or getattr(
            candidate, "citationMetadata", None
        )
        if citations:
            citation_sources = getattr(citations, "citations", None) or getattr(
                citations, "citationSources", None
            )
            if citation_sources:
                for citation in citation_sources:
                    url = getattr(citation, "uri", None) or getattr(citation, "url", None)
                    if not url and isinstance(citation, dict):
                        url = citation.get("uri") or citation.get("url")
                    if url:
                        sources.append(url)
    return list(dict.fromkeys(sources))
