"""Tests for subagent spawn, filespace helpers, and nesting guards."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.entities import (
    Chat,
    ChatGenerationTask,
    ChatMessage,
    ChatModel,
    Org,
    User,
)
from app.services.chat_filespace import family_chat_ids, filespace_chat_id, resolve_filespace_chat_id
from app.services.tools.subagent_tools import (
    SubagentToolContext,
    get_subagent_result,
    spawn_subagent,
)


def _session() -> Session:
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
            Chat.__table__,
            ChatMessage.__table__,
            ChatGenerationTask.__table__,
        ],
    )
    return Session(engine)


def _seed_parent(session: Session) -> tuple[Org, User, ChatModel, Chat]:
    org = Org(name=f"Org-{uuid4().hex[:8]}")
    user = User(email=f"u-{uuid4().hex[:8]}@example.com", hashed_password="x")
    model = ChatModel(
        provider="openai",
        model_name="gpt-test",
        display_name="GPT Test",
    )
    session.add(org)
    session.add(user)
    session.add(model)
    session.commit()
    session.refresh(org)
    session.refresh(user)
    session.refresh(model)
    chat = Chat(
        org_id=org.id,
        user_id=user.id,
        model_id=model.id,
        title="Parent",
        is_incognito=False,
        is_subagent=False,
    )
    session.add(chat)
    session.commit()
    session.refresh(chat)
    return org, user, model, chat


def test_filespace_helpers_resolve_parent() -> None:
    session = _session()
    _org, _user, _model, parent = _seed_parent(session)
    child = Chat(
        org_id=parent.org_id,
        user_id=parent.user_id,
        model_id=parent.model_id,
        parent_chat_id=parent.id,
        title="Child",
        is_subagent=True,
    )
    session.add(child)
    session.commit()
    session.refresh(child)

    assert filespace_chat_id(parent) == parent.id
    assert filespace_chat_id(child) == parent.id
    assert resolve_filespace_chat_id(session, child.id) == parent.id
    assert set(family_chat_ids(session, parent.id)) == {parent.id, child.id}


@pytest.mark.asyncio
async def test_spawn_subagent_creates_child_and_rejects_nested(monkeypatch) -> None:
    session = _session()
    enqueued: list = []

    def _fake_enqueue(task_id):
        enqueued.append(task_id)

    async def _no_wait(task_id, *, timeout_seconds=600):
        return None

    monkeypatch.setattr(
        "app.services.tools.subagent_tools._wait_for_task",
        _no_wait,
    )

    _org, _user, _model, parent = _seed_parent(session)
    ctx = SubagentToolContext(
        session=session,
        parent_chat=parent,
        parent_task_id=None,
        parent_metadata={"locale": "en"},
        enqueue_generation=_fake_enqueue,
    )

    result = await spawn_subagent(
        ctx,
        prompt="Research topic X",
        title="Researcher",
        mode="background",
    )
    assert result.output.get("status") == "running"
    child_id = result.output.get("child_chat_id")
    assert child_id
    assert enqueued

    child = session.get(Chat, UUID(str(child_id)))
    assert child is not None
    assert child.is_subagent is True
    assert child.parent_chat_id == parent.id
    assert child.title == "Researcher"
    seed = session.exec(
        select(ChatMessage).where(
            ChatMessage.chat_id == child.id, ChatMessage.role == "user"
        )
    ).first()
    assert seed is not None
    assert "Research topic X" in seed.content

    nested_ctx = SubagentToolContext(
        session=session,
        parent_chat=child,
        enqueue_generation=_fake_enqueue,
    )
    nested = await spawn_subagent(nested_ctx, prompt="Nope", mode="background")
    assert nested.output.get("status") == "error"
    assert "cannot spawn" in str(nested.output.get("error") or "").lower()


@pytest.mark.asyncio
async def test_get_subagent_result_scoped_to_parent(monkeypatch) -> None:
    session = _session()
    enqueued: list = []

    def _fake_enqueue(task_id):
        enqueued.append(task_id)

    async def _no_wait(*_a, **_k):
        return None

    monkeypatch.setattr(
        "app.services.tools.subagent_tools._wait_for_task",
        _no_wait,
    )

    _org, _user, _model, parent = _seed_parent(session)
    other = Chat(
        org_id=parent.org_id,
        user_id=parent.user_id,
        model_id=parent.model_id,
        title="Other",
    )
    session.add(other)
    session.commit()
    session.refresh(other)

    ctx = SubagentToolContext(
        session=session,
        parent_chat=parent,
        enqueue_generation=_fake_enqueue,
    )
    spawned = await spawn_subagent(ctx, prompt="Do work", title="Worker", mode="background")
    child_id = spawned.output["child_chat_id"]

    ok = await get_subagent_result(ctx, child_chat_id=child_id)
    assert ok.output.get("child_chat_id") == child_id

    wrong = await get_subagent_result(
        SubagentToolContext(session=session, parent_chat=other),
        child_chat_id=child_id,
    )
    assert wrong.output.get("status") == "error"


def test_chat_read_includes_subagent_fields() -> None:
    from app.api.chats import ChatRead, _chat_read

    chat = Chat(
        org_id=uuid4(),
        user_id=uuid4(),
        title="Child",
        parent_chat_id=uuid4(),
        is_subagent=True,
        is_incognito=True,
    )
    read = _chat_read(chat)
    assert isinstance(read, ChatRead)
    assert read.is_subagent is True
    assert read.parent_chat_id == str(chat.parent_chat_id)
    assert read.is_incognito is True
