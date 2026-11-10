from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.deps import AuthContext
from app.api.openai_compat import (
    ChatCompletionRequest,
    ChatContentPart,
    ChatMessagePayload,
    LegacyCompletionRequest,
    chat_completions,
    legacy_completions,
    list_models,
)
from app.api import openai_compat_support as compat_support
from app.models import ChatModel, Org, OrgProviderConfig, UsageEvent, User
from app.services.providers.base import (
    ChatResponse,
    ChatStreamChunk,
    ChatToolCall,
    ChatToolSpec,
    ChatUsage,
)


def _make_session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            Org.__table__,
            ChatModel.__table__,
            UsageEvent.__table__,
            OrgProviderConfig.__table__,
        ],
    )
    return Session(engine)


def _seed_org_user_model(session: Session) -> tuple[Org, User, ChatModel, AuthContext]:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    org = Org(name=f"Org-{uuid4().hex[:8]}")
    user = User(
        email=f"user-{uuid4().hex[:8]}@example.com",
        hashed_password="x",
        display_name="API User",
    )
    session.add(org)
    session.add(user)
    session.commit()
    session.refresh(org)
    session.refresh(user)
    model = ChatModel(
        provider="openai",
        model_name="gpt-test",
        display_name="GPT Test",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    session.add(model)
    session.commit()
    session.refresh(model)
    auth = AuthContext(user=user, org_id=org.id)
    return org, user, model, auth


class NoStreamProvider:
    def __init__(self) -> None:
        self.completion_params: dict | None = None

    async def chat(
        self,
        model: str,
        messages: list[dict],
        *,
        completion_params: dict | None = None,
    ) -> ChatResponse:
        self.completion_params = completion_params
        return ChatResponse(
            content="buffered",
            usage=ChatUsage(1, 1, 2, 1, 1, 0, 0),
        )


class RecordingProvider:
    def __init__(self) -> None:
        self.completion_params: dict | None = None
        self.messages: list[dict] | None = None
        self.tool_choice: object | None = None
        self.tools: list[ChatToolSpec] | None = None
        self.chat_calls = 0
        self.stream_calls = 0

    async def chat(
        self,
        model: str,
        messages: list[dict],
        *,
        completion_params: dict | None = None,
    ) -> ChatResponse:
        self.chat_calls += 1
        self.completion_params = completion_params
        self.messages = messages
        return ChatResponse(
            content="pong",
            usage=ChatUsage(
                prompt_tokens=11,
                completion_tokens=3,
                total_tokens=14,
                input_tokens=10,
                output_tokens=3,
                cached_tokens=4,
                thinking_tokens=2,
            ),
        )

    async def chat_stream(
        self,
        model: str,
        messages: list[dict],
        *,
        completion_params: dict | None = None,
    ) -> AsyncIterator[ChatStreamChunk]:
        self.stream_calls += 1
        self.completion_params = completion_params
        self.messages = messages
        yield ChatStreamChunk(content="po")
        yield ChatStreamChunk(content="ng")
        yield ChatStreamChunk(
            usage=ChatUsage(
                prompt_tokens=5,
                completion_tokens=2,
                total_tokens=7,
                input_tokens=5,
                output_tokens=2,
                cached_tokens=1,
                thinking_tokens=0,
            )
        )

    async def chat_with_tools(
        self,
        model: str,
        messages: list[dict],
        tools: list[ChatToolSpec],
        tool_choice: object | None = None,
        *,
        completion_params: dict | None = None,
    ) -> ChatResponse:
        self.chat_calls += 1
        self.completion_params = completion_params
        self.messages = messages
        self.tools = tools
        self.tool_choice = tool_choice
        return ChatResponse(
            content="",
            usage=ChatUsage(1, 1, 2, 1, 1, 0, 0),
            tool_calls=[
                ChatToolCall(id="call_1", name="get_weather", arguments={"city": "Riga"})
            ],
            finish_reason="tool_calls",
        )


@pytest.fixture
def compat_env(monkeypatch):
    session = _make_session()
    org, user, model, auth = _seed_org_user_model(session)
    provider = RecordingProvider()
    monkeypatch.setattr(
        "app.api.openai_compat.get_provider_for_org",
        lambda *args, **kwargs: provider,
    )
    monkeypatch.setattr(
        "app.api.openai_compat.allowed_model_ids",
        lambda *a, **k: {model.id},
    )
    monkeypatch.setattr(
        "app.api.openai_compat.require_provider_enabled",
        lambda *a, **k: SimpleNamespace(),
    )
    monkeypatch.setattr(
        "app.api.openai_compat.persist_responses_api_discovery",
        lambda *a, **k: None,
    )
    return SimpleNamespace(
        session=session,
        org=org,
        user=user,
        model=model,
        auth=auth,
        provider=provider,
    )


@pytest.mark.asyncio
async def test_chat_completions_forwards_sampling_params(compat_env) -> None:
    payload = ChatCompletionRequest(
        model=compat_env.model.model_name,
        messages=[ChatMessagePayload(role="user", content="ping")],
        temperature=0.15,
        max_completion_tokens=256,
        top_p=0.85,
        seed=42,
        stop=["END"],
    )
    result = await chat_completions(
        payload, session=compat_env.session, auth=compat_env.auth
    )
    assert result.choices[0].message.content == "pong"
    assert compat_env.provider.completion_params is not None
    assert compat_env.provider.completion_params["temperature"] == 0.15
    assert compat_env.provider.completion_params["max_completion_tokens"] == 256
    assert compat_env.provider.completion_params["top_p"] == 0.85
    assert compat_env.provider.completion_params["seed"] == 42
    assert compat_env.provider.completion_params["stop"] == ["END"]


@pytest.mark.asyncio
async def test_chat_completions_records_usage_with_token_details(compat_env) -> None:
    payload = ChatCompletionRequest(
        model=compat_env.model.model_name,
        messages=[ChatMessagePayload(role="user", content="ping")],
    )
    result = await chat_completions(
        payload, session=compat_env.session, auth=compat_env.auth
    )
    assert result.usage.prompt_tokens_details == {"cached_tokens": 4}
    assert result.usage.completion_tokens_details == {"reasoning_tokens": 2}
    events = compat_env.session.exec(select(UsageEvent)).all()
    assert len(events) == 1
    assert events[0].prompt_tokens == 11
    assert events[0].cached_tokens == 4
    assert events[0].thinking_tokens == 2
    assert events[0].model_id == compat_env.model.id


@pytest.mark.asyncio
async def test_chat_completions_multimodal_reaches_provider(compat_env) -> None:
    payload = ChatCompletionRequest(
        model=compat_env.model.model_name,
        messages=[
            ChatMessagePayload(
                role="user",
                content=[
                    ChatContentPart(type="text", text="describe"),
                    ChatContentPart(
                        type="image_url",
                        image_url={"url": "data:image/png;base64,abc"},
                    ),
                ],
            )
        ],
    )
    await chat_completions(payload, session=compat_env.session, auth=compat_env.auth)
    assert compat_env.provider.messages is not None
    user = compat_env.provider.messages[-1]
    assert isinstance(user["content"], list)
    assert user["content"][1]["type"] == "image_url"


@pytest.mark.asyncio
async def test_chat_completions_accepts_image_only_user_message(compat_env) -> None:
    payload = ChatCompletionRequest(
        model=compat_env.model.model_name,
        messages=[
            ChatMessagePayload(
                role="user",
                content=[
                    ChatContentPart(
                        type="image_url",
                        image_url={"url": "https://example.com/x.png"},
                    )
                ],
            )
        ],
    )
    await chat_completions(payload, session=compat_env.session, auth=compat_env.auth)
    assert compat_env.provider.chat_calls == 1


@pytest.mark.asyncio
async def test_chat_completions_stream_omits_usage_without_include_usage(
    compat_env,
) -> None:
    payload = ChatCompletionRequest(
        model=compat_env.model.model_name,
        messages=[ChatMessagePayload(role="user", content="ping")],
        stream=True,
    )
    response = await chat_completions(
        payload, session=compat_env.session, auth=compat_env.auth
    )
    body = b""
    async for chunk in response.body_iterator:
        body += chunk.encode("utf-8") if isinstance(chunk, str) else chunk
    text = body.decode("utf-8")
    assert '"usage"' not in text


@pytest.mark.asyncio
async def test_chat_completions_stream_yields_openai_sse_chunks(compat_env) -> None:
    payload = ChatCompletionRequest(
        model=compat_env.model.model_name,
        messages=[ChatMessagePayload(role="user", content="ping")],
        stream=True,
        stream_options={"include_usage": True},
        temperature=0.5,
    )
    response = await chat_completions(
        payload, session=compat_env.session, auth=compat_env.auth
    )
    body = b""
    async for chunk in response.body_iterator:
        body += chunk.encode("utf-8") if isinstance(chunk, str) else chunk
    text = body.decode("utf-8")
    assert "chat.completion.chunk" in text
    assert '"delta":' in text
    assert "[DONE]" in text
    assert '"content": "po"' in text or '"content":"po"' in text.replace(" ", "")
    assert compat_env.provider.stream_calls == 1
    assert compat_env.provider.completion_params["temperature"] == 0.5
    events = compat_env.session.exec(select(UsageEvent)).all()
    assert len(events) == 1
    assert events[0].total_tokens == 7


@pytest.mark.asyncio
async def test_chat_completions_tools_forwarded(compat_env) -> None:
    from app.api.openai_compat import ToolFunction, ToolSpec

    payload = ChatCompletionRequest(
        model=compat_env.model.model_name,
        messages=[ChatMessagePayload(role="user", content="weather?")],
        tools=[
            ToolSpec(
                type="function",
                function=ToolFunction(
                    name="get_weather",
                    description="Get weather",
                    parameters={"type": "object", "properties": {"city": {"type": "string"}}},
                ),
            )
        ],
        tool_choice={"type": "function", "function": {"name": "get_weather"}},
    )
    result = await chat_completions(
        payload, session=compat_env.session, auth=compat_env.auth
    )
    assert result.choices[0].finish_reason == "tool_calls"
    assert compat_env.provider.tools is not None
    assert compat_env.provider.tools[0].name == "get_weather"
    assert compat_env.provider.tool_choice == payload.tool_choice


@pytest.mark.asyncio
async def test_legacy_completions_maps_prompt_to_chat(compat_env) -> None:
    payload = LegacyCompletionRequest(
        model=compat_env.model.model_name,
        prompt="Complete this",
        temperature=0.1,
        max_tokens=50,
    )
    result = await legacy_completions(
        payload, session=compat_env.session, auth=compat_env.auth
    )
    assert result.object == "text_completion"
    assert result.choices[0].text == "pong"
    assert compat_env.provider.completion_params["temperature"] == 0.1


@pytest.mark.asyncio
async def test_chat_completions_passthrough_extra_request_fields(compat_env) -> None:
    payload = ChatCompletionRequest.model_validate(
        {
            "model": compat_env.model.model_name,
            "messages": [{"role": "user", "content": "hi"}],
            "temperature": 0.2,
            "vendor_extension": {"foo": "bar"},
        }
    )
    await chat_completions(payload, session=compat_env.session, auth=compat_env.auth)
    assert compat_env.provider.completion_params.get("vendor_extension") == {
        "foo": "bar"
    }


def test_list_models_returns_enabled_model(compat_env) -> None:
    from app.api.openai_compat import get_model

    result = list_models(session=compat_env.session, auth=compat_env.auth)
    assert [item.id for item in result.data] == [compat_env.model.model_name]
    assert result.data[0].created > 0
    single = get_model(
        compat_env.model.model_name,
        session=compat_env.session,
        auth=compat_env.auth,
    )
    assert single.id == compat_env.model.model_name
    assert single.created > 0


def test_completion_from_raw_preserves_multiple_choices() -> None:
    raw = SimpleNamespace(
        id="chatcmpl-abc",
        created=1700000000,
        object="chat.completion",
        choices=[
            SimpleNamespace(
                index=0,
                finish_reason="stop",
                logprobs=None,
                message=SimpleNamespace(
                    content="A",
                    tool_calls=None,
                    role="assistant",
                ),
            ),
            SimpleNamespace(
                index=1,
                finish_reason="stop",
                logprobs=None,
                message=SimpleNamespace(
                    content="B",
                    tool_calls=None,
                    role="assistant",
                ),
            ),
        ],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=4, total_tokens=14),
    )
    completion = compat_support.completion_from_raw(raw, model_name="gpt-test")
    assert len(completion.choices) == 2
    assert completion.choices[0].message.content == "A"
    assert completion.choices[1].message.content == "B"


