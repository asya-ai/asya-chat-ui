from __future__ import annotations

import pytest

from app.services.langchain_runtime.model_adapter import chat_with_langchain
from app.services.providers.base import ChatResponse, ChatUsage


@pytest.mark.anyio
async def test_chat_with_langchain_awaits_async_provider() -> None:
    class Provider:
        async def chat(self, model: str, messages: list[dict]) -> ChatResponse:
            assert model == "test-model"
            assert messages == [{"role": "user", "content": "hi"}]
            return ChatResponse(content="ok", usage=ChatUsage(1, 2, 3, 1, 2, 0, 0))

    response = await chat_with_langchain(Provider(), "test-model", [{"role": "user", "content": "hi"}])
    assert response.content == "ok"
