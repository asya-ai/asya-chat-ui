from __future__ import annotations

import anyio

from anthropic import AnthropicVertex, AsyncAnthropicVertex

from app.services.providers.vertex_config import (
    VertexRuntimeConfig,
    is_vertex_claude_model,
    normalize_vertex_model_name,
)


def build_async_anthropic_vertex(
    runtime: VertexRuntimeConfig,
) -> AsyncAnthropicVertex:
    if not runtime.project:
        raise ValueError("Vertex project is not configured")
    kwargs: dict = {
        "region": runtime.location,
        "project_id": runtime.project,
        "timeout": 180.0,
    }
    if runtime.credentials is not None:
        kwargs["credentials"] = runtime.credentials
    return AsyncAnthropicVertex(**kwargs)


def build_sync_anthropic_vertex(runtime: VertexRuntimeConfig) -> AnthropicVertex:
    if not runtime.project:
        raise ValueError("Vertex project is not configured")
    kwargs: dict = {
        "region": runtime.location,
        "project_id": runtime.project,
        "timeout": 180.0,
    }
    if runtime.credentials is not None:
        kwargs["credentials"] = runtime.credentials
    return AnthropicVertex(**kwargs)


def ping_vertex_claude_model(runtime: VertexRuntimeConfig, model_name: str) -> None:
    client = build_sync_anthropic_vertex(runtime)
    model = normalize_vertex_model_name(model_name)
    if not is_vertex_claude_model(model):
        raise ValueError(f"Not a Claude model: {model_name}")
    client.messages.create(
        model=model,
        max_tokens=1,
        messages=[{"role": "user", "content": "ping"}],
    )


async def ping_vertex_claude_model_async(
    runtime: VertexRuntimeConfig, model_name: str
) -> None:
    await anyio.to_thread.run_sync(ping_vertex_claude_model, runtime, model_name)