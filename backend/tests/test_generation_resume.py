"""Tests for requeue_interrupted_generation_tasks on worker boot."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.entities import (
    Chat,
    ChatGenerationEvent,
    ChatGenerationTask,
    ChatMessage,
    ChatModel,
    GenerationStatus,
    Org,
    User,
)
from app.workers.tasks import (
    INTERRUPTED_IDLE_AFTER,
    RESUME_ACTIVITY_LABEL,
    requeue_interrupted_generation_tasks,
)


def _engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _session(engine) -> Session:
    SQLModel.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            Org.__table__,
            ChatModel.__table__,
            Chat.__table__,
            ChatMessage.__table__,
            ChatGenerationTask.__table__,
            ChatGenerationEvent.__table__,
        ],
    )
    return Session(engine)


def _seed_chat(session: Session) -> tuple[Chat, ChatMessage, ChatMessage]:
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
        title="Resume test",
    )
    session.add(chat)
    session.commit()
    session.refresh(chat)
    user_msg = ChatMessage(
        chat_id=chat.id,
        user_id=user.id,
        role="user",
        content="Update the model",
        status="done",
    )
    session.add(user_msg)
    session.commit()
    session.refresh(user_msg)
    assistant = ChatMessage(
        chat_id=chat.id,
        user_id=user.id,
        role="assistant",
        content="partial draft…",
        status="generating",
    )
    session.add(assistant)
    session.commit()
    session.refresh(assistant)
    return chat, user_msg, assistant


def test_requeue_interrupted_resets_idle_streaming(
    monkeypatch,
) -> None:
    engine = _engine()
    session = _session(engine)
    chat, user_msg, assistant = _seed_chat(session)
    now = datetime.utcnow()
    started = now - INTERRUPTED_IDLE_AFTER - timedelta(seconds=30)
    task = ChatGenerationTask(
        chat_id=chat.id,
        user_message_id=user_msg.id,
        assistant_message_id=assistant.id,
        status=GenerationStatus.streaming,
        started_at=started,
        created_at=started,
        metadata_json={"model_name": "GPT Test", "web_search_enabled": True},
    )
    session.add(task)
    session.commit()
    session.refresh(task)
    session.add(
        ChatGenerationEvent(
            task_id=task.id,
            event_type="delta",
            payload_json={"delta": "partial"},
            sequence=1,
            created_at=started,
        )
    )
    session.commit()

    enqueued: list = []
    monkeypatch.setattr("app.workers.tasks.engine", engine)
    monkeypatch.setattr(
        "app.workers.tasks._enqueue_generation_task_id",
        lambda task_id: enqueued.append(task_id),
    )

    result = requeue_interrupted_generation_tasks(now=now)

    assert result == {"requeued": 1}
    assert enqueued == [task.id]

    refreshed = session.get(ChatGenerationTask, task.id)
    assert refreshed is not None
    assert refreshed.status == GenerationStatus.queued
    assert refreshed.started_at is None
    assert refreshed.error is None
    assert refreshed.metadata_json == {
        "model_name": "GPT Test",
        "web_search_enabled": True,
    }

    assistant_row = session.get(ChatMessage, assistant.id)
    assert assistant_row is not None
    assert assistant_row.content == "partial draft…"
    assert assistant_row.status == "generating"
    assert assistant_row.error_message is None

    events = sorted(
        session.exec(
            select(ChatGenerationEvent).where(ChatGenerationEvent.task_id == task.id)
        ).all(),
        key=lambda row: row.sequence,
    )
    assert len(events) == 2
    assert events[0].event_type == "delta"
    assert events[0].payload_json == {"delta": "partial"}
    assert events[1].event_type == "activity"
    assert events[1].payload_json == {
        "label": RESUME_ACTIVITY_LABEL,
        "state": "start",
    }


def test_requeue_skips_recent_activity(monkeypatch) -> None:
    engine = _engine()
    session = _session(engine)
    chat, user_msg, assistant = _seed_chat(session)
    now = datetime.utcnow()
    started = now - timedelta(seconds=10)
    task = ChatGenerationTask(
        chat_id=chat.id,
        user_message_id=user_msg.id,
        assistant_message_id=assistant.id,
        status=GenerationStatus.running,
        started_at=started,
        created_at=started,
    )
    session.add(task)
    session.commit()
    session.refresh(task)
    session.add(
        ChatGenerationEvent(
            task_id=task.id,
            event_type="activity",
            payload_json={"label": "Thinking", "state": "start"},
            sequence=1,
            created_at=now - timedelta(seconds=5),
        )
    )
    session.commit()

    enqueued: list = []
    monkeypatch.setattr("app.workers.tasks.engine", engine)
    monkeypatch.setattr(
        "app.workers.tasks._enqueue_generation_task_id",
        lambda task_id: enqueued.append(task_id),
    )

    result = requeue_interrupted_generation_tasks(now=now)

    assert result == {"requeued": 0}
    assert enqueued == []
    refreshed = session.get(ChatGenerationTask, task.id)
    assert refreshed is not None
    assert refreshed.status == GenerationStatus.running


def test_requeue_skips_awaiting_approval(monkeypatch) -> None:
    engine = _engine()
    session = _session(engine)
    chat, user_msg, assistant = _seed_chat(session)
    now = datetime.utcnow()
    started = now - INTERRUPTED_IDLE_AFTER - timedelta(minutes=5)
    task = ChatGenerationTask(
        chat_id=chat.id,
        user_message_id=user_msg.id,
        assistant_message_id=assistant.id,
        status=GenerationStatus.awaiting_approval,
        started_at=started,
        created_at=started,
    )
    session.add(task)
    session.commit()
    session.refresh(task)

    enqueued: list = []
    monkeypatch.setattr("app.workers.tasks.engine", engine)
    monkeypatch.setattr(
        "app.workers.tasks._enqueue_generation_task_id",
        lambda task_id: enqueued.append(task_id),
    )

    result = requeue_interrupted_generation_tasks(now=now)

    assert result == {"requeued": 0}
    assert enqueued == []
    refreshed = session.get(ChatGenerationTask, task.id)
    assert refreshed is not None
    assert refreshed.status == GenerationStatus.awaiting_approval
