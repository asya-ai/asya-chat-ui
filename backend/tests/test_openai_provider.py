from __future__ import annotations

import pytest

from app.services.providers.openai_provider import (
    OpenAIProvider,
    _coalesce_usage_tokens,
    _extract_usage_details,
    _is_function_tools_with_reasoning_effort_error,
    _is_non_chat_model_error,
    _is_prompt_cache_param_error,
    _is_unsupported_auto_tool_choice_error,
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


def test_detects_function_tools_with_reasoning_effort_errors() -> None:
    exc = ValueError(
        "Error code: 400 - {'error': {'message': \"Function tools with reasoning_effort "
        "are not supported for gpt-6-sol in /v1/chat/completions. To use function tools, "
        "use /v1/responses or set reasoning_effort to 'none'.\", "
        "'type': 'invalid_request_error', 'param': 'reasoning_effort', 'code': None}}"
    )

    assert _is_function_tools_with_reasoning_effort_error(exc) is True
    assert _is_unsupported_reasoning_effort_error(exc) is False
    # Prefer /v1/responses so tools can keep reasoning_effort.
    assert _is_non_chat_model_error(exc) is True


def test_detects_unsupported_auto_tool_choice_errors() -> None:
    exc = ValueError(
        '"auto" tool choice requires --enable-auto-tool-choice and --tool-call-parser to be set'
    )

    assert _is_unsupported_auto_tool_choice_error(exc) is True
    assert _is_unsupported_reasoning_effort_error(exc) is False


def test_extract_usage_details_from_chat_completions() -> None:
    from types import SimpleNamespace

    usage = SimpleNamespace(
        prompt_tokens_details=SimpleNamespace(cached_tokens=120),
        completion_tokens_details=SimpleNamespace(reasoning_tokens=40),
    )

    assert _extract_usage_details(usage) == (120, 40)


def test_extract_usage_details_from_dict_shaped_openrouter_usage() -> None:
    usage = {
        "prompt_tokens": 8000,
        "completion_tokens": 50,
        "prompt_tokens_details": {"cached_tokens": 7500, "cache_write_tokens": 0},
        "completion_tokens_details": {"reasoning_tokens": 12},
    }

    assert _extract_usage_details(usage) == (7500, 12)


def test_extract_usage_details_from_anthropic_native_fields() -> None:
    from types import SimpleNamespace

    usage = SimpleNamespace(cache_read_input_tokens=6400, reasoning_tokens=0)
    assert _extract_usage_details(usage) == (6400, 0)


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


class _FakeAutoToolChoiceChatCompletions:
    def __init__(self, parent: "_FakeOpenAIClient") -> None:
        self._parent = parent

    async def create(self, **payload):
        self._parent.payloads.append(dict(payload))
        if len(self._parent.payloads) == 1:
            raise ValueError(
                '"auto" tool choice requires --enable-auto-tool-choice and --tool-call-parser to be set'
            )
        return object()


@pytest.mark.asyncio
async def test_chat_completion_retry_strips_unsupported_auto_tool_choice() -> None:
    provider = OpenAIProvider(api_key="test-key")
    fake_client = _FakeOpenAIClient()
    fake_client.chat.completions = _FakeAutoToolChoiceChatCompletions(fake_client)
    provider.client = fake_client
    payload = {
        "model": "local-model",
        "messages": [{"role": "user", "content": "hello"}],
        "tools": [{"type": "function", "function": {"name": "search", "parameters": {}}}],
        "tool_choice": "auto",
    }

    result = await provider._create_chat_completion(payload)

    assert result is not None
    assert len(fake_client.payloads) == 2
    assert fake_client.payloads[0]["tool_choice"] == "auto"
    assert "tool_choice" not in fake_client.payloads[1]
    assert fake_client.payloads[1]["tools"] == payload["tools"]


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


class _FakeToolsReasoningChatCompletions:
    def __init__(self, parent: "_FakeOpenAIClient") -> None:
        self._parent = parent

    async def create(self, **payload):
        self._parent.payloads.append(dict(payload))
        raise ValueError(
            "Error code: 400 - {'error': {'message': \"Function tools with reasoning_effort "
            "are not supported for gpt-6-sol in /v1/chat/completions. To use function tools, "
            "use /v1/responses or set reasoning_effort to 'none'.\", "
            "'type': 'invalid_request_error', 'param': 'reasoning_effort', 'code': None}}"
        )


@pytest.mark.asyncio
async def test_chat_completion_tools_reasoning_conflict_raises_non_chat() -> None:
    from app.services.providers.openai_provider import NonChatModelError

    provider = OpenAIProvider(api_key="test-key", reasoning_effort="high")
    fake_client = _FakeOpenAIClient()
    fake_client.chat.completions = _FakeToolsReasoningChatCompletions(fake_client)
    provider.client = fake_client
    payload = {
        "model": "gpt-6-sol",
        "messages": [{"role": "user", "content": "hello"}],
        "tools": [{"type": "function", "function": {"name": "search", "parameters": {}}}],
        "tool_choice": "auto",
        "reasoning_effort": "high",
    }

    with pytest.raises(NonChatModelError):
        await provider._create_chat_completion(payload)

    assert len(fake_client.payloads) == 1
    assert fake_client.payloads[0]["reasoning_effort"] == "high"


@pytest.mark.asyncio
async def test_chat_with_tools_preserves_reasoning_effort_on_completions() -> None:
    provider = OpenAIProvider(api_key="test-key", reasoning_effort="high")
    fake_client = _FakeOpenAIClient()
    provider.client = fake_client

    class _Usage:
        prompt_tokens = 1
        completion_tokens = 1
        total_tokens = 2
        input_tokens = 0
        output_tokens = 0

    class _Message:
        content = "ok"
        tool_calls = None
        reasoning_content = None

    class _Choice:
        message = _Message()
        finish_reason = "stop"

    class _Result:
        choices = [_Choice()]
        usage = _Usage()

    async def _create(**payload):
        fake_client.payloads.append(dict(payload))
        return _Result()

    fake_client.chat.completions.create = _create

    from app.services.providers.base import ChatToolSpec

    response = await provider.chat_with_tools(
        "claude-opus",
        [{"role": "user", "content": "hello"}],
        [ChatToolSpec(name="search", description="", parameters={})],
    )

    assert response.content == "ok"
    assert len(fake_client.payloads) == 1
    assert fake_client.payloads[0]["reasoning_effort"] == "high"


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
            type="response.reasoning_summary_text.delta",
            delta="Considering the capital",
            item_id="rs_1",
            summary_index=0,
        )
        yield SimpleNamespace(
            type="response.reasoning_summary_text.delta",
            delta=" of France.",
            item_id="rs_1",
            summary_index=0,
        )
        yield SimpleNamespace(
            type="response.reasoning_summary_text.delta",
            delta="Choosing Paris.",
            item_id="rs_1",
            summary_index=1,
        )
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
    reasoning_chunks = [chunk for chunk in chunks if chunk.reasoning_content]
    assert [chunk.reasoning_content for chunk in reasoning_chunks] == [
        "Considering the capital",
        " of France.",
        "Choosing Paris.",
    ]
    assert [chunk.reasoning_id for chunk in reasoning_chunks] == [
        "reasoning:rs_1:0",
        "reasoning:rs_1:0",
        "reasoning:rs_1:1",
    ]
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


