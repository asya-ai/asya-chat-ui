from __future__ import annotations

import pytest

from app.services.providers.openai_provider import (
    OpenAIProvider,
    _is_prompt_cache_param_error,
    _is_unsupported_reasoning_effort_error,
)


class _FakeChatCompletions:
    def __init__(self, parent: "_FakeOpenAIClient") -> None:
        self._parent = parent

    async def create(self, **payload):
        self._parent.payloads.append(dict(payload))
        if len(self._parent.payloads) == 1:
            raise ValueError(
                "Unsupported value: 'reasoning_effort' does not support 'high' with this model. "
                "Supported values are: 'medium'."
            )
        return object()


class _FakeChat:
    def __init__(self, parent: "_FakeOpenAIClient") -> None:
        self.completions = _FakeChatCompletions(parent)


class _FakeOpenAIClient:
    def __init__(self) -> None:
        self.payloads: list[dict] = []
        self.chat = _FakeChat(self)

    def with_options(self, **kwargs):
        return self


def test_detects_reasoning_effort_errors() -> None:
    exc = ValueError(
        "Unsupported value: 'reasoning_effort' does not support 'high' with this model."
    )

    assert _is_unsupported_reasoning_effort_error(exc) is True
    assert _is_prompt_cache_param_error(exc) is False


@pytest.mark.asyncio
async def test_chat_completion_retry_strips_only_unsupported_reasoning_effort() -> None:
    provider = OpenAIProvider(api_key="test-key")
    fake_client = _FakeOpenAIClient()
    provider.client = fake_client
    payload = {
        "model": "gpt-test",
        "messages": [{"role": "user", "content": "hello"}],
        "reasoning_effort": "high",
        "prompt_cache_key": "chat:123",
        "prompt_cache_retention": "24h",
    }

    result = await provider._create_chat_completion(payload)

    assert result is not None
    assert len(fake_client.payloads) == 2
    assert fake_client.payloads[0]["reasoning_effort"] == "high"
    assert "reasoning_effort" not in fake_client.payloads[1]
    assert fake_client.payloads[1]["prompt_cache_key"] == "chat:123"
    assert fake_client.payloads[1]["prompt_cache_retention"] == "24h"
