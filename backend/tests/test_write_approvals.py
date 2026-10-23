from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import uuid4

import anyio
import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.api.chats import _resolve_exec_policy
from app.models import Org, User
from app.services.integrations.write_approval import (
    cancelled_write_output,
    get_always_allow_providers,
    providers_covered,
    revoke_always_allow,
    upsert_always_allow,
)
from app.services.integrations.write_approval_context import WriteApprovalContext
from app.services.integrations.write_safety import classify_external_write
from app.services.langchain_runtime import run_agentic_loop_langchain
from app.services.providers.base import ChatResponse, ChatToolCall, ChatUsage
from app.services.tools.registry import ToolRegistry, ToolResult, ToolSpec


def _session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _usage() -> ChatUsage:
    return ChatUsage(0, 0, 0, 0, 0, 0, 0)


def test_classify_google_write_vs_read() -> None:
    write = classify_external_write("catalog-gmail__gmail_send_message")
    assert write is not None
    assert write.provider_id == "google"
    assert write.catalog_id == "gmail"
    assert classify_external_write("catalog-gmail__gmail_list_messages") is None
    assert classify_external_write("catalog-google_sheets__sheets_update_values") is not None
    assert classify_external_write("catalog-google_sheets__sheets_get_values") is None


def test_classify_microsoft_write_vs_read() -> None:
    write = classify_external_write("catalog-outlook__create_draft")
    assert write is not None
    assert write.provider_id == "microsoft"
    assert write.catalog_id == "outlook"
    assert classify_external_write("catalog-outlook__list_messages") is None
    assert classify_external_write("catalog-outlook_calendar__get_event") is None


def test_classify_ignores_non_catalog_mcp() -> None:
    assert classify_external_write("custom_server__delete_thing") is None
    assert classify_external_write("code_execution") is None
    assert classify_external_write("not_an_mcp_tool") is None


def test_exec_policy_prompt_collapses_to_always() -> None:
    assert _resolve_exec_policy("prompt", None) == "always"
    assert _resolve_exec_policy("prompt", True) == "always"
    assert _resolve_exec_policy("off", None) == "off"
    assert _resolve_exec_policy("always", None) == "always"
    assert _resolve_exec_policy("always", False) == "off"
    assert _resolve_exec_policy("unknown", None) == "always"


def test_providers_covered_and_cancelled_output() -> None:
    assert providers_covered(providers={"google"}, always={"google"}, session_ok=False)
    assert providers_covered(providers={"google", "microsoft"}, always=set(), session_ok=True)
    assert not providers_covered(
        providers={"google", "microsoft"}, always={"google"}, session_ok=False
    )
    out = cancelled_write_output("catalog-gmail__gmail_send_message")
    assert out["cancelled"] is True
    assert out["requires_write_approval"] is True


def test_always_allow_prefs_upsert_and_revoke() -> None:
    session = _session()
    org = Org(name="o")
    user = User(email="u@example.com", hashed_password="x")
    session.add(org)
    session.add(user)
    session.commit()
    session.refresh(org)
    session.refresh(user)

    upsert_always_allow(
        session, user_id=user.id, org_id=org.id, provider_ids={"google", "microsoft"}
    )
    assert get_always_allow_providers(session, user_id=user.id, org_id=org.id) == {
        "google",
        "microsoft",
    }
    assert revoke_always_allow(
        session, user_id=user.id, org_id=org.id, provider_id="google"
    )
    assert get_always_allow_providers(session, user_id=user.id, org_id=org.id) == {
        "microsoft"
    }


