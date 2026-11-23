"""Delta compaction must keep the rebuilt timeline identical while dropping rows."""

from __future__ import annotations

from sqlmodel import select

from app.api.chats import _build_stream_parts_from_events
from app.models.entities import ChatGenerationEvent, ChatGenerationTask, GenerationStatus
from app.services.generation_event_compaction import compact_backlog, compact_task_delta_events
from tests.test_generation_resume import _engine, _seed_chat, _session


def _make_task(session, status):
    chat, user_msg, assistant = _seed_chat(session)
    task = ChatGenerationTask(
        chat_id=chat.id,
        user_message_id=user_msg.id,
        assistant_message_id=assistant.id,
        status=status,
    )
    session.add(task)
    session.commit()
    session.refresh(task)
    events = [
        ("delta", {"delta": "Hel"}),
        ("delta", {"delta": "lo "}),
        ("activity", {"label": "Searching the web", "state": "start"}),
        ("tool_event", {"type": "tool_call", "id": "t1", "tool_name": "web", "state": "start"}),
        ("delta", {"delta": "wor"}),
        ("delta", {"delta": "ld"}),
        ("delta", {"delta": "!"}),
        ("done", {"done": True}),
    ]
    for seq, (event_type, payload) in enumerate(events, start=1):
        session.add(
            ChatGenerationEvent(
                task_id=task.id, event_type=event_type, payload_json=payload, sequence=seq
            )
        )
    session.commit()
    return task


def _events(session, task):
    return session.exec(
        select(ChatGenerationEvent)
        .where(ChatGenerationEvent.task_id == task.id)
        .where(ChatGenerationEvent.event_type.in_(["tool_event", "activity", "delta"]))
        .order_by(ChatGenerationEvent.sequence)
    ).all()


def test_compaction_merges_runs_and_preserves_timeline() -> None:
    session = _session(_engine())
    task = _make_task(session, GenerationStatus.completed)
    before = _build_stream_parts_from_events(_events(session, task), message_content="Hello world!")

    deleted = compact_task_delta_events(session, task.id)

    assert deleted == 3
    after_events = _events(session, task)
    assert [e.payload_json.get("delta") for e in after_events if e.event_type == "delta"] == [
        "Hello ",
        "world!",
    ]
    after = _build_stream_parts_from_events(after_events, message_content="Hello world!")
    assert after == before
    # Idempotent.
    assert compact_task_delta_events(session, task.id) == 0


def test_compaction_skips_running_tasks() -> None:
    session = _session(_engine())
    task = _make_task(session, GenerationStatus.streaming)
    assert compact_task_delta_events(session, task.id) == 0


def test_backlog_compacts_terminal_tasks() -> None:
    session = _session(_engine())
    task = _make_task(session, GenerationStatus.completed)
    result = compact_backlog(session, min_deltas=2)
    assert result == {"tasks": 1, "deleted": 3}
    assert compact_backlog(session, min_deltas=3) == {"tasks": 0, "deleted": 0}
    assert task.id