def test_openai_compat_router_exposes_core_v1_routes() -> None:
    from app.api.openai_compat import router

    paths = {route.path for route in router.routes if hasattr(route, "path")}
    assert "/v1/models" in paths
    assert any("/v1/models/" in path or path == "/v1/models/{model_id}" for path in paths)
    assert "/v1/chat/completions" in paths
    assert "/v1/completions" in paths
    assert "/v1/responses" in paths
    assert "/v1/embeddings" in paths
    assert "/v1/files" not in paths
    assert not any("/audio" in path for path in paths)


def test_openai_compat_documented_gaps() -> None:
    """Explicit checklist for integrations; fail when endpoints are added without tests."""
    from app.api.openai_compat import router

    paths = {route.path for route in router.routes if hasattr(route, "path")}
    missing_by_design = {
        "/v1/files",
        "/v1/audio/speech",
        "/v1/audio/transcriptions",
        "/v1/images/generations",
        "/v1/assistants",
        "/v1/threads",
        "/v1/batches",
        "/v1/moderations",
    }
    assert missing_by_design.isdisjoint(paths)


@pytest.mark.asyncio
async def test_stream_falls_back_when_provider_has_no_stream(compat_env, monkeypatch) -> None:
    no_stream = NoStreamProvider()
    monkeypatch.setattr(
        "app.api.openai_compat.get_provider_for_org",
        lambda *args, **kwargs: no_stream,
    )
    payload = ChatCompletionRequest(
        model=compat_env.model.model_name,
        messages=[ChatMessagePayload(role="user", content="ping")],
        stream=True,
    )
    response = await chat_completions(
        payload, session=compat_env.session, auth=compat_env.auth
    )
    body = b""
    async for chunk in response.body_iterator:
        body += chunk.encode("utf-8") if isinstance(chunk, str) else chunk
    assert b"buffered" in body
    assert b"[DONE]" in body


