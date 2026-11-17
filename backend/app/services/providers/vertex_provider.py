import logging

from google import genai
from google.genai import types

from app.services.providers.anthropic_provider import AnthropicProvider
from app.services.providers.gemini_provider import GeminiProvider
from app.services.providers.vertex_config import (
    is_vertex_claude_model,
    normalize_vertex_model_name,
    resolve_vertex_runtime_config,
)
from app.services.providers.vertex_claude import build_async_anthropic_vertex


class VertexProvider(GeminiProvider):
    def __init__(
        self,
        *,
        api_key: str | None = None,
        prompt_cache_key: str | None = None,
        prompt_cache_enabled: bool = True,
        config: dict | None = None,
    ) -> None:
        self.logger = logging.getLogger(__name__)
        self.prompt_cache_key = prompt_cache_key
        self.prompt_cache_enabled = prompt_cache_enabled
        self._vertex_runtime = resolve_vertex_runtime_config(config)
        self._anthropic_chat: AnthropicProvider | None = None

        client_kwargs: dict = {
            "vertexai": True,
            "http_options": types.HttpOptions(timeout=180_000),
        }
        if self._vertex_runtime.project:
            client_kwargs["project"] = self._vertex_runtime.project
        if self._vertex_runtime.location:
            client_kwargs["location"] = self._vertex_runtime.location
        if self._vertex_runtime.credentials is not None:
            client_kwargs["credentials"] = self._vertex_runtime.credentials

        if self._vertex_runtime.project and self._vertex_runtime.location:
            self.logger.info(
                "Initializing Vertex AI client for project %s, location %s, auth_mode=%s",
                self._vertex_runtime.project,
                self._vertex_runtime.location,
                self._vertex_runtime.auth_mode,
            )
            self.client = genai.Client(**client_kwargs)
        else:
            self.logger.warning(
                "Vertex AI project/location not configured (checked config_json, "
                "GEMINI_VERTEX_JSON, and GOOGLE_VERTEX_*), falling back to default client init"
            )
            self.client = genai.Client(**client_kwargs)

    def _claude_chat(self) -> AnthropicProvider:
        if self._anthropic_chat is None:
            client = build_async_anthropic_vertex(self._vertex_runtime)
            self._anthropic_chat = AnthropicProvider(client=client)
        return self._anthropic_chat

    def _resolved_model(self, model: str) -> str:
        return normalize_vertex_model_name(model)

    async def chat(
        self,
        model: str,
        messages: list[dict],
        *,
        completion_params: dict | None = None,
    ):
        resolved = self._resolved_model(model)
        if is_vertex_claude_model(resolved):
            return await self._claude_chat().chat(
                resolved, messages, completion_params=completion_params
            )
        return await super().chat(
            resolved, messages, completion_params=completion_params
        )

    async def chat_stream(
        self,
        model: str,
        messages: list[dict],
        *,
        completion_params: dict | None = None,
    ):
        resolved = self._resolved_model(model)
        if is_vertex_claude_model(resolved):
            async for chunk in self._claude_chat().chat_stream(
                resolved, messages, completion_params=completion_params
            ):
                yield chunk
            return
        async for chunk in super().chat_stream(
            resolved, messages, completion_params=completion_params
        ):
            yield chunk

    async def chat_with_tools(
        self,
        model: str,
        messages: list[dict],
        tools,
        tool_choice: object | None = None,
        *,
        completion_params: dict | None = None,
    ):
        resolved = self._resolved_model(model)
        if is_vertex_claude_model(resolved):
            return await self._claude_chat().chat_with_tools(
                resolved,
                messages,
                tools,
                tool_choice,
                completion_params=completion_params,
            )
        return await super().chat_with_tools(
            resolved,
            messages,
            tools,
            tool_choice,
            completion_params=completion_params,
        )

    async def chat_stream_with_tools(
        self,
        model: str,
        messages: list[dict],
        tools,
        tool_choice: object | None = None,
        *,
        completion_params: dict | None = None,
    ):
        resolved = self._resolved_model(model)
        if is_vertex_claude_model(resolved):
            async for chunk in self._claude_chat().chat_stream_with_tools(
                resolved,
                messages,
                tools,
                tool_choice,
                completion_params=completion_params,
            ):
                yield chunk
            return
        async for chunk in super().chat_stream_with_tools(
            resolved,
            messages,
            tools,
            tool_choice,
            completion_params=completion_params,
        ):
            yield chunk