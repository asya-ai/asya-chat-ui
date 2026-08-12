from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from app.models.entities import AgentSource, AgentSourceKind, AgentSourceStatus, Chat
from app.services.agents import chat_index
from app.services.agents.chat_index import (
    build_chat_transcript,
    chat_source_url,
    chat_source_visible_to_user,
    upsert_space_chat_source,
)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


def test_chat_source_url_roundtrip():
    chat_id = uuid4()
    assert chat_source_url(chat_id) == f"chat://{chat_id}"


def test_chat_source_visibility():
    owner = uuid4()
    other = uuid4()
    source = AgentSource(
        agent_id=uuid4(),
        kind=AgentSourceKind.chat,
        title="Chat",
        url=chat_source_url(uuid4()),
        content_text="user: hi",
        status=AgentSourceStatus.ready,
        metadata_json={"user_id": str(owner)},
    )
    file_source = AgentSource(
        agent_id=uuid4(),
        kind=AgentSourceKind.file,
        title="Doc",
        content_text="doc",
        status=AgentSourceStatus.ready,
    )
    assert chat_source_visible_to_user(source, owner) is True
    assert chat_source_visible_to_user(source, other) is False
    assert chat_source_visible_to_user(source, None) is False
    assert chat_source_visible_to_user(file_source, other) is True


def test_build_chat_transcript_skips_tool_roles():
    class _Session:
        def exec(self, _statement):
            return _Result(
                [
                    ("user", "Hello roadmap"),
                    ("assistant", "Here is the plan"),
                    ("tool", "should skip"),
                ]
            )

    transcript = build_chat_transcript(_Session(), uuid4())  # type: ignore[arg-type]
    assert "user: Hello roadmap" in transcript
    assert "assistant: Here is the plan" in transcript
    assert "tool:" not in transcript


def test_upsert_space_chat_source_creates_queued_chat_source(monkeypatch):
    class _Session:
        def __init__(self):
            self.added = []

        def exec(self, _statement):
            return _Result(
                [
                    ("user", "Note about budget"),
                    ("assistant", "Noted"),
                ]
            )

        def add(self, obj):
            self.added.append(obj)
            if getattr(obj, "id", None) is None:
                obj.id = uuid4()

        def flush(self):
            return None

    monkeypatch.setattr(chat_index, "find_chat_source", lambda *_args, **_kwargs: None)
    session = _Session()
    chat = Chat(
        id=uuid4(),
        org_id=uuid4(),
        user_id=uuid4(),
        agent_id=uuid4(),
        title="Budget chat",
        is_deleted=False,
        is_incognito=False,
        created_at=datetime.utcnow(),
        last_activity_at=datetime.utcnow(),
    )
    source = upsert_space_chat_source(session, chat)  # type: ignore[arg-type]
    assert source is not None
    assert source.kind == AgentSourceKind.chat
    assert source.status == AgentSourceStatus.queued
    assert source.url == chat_source_url(chat.id)
    assert source.metadata_json["user_id"] == str(chat.user_id)
    assert "budget" in source.content_text.lower()


def test_upsert_skips_incognito_and_non_space_chats():
    class _Session:
        def exec(self, _statement):
            return _Result([])

        def add(self, _obj):
            raise AssertionError("should not add")

        def flush(self):
            return None

    session = _Session()
    personal = Chat(
        id=uuid4(),
        org_id=uuid4(),
        user_id=uuid4(),
        agent_id=None,
        title="Personal",
        is_deleted=False,
        is_incognito=False,
    )
    assert upsert_space_chat_source(session, personal) is None  # type: ignore[arg-type]

    incognito = Chat(
        id=uuid4(),
        org_id=uuid4(),
        user_id=uuid4(),
        agent_id=uuid4(),
        title="Secret",
        is_deleted=False,
        is_incognito=True,
    )
    assert upsert_space_chat_source(session, incognito) is None  # type: ignore[arg-type]
