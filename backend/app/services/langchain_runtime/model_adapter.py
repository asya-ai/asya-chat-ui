from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from langchain_core.runnables import RunnableLambda

from app.services.providers.base import ChatResponse, ChatStreamChunk


async def chat_with_langchain(provider: Any, model_name: str, messages: list[dict]) -> ChatResponse:
    runnable = RunnableLambda(lambda payload: provider.chat(model_name, payload))
    response = await runnable.ainvoke(messages)
    return response


async def chat_stream_with_langchain(
    provider: Any,
    model_name: str,
    messages: list[dict],
) -> AsyncIterator[ChatStreamChunk]:
    if not hasattr(provider, "chat_stream"):
        response = await chat_with_langchain(provider, model_name, messages)
        yield ChatStreamChunk(content=response.content, usage=response.usage)
        return
    stream_runnable = RunnableLambda(lambda payload: provider.chat_stream(model_name, payload))
    stream = await stream_runnable.ainvoke(messages)
    async for chunk in stream:
        yield chunk
