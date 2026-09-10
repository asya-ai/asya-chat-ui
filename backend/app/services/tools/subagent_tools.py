"""Parent-orchestrated subagent spawn / result tools."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlmodel import Session, select

from app.db.session import engine
from app.models.entities import (
    Chat,
    ChatGenerationTask,
    ChatMessage,
    ChatModel,
    GenerationStatus,
)
from app.services.tools.registry import ToolResult

logger = logging.getLogger(__name__)

SUBAGENT_SUMMARY_MAX_CHARS = 16_000
SUBAGENT_DATA_MAX_CHARS = 32_000
SUBAGENT_BLOCKING_TIMEOUT_SECONDS = 600
_TERMINAL = {
    GenerationStatus.completed,
    GenerationStatus.failed,
    GenerationStatus.cancelled,
}


@dataclass
class SubagentToolContext:
    session: Session
    parent_chat: Chat
    parent_task_id: UUID | None = None
    parent_metadata: dict[str, Any] | None = None
    enqueue_generation: Any | None = None


def _enqueue(task_id: UUID, enqueue_generation: Any | None) -> None:
    if enqueue_generation is not None:
        enqueue_generation(task_id)
        return
    from app.api.chats import _enqueue_generation_task

    _enqueue_generation_task(task_id)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _build_seed_content(*, prompt: str, data: str | None, title: str | None) -> str:
    parts: list[str] = []
    if title and title.strip():
        parts.append(f"Task title: {title.strip()}")
    parts.append(prompt.strip())
    if data and str(data).strip():
        parts.append("Data to process:\n" + _truncate(str(data).strip(), SUBAGENT_DATA_MAX_CHARS))
    parts.append(
        "You are a subagent. Complete the task using the tools available to you. "
        "Return a concise final answer that the parent agent can use. "
        "Do not ask the user clarifying questions."
    )
    return "\n\n".join(parts)


def _child_summary(session: Session, child_chat_id: UUID) -> str:
    message = session.exec(
        select(ChatMessage)
        .where(
            ChatMessage.chat_id == child_chat_id,
            ChatMessage.role == "assistant",
            ChatMessage.is_current.is_(True),
        )
        .order_by(ChatMessage.created_at.desc())
    ).first()
    content = (message.content or "").strip() if message else ""
    if not content:
        return ""
    return _truncate(content, SUBAGENT_SUMMARY_MAX_CHARS)


def _task_status_payload(task: ChatGenerationTask | None, *, child_chat_id: UUID) -> dict[str, Any]:
    if task is None:
        return {
            "child_chat_id": str(child_chat_id),
            "status": "unknown",
            "error": "Generation task not found",
        }
    status = task.status.value if isinstance(task.status, GenerationStatus) else str(task.status)
    return {
        "child_chat_id": str(child_chat_id),
        "task_id": str(task.id),
        "status": status,
        "error": task.error,
    }


async def _wait_for_task(
    task_id: UUID,
    *,
    timeout_seconds: float = SUBAGENT_BLOCKING_TIMEOUT_SECONDS,
) -> ChatGenerationTask | None:
    deadline = asyncio.get_event_loop().time() + timeout_seconds
    while True:
        with Session(engine) as session:
            task = session.get(ChatGenerationTask, task_id)
            if task is None:
                return None
            if task.status in _TERMINAL:
                session.expunge(task)
                return task
        if asyncio.get_event_loop().time() >= deadline:
            with Session(engine) as session:
                task = session.get(ChatGenerationTask, task_id)
                if task is not None:
                    session.expunge(task)
                return task
        await asyncio.sleep(0.75)


def _create_child_run(
    context: SubagentToolContext,
    *,
    prompt: str,
    title: str | None,
    data: str | None,
    model_id: UUID | None,
    mode: str,
) -> tuple[Chat, ChatGenerationTask]:
    parent = context.parent_chat
    if parent.is_subagent or parent.parent_chat_id is not None:
        raise ValueError("Subagents cannot spawn further subagents.")

    resolved_model_id = model_id or parent.model_id
    if resolved_model_id is None:
        raise ValueError("Parent chat has no model configured.")

    model = context.session.get(ChatModel, resolved_model_id)
    if model is None:
        raise ValueError("Model not found.")

    child = Chat(
        org_id=parent.org_id,
        user_id=parent.user_id,
        model_id=resolved_model_id,
        agent_id=parent.agent_id,
        parent_chat_id=parent.id,
        title=(title or "Subagent").strip()[:200] or "Subagent",
        is_incognito=bool(parent.is_incognito),
        is_subagent=True,
        is_pinned=False,
        share_token=None,
    )
    context.session.add(child)
    context.session.flush()

    user_message = ChatMessage(
        chat_id=child.id,
        role="user",
        content=_build_seed_content(prompt=prompt, data=data, title=title),
        status="done",
    )
    context.session.add(user_message)
    context.session.flush()

    assistant_message = ChatMessage(
        chat_id=child.id,
        role="assistant",
        content="",
        model_id=model.id,
        status="generating",
        started_at=datetime.utcnow(),
    )
    context.session.add(assistant_message)
    context.session.flush()

    parent_meta = context.parent_metadata if isinstance(context.parent_metadata, dict) else {}
    child_meta: dict[str, Any] = {
        "model_id": str(model.id),
        "model_name": model.display_name,
        "locale": parent_meta.get("locale"),
        "timezone": parent_meta.get("timezone"),
        "reasoning_effort": parent_meta.get("reasoning_effort"),
        "web_search_enabled": parent_meta.get("web_search_enabled"),
        "code_execution_enabled": parent_meta.get("code_execution_enabled"),
        "is_subagent": True,
        "parent_chat_id": str(parent.id),
        "parent_task_id": str(context.parent_task_id) if context.parent_task_id else None,
        "spawn_mode": mode,
        "subagent_title": child.title,
    }
    task = ChatGenerationTask(
        chat_id=child.id,
        user_message_id=user_message.id,
        assistant_message_id=assistant_message.id,
        status=GenerationStatus.queued,
        metadata_json=child_meta,
    )
    context.session.add(task)
    child.last_activity_at = datetime.utcnow()
    context.session.add(child)
    context.session.commit()
    context.session.refresh(child)
    context.session.refresh(task)
    _enqueue(task.id, context.enqueue_generation)
    return child, task


async def _emit_subagent_started(child: Chat, task: ChatGenerationTask, *, mode: str) -> None:
    try:
        from app.services.langchain_runtime.agentic_loop import emit_tool_event_from_tool

        await emit_tool_event_from_tool(
            {
                "type": "subagent",
                "title": child.title,
                "mode": mode,
                "child_chat_id": str(child.id),
                "task_id": str(task.id),
                "status": "running",
                "output": {
                    "child_chat_id": str(child.id),
                    "task_id": str(task.id),
                    "title": child.title,
                    "mode": mode,
                    "status": "running",
                },
            }
        )
    except Exception:
        logger.debug("Mid-flight subagent UI event skipped", exc_info=True)


async def spawn_subagent(
    context: SubagentToolContext,
    *,
    prompt: str,
    title: str | None = None,
    mode: str = "blocking",
    data: str | None = None,
    model_id: str | None = None,
) -> ToolResult:
    prompt = (prompt or "").strip()
    if not prompt:
        return ToolResult(
            name="spawn_subagent",
            output={"error": "prompt is required", "status": "error"},
        )

    normalized_mode = (mode or "blocking").strip().lower()
    if normalized_mode not in {"blocking", "background"}:
        return ToolResult(
            name="spawn_subagent",
            output={"error": "mode must be 'blocking' or 'background'", "status": "error"},
        )

    resolved_model: UUID | None = None
    if model_id and str(model_id).strip():
        try:
            resolved_model = UUID(str(model_id).strip())
        except ValueError:
            return ToolResult(
                name="spawn_subagent",
                output={"error": "Invalid model_id", "status": "error"},
            )

    try:
        child, task = _create_child_run(
            context,
            prompt=prompt,
            title=title,
            data=data,
            model_id=resolved_model,
            mode=normalized_mode,
        )
    except ValueError as exc:
        return ToolResult(
            name="spawn_subagent",
            output={"error": str(exc), "status": "error"},
        )
    except Exception as exc:
        logger.exception("Failed to spawn subagent")
        return ToolResult(
            name="spawn_subagent",
            output={"error": f"Failed to spawn subagent: {exc}", "status": "error"},
        )

    await _emit_subagent_started(child, task, mode=normalized_mode)

    base = {
        "child_chat_id": str(child.id),
        "task_id": str(task.id),
        "title": child.title,
        "mode": normalized_mode,
        "status": "running",
    }

    if normalized_mode == "background":
        return ToolResult(name="spawn_subagent", output=base)

    finished = await _wait_for_task(task.id)
    with Session(engine) as session:
        fresh_task = session.get(ChatGenerationTask, task.id)
        payload = _task_status_payload(fresh_task, child_chat_id=child.id)
        payload["title"] = child.title
        payload["mode"] = normalized_mode
        if finished is None or fresh_task is None:
            payload["status"] = "timeout"
            payload["error"] = "Timed out waiting for subagent"
            payload["summary"] = _child_summary(session, child.id) or None
            return ToolResult(name="spawn_subagent", output=payload)

        status = (
            fresh_task.status.value
            if isinstance(fresh_task.status, GenerationStatus)
            else str(fresh_task.status)
        )
        if status not in {s.value for s in _TERMINAL}:
            payload["status"] = "timeout"
            payload["error"] = (
                f"Timed out after {SUBAGENT_BLOCKING_TIMEOUT_SECONDS}s; "
                "subagent is still running — use get_subagent_result later"
            )
            payload["summary"] = _child_summary(session, child.id) or None
            return ToolResult(name="spawn_subagent", output=payload)

        payload["summary"] = _child_summary(session, child.id)
        if status != GenerationStatus.completed.value and not payload.get("summary"):
            payload["summary"] = fresh_task.error or f"Subagent ended with status={status}"
        return ToolResult(name="spawn_subagent", output=payload)


async def get_subagent_result(
    context: SubagentToolContext,
    *,
    child_chat_id: str,
) -> ToolResult:
    try:
        child_uuid = UUID(str(child_chat_id).strip())
    except ValueError:
        return ToolResult(
            name="get_subagent_result",
            output={"error": "Invalid child_chat_id", "status": "error"},
        )

    child = context.session.get(Chat, child_uuid)
    if (
        child is None
        or child.is_deleted
        or child.parent_chat_id != context.parent_chat.id
        or not child.is_subagent
    ):
        return ToolResult(
            name="get_subagent_result",
            output={"error": "Subagent not found for this chat", "status": "error"},
        )

    task = context.session.exec(
        select(ChatGenerationTask)
        .where(ChatGenerationTask.chat_id == child.id)
        .order_by(ChatGenerationTask.created_at.desc())
    ).first()
    payload = _task_status_payload(task, child_chat_id=child.id)
    payload["title"] = child.title
    payload["summary"] = _child_summary(context.session, child.id) or None
    return ToolResult(name="get_subagent_result", output=payload)


async def await_subagents(
    context: SubagentToolContext,
    *,
    child_chat_ids: list[str] | None = None,
    timeout_seconds: float | None = None,
) -> ToolResult:
    parent_id = context.parent_chat.id
    query = select(Chat).where(
        Chat.parent_chat_id == parent_id,
        Chat.is_subagent.is_(True),
        Chat.is_deleted.is_(False),
    )
    children = list(context.session.exec(query).all())
    if child_chat_ids:
        wanted = {str(item).strip() for item in child_chat_ids if str(item).strip()}
        children = [child for child in children if str(child.id) in wanted]

    if not children:
        return ToolResult(
            name="await_subagents",
            output={"results": [], "error": None},
        )

    timeout = (
        float(timeout_seconds)
        if timeout_seconds is not None
        else float(SUBAGENT_BLOCKING_TIMEOUT_SECONDS)
    )
    results: list[dict[str, Any]] = []
    for child in children:
        task = context.session.exec(
            select(ChatGenerationTask)
            .where(ChatGenerationTask.chat_id == child.id)
            .order_by(ChatGenerationTask.created_at.desc())
        ).first()
        if task and task.status not in _TERMINAL:
            await _wait_for_task(task.id, timeout_seconds=timeout)
            context.session.expire_all()
            task = context.session.get(ChatGenerationTask, task.id)
        payload = _task_status_payload(task, child_chat_id=child.id)
        payload["title"] = child.title
        payload["summary"] = _child_summary(context.session, child.id) or None
        results.append(payload)

    return ToolResult(name="await_subagents", output={"results": results})
