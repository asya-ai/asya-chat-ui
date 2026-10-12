"""Regression matrix for prod chat models × tools × reasoning_effort.

Guards the failure modes we hit in production:

1. Never preemptively send reasoning_effort="none" on tool calls for models that
   require reasoning (OpenRouter Claude / Grok → "Reasoning is mandatory...").
2. gpt-*-sol tools + reasoning on chat.completions must fall back to /v1/responses
   (keeping reasoning), not disable reasoning via reasoning_effort="none".
3. Preserve the configured effort on the first request for normal chat models.
4. Azure must call /openai/v1/responses (not legacy /openai/responses) — verified
   with httpx MockTransport against the real OpenAI SDK clients.
5. If Azure responses 404s, fall back to chat.completions with reasoning_effort=none
   and do not sticky-prefer responses.

Prod models covered here:
  openai/azure · gpt-5.6-terra, gpt-6-astra, gpt-5.3-codex, gpt-6-sol, gpt-6-luna
  openrouter · anthropic/claude-sonnet-5, anthropic/claude-opus-5.5, x-ai/grok-4.7

Not in this matrix (different APIs):
  vertex / gemini, embeddings, image-only models.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.services.providers.base import ChatToolSpec
from app.services.providers.openai_provider import (
    AzureOpenAIProvider,
    NonChatModelError,
    OpenAIProvider,
    _is_function_tools_with_reasoning_effort_error,
    _is_non_chat_model_error,
)
from app.services.providers.openrouter_provider import OpenRouterProvider

PROD_OPENAI_COMPAT_CHAT_MODELS: list[tuple[str, str, str]] = [
    ("openai", "gpt-5.6-terra", "high"),
    ("openai", "gpt-6-astra", "high"),
    ("openai", "gpt-5.3-codex", "medium"),
    ("openai", "gpt-6-sol", "high"),
    ("openai", "gpt-6-luna", "high"),
    ("azure", "gpt-6-sol", "high"),
    ("azure", "gpt-6-luna", "high"),
    ("azure", "gpt-5.6-terra", "high"),
]

PROD_OPENROUTER_MANDATORY_REASONING_MODELS: list[tuple[str, str, str]] = [
    ("openrouter", "anthropic/claude-sonnet-5", "high"),
    ("openrouter", "anthropic/claude-opus-5.5", "high"),
    ("openrouter", "x-ai/grok-4.7", "high"),
]

PROD_SOL_TOOLS_CONFLICT_MODELS: list[str] = [
    "gpt-6-sol",
    "gpt-6-luna",
]

_SEARCH_TOOL = ChatToolSpec(name="search", description="", parameters={})


class _RecordingClient:
    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []
        self.chat = self
        self.completions = self
        self.responses = self
        self.response_payloads: list[dict[str, Any]] = []

    def with_options(self, **kwargs):
        return self

    async def create(self, **payload):
        raise NotImplementedError


def _chat_result(text: str = "ok") -> Any:
    message = SimpleNamespace(content=text, tool_calls=None, reasoning_content=None)
    choice = SimpleNamespace(message=message, finish_reason="stop", delta=message)
    usage = SimpleNamespace(
        prompt_tokens=1,
        completion_tokens=1,
        total_tokens=2,
        input_tokens=0,
        output_tokens=0,
    )
    return SimpleNamespace(choices=[choice], usage=usage)


def _responses_result(text: str = "ok") -> Any:
    usage = SimpleNamespace(
        prompt_tokens=1,
        completion_tokens=1,
        total_tokens=2,
        input_tokens=1,
        output_tokens=1,
        input_tokens_details=SimpleNamespace(cached_tokens=0),
        output_tokens_details=SimpleNamespace(reasoning_tokens=0),
    )
    return SimpleNamespace(
        output_text=text,
        output=[
            SimpleNamespace(
                type="message",
                content=[SimpleNamespace(type="output_text", text=text)],
            )
        ],
        usage=usage,
        status="completed",
    )


def _mandatory_reasoning_error() -> ValueError:
    return ValueError(
        "Error code: 400 - {'error': {'message': 'Reasoning is mandatory for this "
        "endpoint and cannot be disabled.', 'code': 400, "
        "'metadata': {'provider_name': None}}}"
    )


def _sol_tools_reasoning_error(model: str) -> ValueError:
    return ValueError(
        "Error code: 400 - {'error': {'message': \"Function tools with reasoning_effort "
        f"are not supported for {model} in /v1/chat/completions. To use function tools, "
        "use /v1/responses or set reasoning_effort to 'none'.\", "
        "'type': 'invalid_request_error', 'param': 'reasoning_effort', 'code': None}}"
    )


def _attach_recording_client(provider: OpenAIProvider, *, chat_create, responses_create=None) -> _RecordingClient:
    client = _RecordingClient()

    async def _chat_create(**payload):
        client.payloads.append(dict(payload))
        return await chat_create(payload)

    async def _responses_create(**payload):
        client.response_payloads.append(dict(payload))
        if responses_create is None:
            raise AssertionError("responses.create was not expected")
        return await responses_create(payload)

    async def _create(**payload):
        if "messages" in payload:
            return await _chat_create(**payload)
        return await _responses_create(**payload)

    client.create = _create  # type: ignore[method-assign]
    provider.client = client
    # Azure routes responses through a separate v1 client.
    if hasattr(provider, "_azure_responses_client"):
        provider._azure_responses_client = client
    return client


@pytest.mark.parametrize(
    ("provider_name", "model_name", "effort"),
    [
        pytest.param(provider, model, effort, id=f"{provider}:{model}")
        for provider, model, effort in (
            PROD_OPENAI_COMPAT_CHAT_MODELS + PROD_OPENROUTER_MANDATORY_REASONING_MODELS
        )
    ],
)
@pytest.mark.asyncio
async def test_prod_chat_with_tools_preserves_configured_reasoning_effort(
    provider_name: str,
    model_name: str,
    effort: str,
) -> None:
    """First tools request must keep configured effort — never preemptively 'none'."""
    if provider_name == "openrouter":
        provider: OpenAIProvider = OpenRouterProvider(
            api_key="test-key", reasoning_effort=effort
        )
    elif provider_name == "azure":
        provider = AzureOpenAIProvider(
            api_key="test-key",
            endpoint="https://example.openai.azure.com",
            reasoning_effort=effort,
        )
    else:
        provider = OpenAIProvider(api_key="test-key", reasoning_effort=effort)

    async def chat_create(payload: dict):
        if payload.get("reasoning_effort") == "none":
            raise _mandatory_reasoning_error()
        return _chat_result()

    client = _attach_recording_client(provider, chat_create=chat_create)

    response = await provider.chat_with_tools(
        model_name,
        [{"role": "user", "content": "hello"}],
        [_SEARCH_TOOL],
    )

    assert response.content == "ok"
    assert len(client.payloads) == 1
    assert client.payloads[0]["model"] == model_name
    assert client.payloads[0]["reasoning_effort"] == effort
    assert client.payloads[0]["reasoning_effort"] != "none"
    assert client.payloads[0]["tools"]
    assert client.response_payloads == []


@pytest.mark.parametrize(
    ("provider_name", "model_name", "effort"),
    [
        pytest.param(provider, model, effort, id=f"{provider}:{model}")
        for provider, model, effort in PROD_OPENROUTER_MANDATORY_REASONING_MODELS
    ],
)
@pytest.mark.asyncio
async def test_prod_openrouter_stream_with_tools_preserves_reasoning(
    provider_name: str,
    model_name: str,
    effort: str,
) -> None:
    del provider_name
    provider = OpenRouterProvider(api_key="test-key", reasoning_effort=effort)

    class _Stream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    async def chat_create(payload: dict):
        if payload.get("reasoning_effort") == "none":
            raise _mandatory_reasoning_error()
        return _Stream()

    client = _attach_recording_client(provider, chat_create=chat_create)

    async for _ in provider.chat_stream_with_tools(
        model_name,
        [{"role": "user", "content": "hello"}],
        [_SEARCH_TOOL],
    ):
        pass

    assert len(client.payloads) == 1
    assert client.payloads[0]["reasoning_effort"] == effort
    assert client.payloads[0]["stream"] is True


@pytest.mark.parametrize("model_name", PROD_SOL_TOOLS_CONFLICT_MODELS)
@pytest.mark.parametrize(
    "provider_factory",
    [
        pytest.param(
            lambda: OpenAIProvider(api_key="test-key", reasoning_effort="high"),
            id="openai",
        ),
        pytest.param(
            lambda: AzureOpenAIProvider(
                api_key="test-key",
                endpoint="https://example.openai.azure.com",
                reasoning_effort="high",
            ),
            id="azure",
        ),
    ],
)
@pytest.mark.asyncio
async def test_prod_sol_tools_conflict_falls_back_to_responses(
    model_name: str,
    provider_factory,
) -> None:
    provider = provider_factory()

    async def chat_create(payload: dict):
        raise _sol_tools_reasoning_error(model_name)

    async def responses_create(payload: dict):
        return _responses_result("news via responses")

    client = _attach_recording_client(
        provider, chat_create=chat_create, responses_create=responses_create
    )

    response = await provider.chat_with_tools(
        model_name,
        [{"role": "user", "content": "Whats on the news today?"}],
        [_SEARCH_TOOL],
    )

    assert response.content == "news via responses"
    assert len(client.payloads) == 1
    assert client.payloads[0]["reasoning_effort"] == "high"
    assert len(client.response_payloads) == 1
    # Responses keeps reasoning — never disabled to none on chat.completions retry.
    assert client.response_payloads[0].get("reasoning", {}).get("effort") == "high"
    assert provider._should_use_responses(model_name) is True
    assert provider.consume_responses_api_discovery() is True


def test_sol_tools_conflict_is_treated_as_responses_only() -> None:
    exc = _sol_tools_reasoning_error("gpt-6-sol")
    assert _is_function_tools_with_reasoning_effort_error(exc) is True
    assert _is_non_chat_model_error(exc) is True


def test_mandatory_reasoning_error_is_not_sol_tools_conflict() -> None:
    exc = _mandatory_reasoning_error()
    assert _is_function_tools_with_reasoning_effort_error(exc) is False
    assert _is_non_chat_model_error(exc) is False


def test_azure_responses_client_uses_openai_v1_base_url() -> None:
    provider = AzureOpenAIProvider(
        api_key="test-key",
        endpoint="https://example.openai.azure.com",
        reasoning_effort="high",
    )
    assert str(provider._responses_client.base_url).rstrip("/") == (
        "https://example.openai.azure.com/openai/v1"
    )
    # Prepared path must be the v1 route Microsoft documents — not /openai/responses.
    prepared = str(provider._responses_client._prepare_url("/responses"))
    assert prepared.rstrip("/") == "https://example.openai.azure.com/openai/v1/responses"
    assert "/openai/responses" not in prepared or "/openai/v1/responses" in prepared
    # Classic Azure client must NOT be the responses client (that path 404s in prod).
    assert provider._responses_client is not provider.client


@pytest.mark.asyncio
async def test_azure_wire_sol_conflict_posts_to_openai_v1_responses() -> None:
    """httpx-level: Azure tools+reasoning conflict must hit /openai/v1/responses."""
    import json

    import httpx
    from openai import AsyncAzureOpenAI, AsyncOpenAI

    recorded: list[tuple[str, str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode()) if request.content else {}
        url = str(request.url)
        recorded.append((request.method, url, body))
        path = request.url.path
        # Legacy Azure responses path — the prod 404 we hit.
        if path.endswith("/responses") and "/openai/v1/" not in path:
            return httpx.Response(
                404,
                json={"error": {"code": "404", "message": "Resource not found"}},
            )
        if path.endswith("/chat/completions"):
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": (
                            "Function tools with reasoning_effort are not supported "
                            "for gpt-6-sol in /v1/chat/completions. To use function "
                            "tools, use /v1/responses or set reasoning_effort to 'none'."
                        ),
                        "type": "invalid_request_error",
                        "param": "reasoning_effort",
                    }
                },
            )
        if path.endswith("/openai/v1/responses") or path.endswith("/v1/responses"):
            assert body.get("reasoning", {}).get("effort") == "high"
            assert body.get("tools")
            return httpx.Response(
                200,
                json={
                    "id": "resp_1",
                    "object": "response",
                    "created_at": 1,
                    "status": "completed",
                    "model": "gpt-6-sol",
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "wire ok"}],
                        }
                    ],
                    "output_text": "wire ok",
                    "usage": {
                        "input_tokens": 1,
                        "output_tokens": 1,
                        "total_tokens": 2,
                    },
                },
            )
        return httpx.Response(404, json={"error": {"message": f"unexpected {path}"}})

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)

    provider = AzureOpenAIProvider(
        api_key="test-key",
        endpoint="https://example.openai.azure.com",
        reasoning_effort="high",
    )
    provider.client = AsyncAzureOpenAI(
        api_key="test-key",
        azure_endpoint="https://example.openai.azure.com",
        api_version="2024-06-01",
        http_client=http_client,
        max_retries=0,
    )
    provider._azure_responses_client = AsyncOpenAI(
        api_key="test-key",
        base_url="https://example.openai.azure.com/openai/v1/",
        http_client=http_client,
        max_retries=0,
    )

    response = await provider.chat_with_tools(
        "gpt-6-sol",
        [{"role": "user", "content": "Hi, whats new today?"}],
        [_SEARCH_TOOL],
    )

    assert response.content == "wire ok"
    urls = [url for _, url, _ in recorded]
    assert any("/chat/completions" in url for url in urls)
    assert any(url.rstrip("/").endswith("/openai/v1/responses") for url in urls)
    assert not any(
        "/openai/responses" in url and "/openai/v1/responses" not in url for url in urls
    ), f"must never call legacy Azure responses path; got {urls}"
    assert provider._should_use_responses("gpt-6-sol") is True


@pytest.mark.asyncio
async def test_azure_wire_legacy_responses_path_404_falls_back_to_none() -> None:
    """If responses is still 404, chat.completions retry with none must succeed."""
    import json

    import httpx
    from openai import AsyncAzureOpenAI, AsyncOpenAI

    recorded: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(str(request.url))
        body = json.loads(request.content.decode()) if request.content else {}
        path = request.url.path
        if path.endswith("/chat/completions"):
            if body.get("reasoning_effort") == "none":
                return httpx.Response(
                    200,
                    json={
                        "id": "chatcmpl_1",
                        "object": "chat.completion",
                        "created": 1,
                        "model": "gpt-6-sol",
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": "none ok"},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 1,
                            "completion_tokens": 1,
                            "total_tokens": 2,
                        },
                    },
                )
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": (
                            "Function tools with reasoning_effort are not supported "
                            "for gpt-6-sol in /v1/chat/completions. To use function "
                            "tools, use /v1/responses or set reasoning_effort to 'none'."
                        ),
                        "type": "invalid_request_error",
                        "param": "reasoning_effort",
                    }
                },
            )
        if path.endswith("/responses"):
            return httpx.Response(
                404,
                json={"error": {"code": "404", "message": "Resource not found"}},
            )
        return httpx.Response(500, json={"error": {"message": "unexpected"}})

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)

    provider = AzureOpenAIProvider(
        api_key="test-key",
        endpoint="https://example.openai.azure.com",
        reasoning_effort="high",
    )
    provider.client = AsyncAzureOpenAI(
        api_key="test-key",
        azure_endpoint="https://example.openai.azure.com",
        api_version="2024-06-01",
        http_client=http_client,
        max_retries=0,
    )
    provider._azure_responses_client = AsyncOpenAI(
        api_key="test-key",
        base_url="https://example.openai.azure.com/openai/v1/",
        http_client=http_client,
        max_retries=0,
    )

    response = await provider.chat_with_tools(
        "gpt-6-sol",
        [{"role": "user", "content": "Hi, whats new today?"}],
        [_SEARCH_TOOL],
    )

    assert response.content == "none ok"
    assert any("/openai/v1/responses" in url for url in recorded)
    assert any("/chat/completions" in url for url in recorded)
    assert provider._should_use_responses("gpt-6-sol") is False


@pytest.mark.asyncio
async def test_azure_wire_rejects_using_classic_client_for_responses() -> None:
    """Regression: classic AsyncAzureOpenAI responses URL is the prod 404 path."""
    import httpx
    from openai import AsyncAzureOpenAI

    recorded: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(str(request.url))
        return httpx.Response(
            404, json={"error": {"code": "404", "message": "Resource not found"}}
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    classic = AsyncAzureOpenAI(
        api_key="test-key",
        azure_endpoint="https://example.openai.azure.com",
        api_version="2024-06-01",
        http_client=http_client,
        max_retries=0,
    )
    with pytest.raises(Exception):
        await classic.responses.create(model="gpt-6-sol", input="hi")

    assert recorded
    assert all("/openai/v1/responses" not in url for url in recorded)
    assert any(url.split("?")[0].endswith("/openai/responses") for url in recorded)

    # Our provider must not use that classic client for responses.
    provider = AzureOpenAIProvider(
        api_key="test-key",
        endpoint="https://example.openai.azure.com",
    )
    assert str(provider._responses_client._prepare_url("/responses")).endswith(
        "/openai/v1/responses"
    )


@pytest.mark.asyncio
async def test_azure_responses_404_falls_back_to_reasoning_none() -> None:
    """When Azure /openai/v1/responses is missing, still answer via chat + none."""
    from app.services.providers.openai_provider import _is_responses_unavailable_error

    provider = AzureOpenAIProvider(
        api_key="test-key",
        endpoint="https://example.openai.azure.com",
        reasoning_effort="high",
    )

    class _NotFound(Exception):
        status_code = 404

        def __str__(self) -> str:
            return "Error code: 404 - {'error': {'code': '404', 'message': 'Resource not found'}}"

    assert _is_responses_unavailable_error(_NotFound()) is True

    async def chat_create(payload: dict):
        if payload.get("reasoning_effort") == "none":
            return _chat_result("fallback ok")
        raise _sol_tools_reasoning_error("gpt-6-sol")

    async def responses_create(payload: dict):
        raise _NotFound()

    client = _attach_recording_client(
        provider, chat_create=chat_create, responses_create=responses_create
    )

    response = await provider.chat_with_tools(
        "gpt-6-sol",
        [{"role": "user", "content": "Hi, whats new today?"}],
        [_SEARCH_TOOL],
    )

    assert response.content == "fallback ok"
    assert len(client.payloads) == 2
    assert client.payloads[0]["reasoning_effort"] == "high"
    assert client.payloads[1]["reasoning_effort"] == "none"
    assert len(client.response_payloads) == 1
    # Must not sticky-prefer responses after a 404.
    assert provider._should_use_responses("gpt-6-sol") is False


@pytest.mark.asyncio
async def test_sol_conflict_raises_non_chat_then_responses_keeps_reasoning() -> None:
    provider = OpenAIProvider(api_key="test-key", reasoning_effort="high")
    payload = {
        "model": "gpt-6-sol",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"type": "function", "function": {"name": "search", "parameters": {}}}],
        "reasoning_effort": "high",
    }

    async def chat_create(**kwargs):
        raise _sol_tools_reasoning_error("gpt-6-sol")

    client = _RecordingClient()
    client.create = chat_create  # type: ignore[method-assign]
    provider.client = client

    with pytest.raises(NonChatModelError):
        await provider._create_chat_completion(payload)
