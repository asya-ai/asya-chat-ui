"""Merge per-token ``delta`` generation events once a generation has finished.

While streaming, every token is persisted as its own ``delta`` row so live clients can
replay/resume. After the task is terminal nobody needs that granularity: consecutive
deltas (between tool/activity events) are merged into the first row of the run and the
rest are deleted. Ordering relative to non-delta events is preserved, so
``_build_stream_parts_from_events`` produces identical output from far fewer rows.
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session

from app.models.entities import ChatGenerationEvent, ChatGenerationTask, GenerationStatus

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = (
    GenerationStatus.completed,
    GenerationStatus.failed,
    GenerationStatus.cancelled,
)


def compact_task_delta_events(session: Session, task_id: UUID) -> int:
    """Merge consecutive delta rows for a terminal task. Returns rows deleted."""
    task = session.get(ChatGenerationTask, task_id)
    if task is None or task.status not in TERMINAL_STATUSES:
        return 0

    events = session.scalars(
        select(ChatGenerationEvent)
        .where(ChatGenerationEvent.task_id == task_id)
        .order_by(ChatGenerationEvent.sequence, ChatGenerationEvent.created_at)
    ).all()

    delete_ids: list[UUID] = []
    run_head: ChatGenerationEvent | None = None
    run_text: list[str] = []

    def flush() -> None:
        nonlocal run_head, run_text
        if run_head is not None and len(run_text) > 1:
            run_head.payload_json = {"delta": "".join(run_text)}
            flag_modified(run_head, "payload_json")
            session.add(run_head)
        run_head = None
        run_text = []

    for event in events:
        payload = event.payload_json if isinstance(event.payload_json, dict) else None
        delta = payload.get("delta") if payload else None
        is_plain_delta = (
            event.event_type == "delta"
            and isinstance(delta, str)
            and set(payload.keys()) == {"delta"}
        )
        if not is_plain_delta:
            flush()
            continue
        if run_head is None:
            run_head = event
            run_text = [delta]
        else:
            run_text.append(delta)
            delete_ids.append(event.id)
    flush()

    if delete_ids:
        for start in range(0, len(delete_ids), 1000):
            session.execute(
                delete(ChatGenerationEvent).where(
                    ChatGenerationEvent.id.in_(delete_ids[start : start + 1000])
                )
            )
    session.commit()
    return len(delete_ids)


def compact_backlog(session: Session, *, limit: int = 200, min_deltas: int = 50) -> dict[str, int]:
    """Compact older terminal tasks that still hold many per-token delta rows."""
    task_ids = session.scalars(
        select(ChatGenerationEvent.task_id)
        .join(ChatGenerationTask, ChatGenerationTask.id == ChatGenerationEvent.task_id)
        .where(ChatGenerationEvent.event_type == "delta")
        .where(ChatGenerationTask.status.in_(TERMINAL_STATUSES))
        .group_by(ChatGenerationEvent.task_id)
        .having(func.count() >= min_deltas)
        .limit(limit)
    ).all()
    tasks = 0
    deleted = 0
    for task_id in task_ids:
        try:
            deleted += compact_task_delta_events(session, task_id)
            tasks += 1
        except Exception:
            session.rollback()
            logger.warning("Delta compaction failed task=%s", task_id, exc_info=True)
    return {"tasks": tasks, "deleted": deleted}