def test_openrouter_anthropic_applies_cache_control() -> None:
    from app.services.providers.openrouter_provider import OpenRouterProvider

    provider = OpenRouterProvider(
        api_key="test-key",
        prompt_cache_key="chat:abc",
        prompt_cache_retention="24h",
        prompt_cache_enabled=True,
    )
    payload: dict = {
        "model": "anthropic/claude-opus-4.5",
        "messages": [{"role": "user", "content": "hi"}],
    }

    provider._apply_prompt_cache(payload)

    assert payload["prompt_cache_key"] == "chat:abc"
    assert "prompt_cache_retention" not in payload
    assert payload["extra_body"] == {
        "session_id": "chat:abc",
        "cache_control": {"type": "ephemeral", "ttl": "1h"},
    }


def test_openrouter_anthropic_default_cache_ttl_is_ephemeral() -> None:
    from app.services.providers.openrouter_provider import OpenRouterProvider

    provider = OpenRouterProvider(
        api_key="test-key",
        prompt_cache_key="chat:abc",
        prompt_cache_enabled=True,
    )
    # Avoid inheriting a non-None retention from process env / settings.
    provider.prompt_cache_retention = None
    payload: dict = {"model": "anthropic/claude-sonnet-4", "messages": []}

    provider._apply_prompt_cache(payload)

    assert payload["extra_body"] == {
        "session_id": "chat:abc",
        "cache_control": {"type": "ephemeral"},
    }