@pytest.mark.asyncio
async def test_agentic_loop_skips_wait_when_always_allowed() -> None:
    registry = ToolRegistry()
    ran: list[str] = []

    async def _send(args: dict) -> ToolResult:
        ran.append("send")
        return ToolResult(name="catalog-gmail__gmail_send_message", output={"ok": True})

    registry.register(
        ToolSpec(
            name="catalog-gmail__gmail_send_message",
            description="Send",
            parameters={"type": "object", "properties": {}},
        ),
        _send,
    )

    @dataclass
    class _Provider:
        calls: int = 0

        async def chat_with_tools(self, model: str, messages: list[dict], tools: list[ToolSpec]):
            self.calls += 1
            if self.calls == 1:
                return ChatResponse(
                    content="",
                    usage=_usage(),
                    tool_calls=[
                        ChatToolCall(
                            id="t1",
                            name="catalog-gmail__gmail_send_message",
                            arguments={},
                        )
                    ],
                )
            return ChatResponse(content="sent", usage=_usage(), tool_calls=[])

    waited = MagicMock()
    ctx = WriteApprovalContext(
        task_id=uuid4(),
        chat_id=uuid4(),
        org_id=uuid4(),
        user_id=uuid4(),
        get_always_providers=lambda: {"google"},
        set_always_providers=lambda _p: None,
    )
    with patch(
        "app.services.integrations.write_approval.wait_for_write_decision",
        waited,
    ), patch(
        "app.services.integrations.write_approval.has_session_allow",
        return_value=False,
    ):
        content, *_ = await run_agentic_loop_langchain(
            provider=_Provider(),
            model_name="fake",
            messages=[{"role": "user", "content": "send"}],
            tool_registry=registry,
            max_steps=3,
            write_approval=ctx,
        )
    assert content == "sent"
    assert ran == ["send"]
    waited.assert_not_called()


@pytest.mark.asyncio
async def test_agentic_loop_once_runs_writes_after_approval() -> None:
    registry = ToolRegistry()
    ran: list[str] = []

    async def _send(args: dict) -> ToolResult:
        ran.append("send")
        return ToolResult(name="catalog-gmail__gmail_send_message", output={"ok": True})

    async def _list(args: dict) -> ToolResult:
        ran.append("list")
        return ToolResult(name="catalog-gmail__gmail_list_messages", output={"ok": True})

    registry.register(
        ToolSpec(
            name="catalog-gmail__gmail_send_message",
            description="Send",
            parameters={"type": "object", "properties": {}},
        ),
        _send,
    )
    registry.register(
        ToolSpec(
            name="catalog-gmail__gmail_list_messages",
            description="List",
            parameters={"type": "object", "properties": {}},
        ),
        _list,
    )

    @dataclass
    class _Provider:
        calls: int = 0

        async def chat_with_tools(self, model: str, messages: list[dict], tools: list[ToolSpec]):
            self.calls += 1
            if self.calls == 1:
                return ChatResponse(
                    content="",
                    usage=_usage(),
                    tool_calls=[
                        ChatToolCall(
                            id="t-list",
                            name="catalog-gmail__gmail_list_messages",
                            arguments={},
                        ),
                        ChatToolCall(
                            id="t-send",
                            name="catalog-gmail__gmail_send_message",
                            arguments={},
                        ),
                    ],
                )
            return ChatResponse(content="done", usage=_usage(), tool_calls=[])

    awaiting: list[dict[str, Any]] = []
    set_always_calls: list[set[str]] = []
    events: list[dict[str, Any]] = []

    ctx = WriteApprovalContext(
        task_id=uuid4(),
        chat_id=uuid4(),
        org_id=uuid4(),
        user_id=uuid4(),
        get_always_providers=lambda: set(),
        set_always_providers=lambda providers: set_always_calls.append(set(providers)),
        set_task_awaiting=lambda payload: awaiting.append(payload),
        set_task_streaming=lambda: None,
    )

    send_stream, recv_stream = anyio.create_memory_object_stream[dict[str, Any]](64)

    async def _drain() -> None:
        async with recv_stream:
            async for event in recv_stream:
                events.append(event)

    with patch(
        "app.services.integrations.write_approval.wait_for_write_decision",
        return_value="once",
    ), patch(
        "app.services.integrations.write_approval.has_session_allow",
        return_value=False,
    ):
        async with anyio.create_task_group() as tg:
            tg.start_soon(_drain)
            content, *_ = await run_agentic_loop_langchain(
                provider=_Provider(),
                model_name="fake",
                messages=[{"role": "user", "content": "go"}],
                tool_registry=registry,
                max_steps=3,
                write_approval=ctx,
                tool_event_sender=send_stream,
            )
            await send_stream.aclose()

    assert content == "done"
    assert ran == ["list", "send"]
    assert awaiting
    assert awaiting[0]["type"] == "write_approval"
    assert awaiting[0]["status"] == "pending"
    assert set_always_calls == []
    approval_events = [e for e in events if e.get("type") == "write_approval"]
    assert any(e.get("status") == "pending" for e in approval_events)
    assert any(e.get("status") == "resolved" and e.get("decision") == "once" for e in approval_events)