@pytest.mark.asyncio
async def test_chat_completions_rejects_invalid_role(compat_env) -> None:
    from fastapi import HTTPException

    payload = ChatCompletionRequest(
        model=compat_env.model.model_name,
        messages=[ChatMessagePayload(role="bogus", content="hi")],
    )
    with pytest.raises(HTTPException) as exc:
        await chat_completions(
            payload, session=compat_env.session, auth=compat_env.auth
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_developer_role_normalized_to_system_message(compat_env) -> None:
    payload = ChatCompletionRequest(
        model=compat_env.model.model_name,
        messages=[
            ChatMessagePayload(role="developer", content="You are helpful."),
            ChatMessagePayload(role="user", content="hello"),
        ],
    )
    await chat_completions(payload, session=compat_env.session, auth=compat_env.auth)
    roles = [m["role"] for m in compat_env.provider.messages or []]
    assert roles[0] == "system"


def test_stream_chunk_sse_includes_usage_when_requested() -> None:
    from app.api.openai_compat import ChatCompletionUsage

    payload = compat_support.stream_chunk_sse(
        completion_id="chatcmpl-x",
        created=1,
        model="m",
        index=0,
        delta={},
        usage=ChatCompletionUsage(
            prompt_tokens=1,
            completion_tokens=2,
            total_tokens=3,
        ),
        usage_only=True,
    )
    data = json.loads(payload.removeprefix("data: ").strip())
    assert data["usage"]["total_tokens"] == 3
    assert data["choices"] == []
