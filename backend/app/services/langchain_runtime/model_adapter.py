from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from app.services.providers.base import ChatResponse, ChatStreamChunk


async def chat_with_langchain(provider: Any, model_name: str, messages: list[dict]) -> ChatResponse:
    return await provider.chat(model_name, messages)


async def chat_stream_with_langchain(
    provider: Any,
    model_name: str,
    messages: list[dict],
) -> AsyncIterator[ChatStreamChunk]:
    if not hasattr(provider, "chat_stream"):
        response = await chat_with_langchain(provider, model_name, messages)
        yield ChatStreamChunk(content=response.content, usage=response.usage)
        return
    stream = await provider.chat_stream(model_name, messages)
    async for chunk in stream:
        yield chunk