@pytest.mark.asyncio
async def test_agentic_loop_deny_cancels_writes() -> None:
    registry = ToolRegistry()
    ran: list[str] = []

    async def _send(args: dict) -> ToolResult:
        ran.append("send")
        return ToolResult(name="catalog-gmail__gmail_send_message", output={"ok": True})

    registry.register(
        ToolSpec(
            name="catalog-gmail__gmail_send_message",
            description="Send",
            parameters={"type": "object", "properties": {}},
        ),
        _send,
    )

    @dataclass
    class _Provider:
        calls: int = 0
        tool_payloads: list[Any] = field(default_factory=list)

        async def chat_with_tools(self, model: str, messages: list[dict], tools: list[ToolSpec]):
            self.calls += 1
            if self.calls == 1:
                return ChatResponse(
                    content="",
                    usage=_usage(),
                    tool_calls=[
                        ChatToolCall(
                            id="t1",
                            name="catalog-gmail__gmail_send_message",
                            arguments={},
                        )
                    ],
                )
            self.tool_payloads = [
                m for m in messages if m.get("role") == "tool"
            ]
            return ChatResponse(content="cancelled", usage=_usage(), tool_calls=[])

    provider = _Provider()
    ctx = WriteApprovalContext(
        task_id=uuid4(),
        chat_id=uuid4(),
        org_id=uuid4(),
        user_id=uuid4(),
        get_always_providers=lambda: set(),
        set_always_providers=lambda _p: None,
        set_task_awaiting=lambda _p: None,
        set_task_streaming=lambda: None,
    )
    with patch(
        "app.services.integrations.write_approval.wait_for_write_decision",
        return_value="deny",
    ), patch(
        "app.services.integrations.write_approval.has_session_allow",
        return_value=False,
    ):
        content, *_ = await run_agentic_loop_langchain(
            provider=provider,
            model_name="fake",
            messages=[{"role": "user", "content": "send"}],
            tool_registry=registry,
            max_steps=3,
            write_approval=ctx,
        )
    assert content == "cancelled"
    assert ran == []
    assert provider.tool_payloads
    body = str(provider.tool_payloads[0].get("content") or "")
    assert "denied" in body.lower() or "cancelled" in body.lower() or "User denied" in body


@pytest.mark.asyncio
async def test_agentic_loop_session_sets_redis_allow() -> None:
    registry = ToolRegistry()

    async def _send(args: dict) -> ToolResult:
        return ToolResult(name="catalog-gmail__gmail_send_message", output={"ok": True})

    registry.register(
        ToolSpec(
            name="catalog-gmail__gmail_send_message",
            description="Send",
            parameters={"type": "object", "properties": {}},
        ),
        _send,
    )

    @dataclass
    class _Provider:
        calls: int = 0

        async def chat_with_tools(self, model: str, messages: list[dict], tools: list[ToolSpec]):
            self.calls += 1
            if self.calls == 1:
                return ChatResponse(
                    content="",
                    usage=_usage(),
                    tool_calls=[
                        ChatToolCall(
                            id="t1",
                            name="catalog-gmail__gmail_send_message",
                            arguments={},
                        )
                    ],
                )
            return ChatResponse(content="ok", usage=_usage(), tool_calls=[])

    set_session = MagicMock()
    ctx = WriteApprovalContext(
        task_id=uuid4(),
        chat_id=uuid4(),
        org_id=uuid4(),
        user_id=uuid4(),
        get_always_providers=lambda: set(),
        set_always_providers=lambda _p: None,
        set_task_awaiting=lambda _p: None,
        set_task_streaming=lambda: None,
    )
    with patch(
        "app.services.integrations.write_approval.wait_for_write_decision",
        return_value="session",
    ), patch(
        "app.services.integrations.write_approval.has_session_allow",
        return_value=False,
    ), patch(
        "app.services.integrations.write_approval.set_session_allow",
        set_session,
    ):
        content, *_ = await run_agentic_loop_langchain(
            provider=_Provider(),
            model_name="fake",
            messages=[{"role": "user", "content": "send"}],
            tool_registry=registry,
            max_steps=3,
            write_approval=ctx,
        )
    assert content == "ok"
    set_session.assert_called_once()
