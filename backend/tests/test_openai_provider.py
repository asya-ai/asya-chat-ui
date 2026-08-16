from __future__ import annotations

import pytest

from app.services.providers.openai_provider import (
    OpenAIProvider,
    _coalesce_usage_tokens,
    _extract_usage_details,
    _is_prompt_cache_param_error,
    _is_unsupported_reasoning_effort_error,
    _usage_chunk_from_response_usage,
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


def test_extract_usage_details_from_chat_completions() -> None:
    from types import SimpleNamespace

    usage = SimpleNamespace(
        prompt_tokens_details=SimpleNamespace(cached_tokens=120),
        completion_tokens_details=SimpleNamespace(reasoning_tokens=40),
    )

    assert _extract_usage_details(usage) == (120, 40)


def test_extract_usage_details_from_responses_api() -> None:
    from types import SimpleNamespace

    usage = SimpleNamespace(
        input_tokens=500,
        output_tokens=80,
        total_tokens=580,
        prompt_tokens=0,
        completion_tokens=0,
        input_tokens_details=SimpleNamespace(cached_tokens=300),
        output_tokens_details=SimpleNamespace(reasoning_tokens=25),
    )

    assert _extract_usage_details(usage) == (300, 25)
    prompt, completion, total, input_tokens, output_tokens = _coalesce_usage_tokens(usage)
    assert prompt == 500
    assert completion == 80
    assert total == 580
    assert input_tokens == 200  # excludes cached
    assert output_tokens == 80

    chunk = _usage_chunk_from_response_usage(usage)
    assert chunk.usage is not None
    assert chunk.usage.cached_tokens == 300
    assert chunk.usage.thinking_tokens == 25
    assert chunk.usage.input_tokens == 200


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


def test_prefer_responses_api_comes_from_constructor() -> None:
    provider = OpenAIProvider(api_key="test-key", prefer_responses_api=True)
    assert provider._should_use_responses("any-model") is True
    assert provider.consume_responses_api_discovery() is False


def test_mark_responses_only_sets_discovery_flag() -> None:
    provider = OpenAIProvider(api_key="test-key", prefer_responses_api=False)
    assert provider._should_use_responses("gpt-whatever") is False
    provider._mark_responses_only_model("gpt-whatever")
    assert provider._should_use_responses("gpt-whatever") is True
    assert provider.consume_responses_api_discovery() is True
    assert provider.consume_responses_api_discovery() is False


@pytest.mark.asyncio
async def test_response_stream_chunks_emit_text_and_tool_calls() -> None:
    from types import SimpleNamespace

    from app.services.providers.openai_provider import _iter_response_stream_chunks

    async def _fake_stream():
        yield SimpleNamespace(type="response.output_text.delta", delta="Hello ")
        yield SimpleNamespace(type="response.output_text.delta", delta="world")
        yield SimpleNamespace(
            type="response.output_item.added",
            output_index=0,
            item=SimpleNamespace(
                type="function_call",
                call_id="call-1",
                name="echo_tool",
                arguments="",
            ),
        )
        yield SimpleNamespace(
            type="response.function_call_arguments.delta",
            output_index=0,
            delta='{"text":"hi"}',
        )
        yield SimpleNamespace(
            type="response.output_item.done",
            output_index=0,
            item=SimpleNamespace(
                type="function_call",
                call_id="call-1",
                name="echo_tool",
                arguments='{"text":"hi"}',
            ),
        )
        yield SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(
                usage=SimpleNamespace(
                    input_tokens=10,
                    output_tokens=5,
                    total_tokens=15,
                    prompt_tokens=0,
                    completion_tokens=0,
                    input_tokens_details=SimpleNamespace(cached_tokens=7),
                    output_tokens_details=SimpleNamespace(reasoning_tokens=2),
                )
            ),
        )

    chunks = [chunk async for chunk in _iter_response_stream_chunks(_fake_stream())]
    assert [chunk.content for chunk in chunks if chunk.content] == ["Hello ", "world"]
    assert any(chunk.finish_reason == "tool_calls" for chunk in chunks)
    final_tools = next(chunk.tool_calls for chunk in chunks if chunk.tool_calls is not None)
    assert final_tools[0].id == "call-1"
    assert final_tools[0].name == "echo_tool"
    assert final_tools[0].arguments == {"text": "hi"}
    usage_chunk = next(chunk for chunk in chunks if chunk.usage is not None)
    assert usage_chunk.usage.input_tokens == 3
    assert usage_chunk.usage.output_tokens == 5
    assert usage_chunk.usage.cached_tokens == 7
    assert usage_chunk.usage.thinking_tokens == 2


class _RecordingCompletions:
    def __init__(self, parent: "_RecordingOpenAIClient") -> None:
        self._parent = parent

    async def create(self, **payload):
        self._parent.payloads.append(dict(payload))
        return object()


class _RecordingChat:
    def __init__(self, parent: "_RecordingOpenAIClient") -> None:
        self.completions = _RecordingCompletions(parent)


class _RecordingOpenAIClient:
    def __init__(self) -> None:
        self.payloads: list[dict] = []
        self.chat = _RecordingChat(self)

    def with_options(self, **kwargs):
        return self


@pytest.mark.asyncio
async def test_chat_completion_includes_openrouter_extra_body() -> None:
    provider = OpenAIProvider(
        api_key="test-key",
        extra_body={
            "provider": {"only": ["google-vertex/eu"], "allow_fallbacks": False},
        },
    )
    fake_client = _RecordingOpenAIClient()
    provider.client = fake_client

    await provider._create_chat_completion(
        {"model": "google/gemini-2.5-pro", "messages": [{"role": "user", "content": "hi"}]}
    )

    assert fake_client.payloads[0]["extra_body"] == {
        "provider": {"only": ["google-vertex/eu"], "allow_fallbacks": False},
    }


def test_openrouter_endpoint_sets_provider_routing() -> None:
    from app.services.providers.registry import get_provider

    provider = get_provider(
        "openrouter",
        api_key="test-key",
        openrouter_endpoint="google-vertex/eu",
    )
    assert provider.extra_body == {
        "provider": {"only": ["google-vertex/eu"], "allow_fallbacks": False},
    }
