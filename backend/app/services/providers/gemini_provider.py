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
        self, *, api_key: str | None = None, prompt_cache_key: str | None = None
    ) -> None:
        self.client = genai.Client(api_key=api_key or settings.gemini_api_key)
        self.logger = logging.getLogger(__name__)
        self.prompt_cache_key = prompt_cache_key

    def _cache_key_for_contents(self, model: str, contents: list[dict]) -> str:
        base = json.dumps(contents, sort_keys=True, ensure_ascii=False)
        prefix = self.prompt_cache_key or ""
        digest = hashlib.sha256(f"{model}:{prefix}:{base}".encode("utf-8")).hexdigest()
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

    def _maybe_cached_content_config(
        self, model: str, contents: list[dict]
    ) -> tuple[list[dict], types.GenerateContentConfig | None]:
        if not settings.gemini_cached_content_enabled:
            return contents, None
        if len(contents) < 2:
            return contents, None
        prefix = contents[:-1]
        suffix = contents[-1:]
        cache_key = self._cache_key_for_contents(model, prefix)
        cached_name = self._get_cached_content_name(cache_key)
        if not cached_name:
            try:
                cached = self.client.caches.create(
                    model=model,
                    config=types.CreateCachedContentConfig(
                        contents=prefix,
                        ttl=f"{settings.gemini_cached_content_ttl_seconds}s",
                    ),
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
        value = getattr(function_call, "thought_signature", None) or getattr(
            part, "thought_signature", None
        )
        if value:
            if isinstance(value, bytes):
                return base64.b64encode(value).decode("ascii")
            if isinstance(value, str):
                return value
            try:
                return str(value)
            except Exception:
                return None
        for attr in ("model_dump", "dict"):
            fn = getattr(function_call, attr, None)
            if callable(fn):
                data = fn()
                if isinstance(data, dict):
                    raw = data.get("thought_signature") or data.get("thoughtSignature")
                    if isinstance(raw, bytes):
                        return base64.b64encode(raw).decode("ascii")
                    if isinstance(raw, str):
                        return raw
                    if raw is not None:
                        return str(raw)
        return None

    async def chat(self, model: str, messages: list[dict]) -> ChatResponse:
        def _run() -> ChatResponse:
            contents: list[dict] = []
            for message in messages:
                role = message.get("role")
                if role == "assistant":
                    role = "model"
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
            contents_to_send, cache_config = self._maybe_cached_content_config(
                model, contents
            )
            try:
                response = self.client.models.generate_content(
                    model=model,
                    contents=contents_to_send,
                    config=cache_config,
                )
            except Exception as exc:
                if cache_config and getattr(cache_config, "cached_content", None):
                    self.logger.error(
                        "Gemini generate_content rejected cached_content, retrying without cache: %s",
                        exc,
                        exc_info=True,
                    )
                    response = self.client.models.generate_content(
                        model=model,
                        contents=contents,
                    )
                else:
                    raise
            text = getattr(response, "text", "") or ""
            usage = getattr(response, "usage_metadata", None)
            prompt_tokens = getattr(usage, "prompt_token_count", 0) if usage else 0
            completion_tokens = (
                getattr(usage, "candidates_token_count", 0) if usage else 0
            )
            total_tokens = getattr(usage, "total_token_count", 0) if usage else 0
            return ChatResponse(
                content=text,
                usage=ChatUsage(
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    input_tokens=prompt_tokens,
                    output_tokens=completion_tokens,
                    cached_tokens=0,
                    thinking_tokens=0,
                ),
            )

        return await anyio.to_thread.run_sync(_run)

    async def chat_stream(self, model: str, messages: list[dict]):
        response = await self.chat(model, messages)
        yield ChatStreamChunk(content=response.content)
        yield ChatStreamChunk(usage=response.usage)

    async def chat_with_tools(
        self,
        model: str,
        messages: list[dict],
        tools: list[ChatToolSpec],
        tool_choice: object | None = None,
    ) -> ChatResponse:
        def _run() -> ChatResponse:
            contents: list[dict] = []
            for message in messages:
                role = message.get("role")
                if role == "assistant":
                    role = "model"
                tool_calls = message.get("tool_calls")
                if tool_calls:
                    parts = []
                    for call in tool_calls:
                        thought_signature = call.get("thought_signature") or call.get(
                            "thoughtSignature"
                        )
                        arguments = call.get("arguments", {})
                        if isinstance(arguments, dict) and "thought_signature" in arguments:
                            arguments = {
                                key: value
                                for key, value in arguments.items()
                                if key != "thought_signature"
                            }
                        part = {
                            "function_call": {
                                "name": call.get("name"),
                                "args": arguments,
                            }
                        }
                        if thought_signature:
                            part["thought_signature"] = thought_signature
                        parts.append(part)
                    contents.append({"role": "model", "parts": parts})
                    continue
                if role == "tool":
                    parts = [
                        {
                            "function_response": {
                                "name": message.get("name"),
                                "response": {"content": message.get("content", "")},
                                "id": message.get("tool_call_id"),
                            }
                        }
                    ]
                    contents.append({"role": "user", "parts": parts})
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

            tool_declarations = [
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
            contents_to_send, cache_config = self._maybe_cached_content_config(
                model, contents
            )
            config = types.GenerateContentConfig(tools=tool_declarations)
            if cache_config and getattr(cache_config, "cached_content", None):
                config.cached_content = cache_config.cached_content
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
                    response = self.client.models.generate_content(
                        model=model,
                        contents=contents,
                        config=types.GenerateContentConfig(tools=tool_declarations),
                    )
                else:
                    raise
            text = getattr(response, "text", "") or ""
            tool_calls: list[ChatToolCall] = []
            for candidate in getattr(response, "candidates", []) or []:
                content = getattr(candidate, "content", None)
                for part in getattr(content, "parts", []) or []:
                    function_call = getattr(part, "function_call", None)
                    if function_call:
                        thought_signature = self._extract_thought_signature(
                            part, function_call
                        )
                        if not thought_signature:
                            self.logger.info(
                                "Gemini tool call missing thought_signature for %s",
                                getattr(function_call, "name", ""),
                            )
                        tool_calls.append(
                            ChatToolCall(
                                id=function_call.name,
                                name=function_call.name,
                                arguments=function_call.args or {},
                                thought_signature=thought_signature,
                            )
                        )
            usage = getattr(response, "usage_metadata", None)
            prompt_tokens = getattr(usage, "prompt_token_count", 0) if usage else 0
            completion_tokens = (
                getattr(usage, "candidates_token_count", 0) if usage else 0
            )
            total_tokens = getattr(usage, "total_token_count", 0) if usage else 0
            return ChatResponse(
                content=text,
                usage=ChatUsage(
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    input_tokens=prompt_tokens,
                    output_tokens=completion_tokens,
                    cached_tokens=0,
                    thinking_tokens=0,
                ),
                tool_calls=tool_calls or None,
                finish_reason=None,
            )

        return await anyio.to_thread.run_sync(_run)

    async def chat_grounded(self, model: str, messages: list[dict]) -> ChatResponse:
        def _run() -> ChatResponse:
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
            contents_to_send, cache_config = self._maybe_cached_content_config(
                model, contents
            )
            config = types.GenerateContentConfig(tools=[{"google_search": {}}])
            if cache_config and getattr(cache_config, "cached_content", None):
                config.cached_content = cache_config.cached_content
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
                    response = self.client.models.generate_content(
                        model=model,
                        contents=contents,
                        config=types.GenerateContentConfig(
                            tools=[{"google_search": {}}]
                        ),
                    )
                else:
                    raise
            text = getattr(response, "text", "") or ""
            sources = _extract_gemini_sources(response)
            usage = getattr(response, "usage_metadata", None)
            prompt_tokens = getattr(usage, "prompt_token_count", 0) if usage else 0
            completion_tokens = (
                getattr(usage, "candidates_token_count", 0) if usage else 0
            )
            total_tokens = getattr(usage, "total_token_count", 0) if usage else 0
            return ChatResponse(
                content=text,
                usage=ChatUsage(
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    input_tokens=prompt_tokens,
                    output_tokens=completion_tokens,
                    cached_tokens=0,
                    thinking_tokens=0,
                ),
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
