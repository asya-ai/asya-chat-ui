"""Fork chat copies messages without usage."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api import chats as chats_api
from app.models.entities import Chat, ChatMessage


class _ExecResult:
    def __init__(self, rows):
        self._rows = rows if isinstance(rows, list) else ([rows] if rows else [])

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


class _ForkSession:
    """Minimal session: first exec → chat, then messages, then attachments."""

    def __init__(self, source: Chat, messages: list[ChatMessage]):
        self.source = source
        self.messages = messages
        self.added: list[object] = []
        self._phase = 0

    def exec(self, _statement):
        phase = self._phase
        self._phase += 1
        if phase == 0:
            return _ExecResult(self.source)
        if phase == 1:
            # agent lookup when agent_id set, else messages
            if self.source.agent_id is not None:
                return _ExecResult(None)
            return _ExecResult(self.messages)
        if phase == 2 and self.source.agent_id is not None:
            return _ExecResult(self.messages)
        return _ExecResult([])

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid4()

    def commit(self):
        return None

    def refresh(self, obj):
        return None


def test_fork_chat_copies_messages_without_usage(monkeypatch):
    owner_id = uuid4()
    org_id = uuid4()
    source = Chat(
        id=uuid4(),
        org_id=org_id,
        user_id=owner_id,
        model_id=uuid4(),
        agent_id=None,
        title="Original",
        is_deleted=False,
        is_incognito=False,
        is_subagent=False,
        created_at=datetime.utcnow(),
        last_activity_at=datetime.utcnow(),
    )
    messages = [
        ChatMessage(
            id=uuid4(),
            chat_id=source.id,
            user_id=owner_id,
            role="user",
            content="Hello",
            is_current=True,
            status="done",
            created_at=datetime.utcnow(),
        ),
        ChatMessage(
            id=uuid4(),
            chat_id=source.id,
            role="assistant",
            content="Hi there",
            is_current=True,
            status="done",
            created_at=datetime.utcnow(),
        ),
    ]
    session = _ForkSession(source, messages)
    current_user = SimpleNamespace(
        id=owner_id,
        is_super_admin=False,
        display_name="Owner",
        username="owner",
        email="owner@example.com",
    )

    monkeypatch.setattr(chats_api, "require_org_member", lambda *a, **k: None)
    monkeypatch.setattr(chats_api, "can_access_chat", lambda *a, **k: True)
    monkeypatch.setattr(chats_api, "copy_message_attachments", lambda *a, **k: [])

    result = chats_api.fork_chat(
        chat_id=str(source.id),
        payload=chats_api.ChatForkRequest(),
        session=session,  # type: ignore[arg-type]
        current_user=current_user,  # type: ignore[arg-type]
    )

    assert result.owner_user_id == str(owner_id)
    assert result.title == "Original"
    forked_chats = [obj for obj in session.added if isinstance(obj, Chat)]
    forked_messages = [obj for obj in session.added if isinstance(obj, ChatMessage)]
    assert len(forked_chats) == 1
    assert forked_chats[0].user_id == owner_id
    assert len(forked_messages) == 2
    assert {m.role for m in forked_messages} == {"user", "assistant"}
    assert all(m.parent_id is None for m in forked_messages)
    assert not any(type(obj).__name__ == "UsageEvent" for obj in session.added)


def test_fork_chat_cuts_off_at_message(monkeypatch):
    owner_id = uuid4()
    source = Chat(
        id=uuid4(),
        org_id=uuid4(),
        user_id=owner_id,
        agent_id=None,
        title="Thread",
        is_deleted=False,
        is_incognito=False,
        is_subagent=False,
        created_at=datetime.utcnow(),
        last_activity_at=datetime.utcnow(),
    )
    first_assistant = uuid4()
    messages = [
        ChatMessage(
            id=uuid4(),
            chat_id=source.id,
            user_id=owner_id,
            role="user",
            content="Q1",
            is_current=True,
            status="done",
            created_at=datetime(2026, 1, 1, 12, 0, 0),
        ),
        ChatMessage(
            id=first_assistant,
            chat_id=source.id,
            role="assistant",
            content="A1",
            is_current=True,
            status="done",
            created_at=datetime(2026, 1, 1, 12, 0, 1),
        ),
        ChatMessage(
            id=uuid4(),
            chat_id=source.id,
            user_id=owner_id,
            role="user",
            content="Q2",
            is_current=True,
            status="done",
            created_at=datetime(2026, 1, 1, 12, 0, 2),
        ),
        ChatMessage(
            id=uuid4(),
            chat_id=source.id,
            role="assistant",
            content="A2",
            is_current=True,
            status="done",
            created_at=datetime(2026, 1, 1, 12, 0, 3),
        ),
    ]
    session = _ForkSession(source, messages)
    current_user = SimpleNamespace(
        id=owner_id,
        is_super_admin=False,
        display_name="Owner",
        username="owner",
        email="owner@example.com",
    )
    monkeypatch.setattr(chats_api, "require_org_member", lambda *a, **k: None)
    monkeypatch.setattr(chats_api, "can_access_chat", lambda *a, **k: True)
    monkeypatch.setattr(chats_api, "copy_message_attachments", lambda *a, **k: [])

    chats_api.fork_chat(
        chat_id=str(source.id),
        payload=chats_api.ChatForkRequest(message_id=str(first_assistant)),
        session=session,  # type: ignore[arg-type]
        current_user=current_user,  # type: ignore[arg-type]
    )
    forked_messages = [obj for obj in session.added if isinstance(obj, ChatMessage)]
    assert [m.content for m in forked_messages] == ["Q1", "A1"]


def test_fork_rejects_inaccessible(monkeypatch):
    source = Chat(
        id=uuid4(),
        org_id=uuid4(),
        user_id=uuid4(),
        is_deleted=False,
        agent_id=None,
    )
    session = _ForkSession(source, [])
    current_user = SimpleNamespace(id=uuid4(), is_super_admin=False)
    monkeypatch.setattr(chats_api, "require_org_member", lambda *a, **k: None)
    monkeypatch.setattr(chats_api, "can_access_chat", lambda *a, **k: False)

    with pytest.raises(HTTPException) as exc:
        chats_api.fork_chat(
            chat_id=str(source.id),
            payload=chats_api.ChatForkRequest(),
            session=session,  # type: ignore[arg-type]
            current_user=current_user,  # type: ignore[arg-type]
        )
    assert exc.value.status_code == 403