def test_openrouter_non_anthropic_skips_cache_control() -> None:
    from app.services.providers.openrouter_provider import OpenRouterProvider

    provider = OpenRouterProvider(
        api_key="test-key",
        prompt_cache_key="chat:abc",
        prompt_cache_retention="24h",
        prompt_cache_enabled=True,
    )
    payload: dict = {"model": "openai/gpt-5", "messages": []}

    provider._apply_prompt_cache(payload)

    assert payload["prompt_cache_key"] == "chat:abc"
    assert payload["prompt_cache_retention"] == "24h"
    assert "extra_body" not in payload


def test_openrouter_incognito_skips_anthropic_cache_control() -> None:
    from app.services.providers.openrouter_provider import OpenRouterProvider

    provider = OpenRouterProvider(
        api_key="test-key",
        prompt_cache_key=None,
        prompt_cache_enabled=False,
    )
    payload: dict = {"model": "anthropic/claude-opus-4.5", "messages": []}

    provider._apply_prompt_cache(payload)

    assert payload == {"model": "anthropic/claude-opus-4.5", "messages": []}


def test_openrouter_strip_prompt_cache_removes_cache_control() -> None:
    from app.services.providers.openrouter_provider import OpenRouterProvider

    provider = OpenRouterProvider(api_key="test-key")
    payload = {
        "prompt_cache_key": "chat:abc",
        "extra_body": {
            "provider": {"only": ["anthropic"], "allow_fallbacks": False},
            "cache_control": {"type": "ephemeral"},
        },
    }

    assert provider._strip_prompt_cache(payload) is True
    assert "prompt_cache_key" not in payload
    assert payload["extra_body"] == {
        "provider": {"only": ["anthropic"], "allow_fallbacks": False},
    }


@pytest.mark.asyncio
async def test_openrouter_chat_completion_merges_cache_control_with_routing() -> None:
    from app.services.providers.openrouter_provider import OpenRouterProvider

    provider = OpenRouterProvider(
        api_key="test-key",
        prompt_cache_key="chat:abc",
        prompt_cache_enabled=True,
        extra_body={
            "provider": {"only": ["anthropic"], "allow_fallbacks": False},
        },
    )
    provider.prompt_cache_retention = None
    fake_client = _RecordingOpenAIClient()
    provider.client = fake_client

    payload = {
        "model": "anthropic/claude-opus-4.5",
        "messages": [{"role": "user", "content": "hi"}],
    }
    provider._apply_prompt_cache(payload)
    await provider._create_chat_completion(payload)

    assert fake_client.payloads[0]["extra_body"] == {
        "provider": {"only": ["anthropic"], "allow_fallbacks": False},
        "session_id": "chat:abc",
        "cache_control": {"type": "ephemeral"},
    }
    assert fake_client.payloads[0]["prompt_cache_key"] == "chat:abc"


def test_extract_response_reasoning_from_summary_items() -> None:
    from types import SimpleNamespace

    from app.services.providers.openai_provider import _extract_response_reasoning

    response = SimpleNamespace(
        output=[
            SimpleNamespace(
                type="reasoning",
                summary=[
                    SimpleNamespace(type="summary_text", text="First thought"),
                    SimpleNamespace(type="summary_text", text="Second thought"),
                ],
            ),
            SimpleNamespace(type="message", content=[]),
        ]
    )
    assert _extract_response_reasoning(response) == "First thought\n\nSecond thought"
    assert _extract_response_reasoning(SimpleNamespace(output=[])) is None


def test_responses_reasoning_payload_opts_into_summary() -> None:
    provider = OpenAIProvider(api_key="test-key", reasoning_effort="medium")
    assert provider._responses_reasoning_payload("o3") == {
        "effort": "medium",
        "summary": "auto",
    }
    provider_none = OpenAIProvider(api_key="test-key", reasoning_effort="none")
    assert provider_none._responses_reasoning_payload("o3") is None
