"""Persist agentic-loop state so interrupted generations can resume."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlmodel import Session

from app.models.entities import ChatGenerationTask, UsageEvent
from app.services.providers.base import ChatUsage
from app.services.tool_usage import persist_tool_model_usage_events

CHECKPOINT_VERSION = 1


def usage_to_dict(usage: ChatUsage | None) -> dict[str, int] | None:
    if usage is None:
        return None
    return {
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cached_tokens": usage.cached_tokens,
        "thinking_tokens": usage.thinking_tokens,
    }


def usage_from_dict(data: dict[str, Any] | None) -> ChatUsage | None:
    if not isinstance(data, dict):
        return None
    try:
        return ChatUsage(
            prompt_tokens=int(data.get("prompt_tokens") or 0),
            completion_tokens=int(data.get("completion_tokens") or 0),
            total_tokens=int(data.get("total_tokens") or 0),
            input_tokens=int(data.get("input_tokens") or 0),
            output_tokens=int(data.get("output_tokens") or 0),
            cached_tokens=int(data.get("cached_tokens") or 0),
            thinking_tokens=int(data.get("thinking_tokens") or 0),
        )
    except (TypeError, ValueError):
        return None


def build_checkpoint_payload(
    *,
    next_step: int,
    messages: list[dict[str, Any]],
    attachments: list[dict[str, Any]],
    scrape_sources: list[dict[str, Any]],
    other_sources: list[dict[str, Any]],
    image_usages: list[dict[str, Any]],
    usage: ChatUsage | None,
    consecutive_empty: int,
    consecutive_length: int,
) -> dict[str, Any]:
    return {
        "v": CHECKPOINT_VERSION,
        "next_step": next_step,
        "messages": messages,
        "attachments": attachments,
        "scrape_sources": scrape_sources,
        "other_sources": other_sources,
        "image_usages": image_usages,
        "usage": usage_to_dict(usage),
        "consecutive_empty": consecutive_empty,
        "consecutive_length": consecutive_length,
    }


def load_agent_checkpoint(
    metadata_json: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(metadata_json, dict):
        return None
    raw = metadata_json.get("agent_checkpoint")
    if not isinstance(raw, dict):
        return None
    if raw.get("v") != CHECKPOINT_VERSION:
        return None
    if not isinstance(raw.get("messages"), list):
        return None
    return raw


def persist_agent_checkpoint(
    session: Session,
    task_id: UUID,
    payload: dict[str, Any],
) -> None:
    task = session.get(ChatGenerationTask, task_id)
    if task is None:
        return
    meta = dict(task.metadata_json or {})
    meta["agent_checkpoint"] = payload
    task.metadata_json = meta
    session.add(task)
    session.commit()


def merge_chat_usage(
    first: ChatUsage | None,
    second: ChatUsage | None,
) -> ChatUsage | None:
    if first is None and second is None:
        return None
    if first is None:
        return second
    if second is None:
        return first
    return ChatUsage(
        prompt_tokens=(first.prompt_tokens or 0) + (second.prompt_tokens or 0),
        completion_tokens=(first.completion_tokens or 0) + (second.completion_tokens or 0),
        total_tokens=(first.total_tokens or 0) + (second.total_tokens or 0),
        input_tokens=(first.input_tokens or 0) + (second.input_tokens or 0),
        output_tokens=(first.output_tokens or 0) + (second.output_tokens or 0),
        cached_tokens=(first.cached_tokens or 0) + (second.cached_tokens or 0),
        thinking_tokens=(first.thinking_tokens or 0) + (second.thinking_tokens or 0),
    )


def usage_has_tokens(usage: ChatUsage | None) -> bool:
    if usage is None:
        return False
    return any(
        int(getattr(usage, field, 0) or 0) > 0
        for field in (
            "total_tokens",
            "input_tokens",
            "output_tokens",
            "prompt_tokens",
            "completion_tokens",
            "thinking_tokens",
        )
    )


def resolve_generation_usage(
    runtime_usage: ChatUsage | None,
    metadata_json: dict[str, Any] | None,
) -> ChatUsage | None:
    checkpoint = load_agent_checkpoint(metadata_json)
    checkpoint_usage = (
        usage_from_dict(checkpoint.get("usage"))
        if checkpoint is not None
        else None
    )
    if not usage_has_tokens(runtime_usage):
        return checkpoint_usage
    if not usage_has_tokens(checkpoint_usage):
        return runtime_usage
    runtime_total = int(runtime_usage.total_tokens or 0)
    checkpoint_total = int(checkpoint_usage.total_tokens or 0)
    return runtime_usage if runtime_total >= checkpoint_total else checkpoint_usage


def resolve_generation_image_usages(
    runtime_image_usages: list[dict[str, Any]] | None,
    metadata_json: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if runtime_image_usages:
        return list(runtime_image_usages)
    checkpoint = load_agent_checkpoint(metadata_json)
    if not checkpoint:
        return []
    raw = checkpoint.get("image_usages")
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return []


def persist_partial_generation_usage(
    session: Session,
    *,
    task_id: UUID,
    org_id: UUID,
    user_id: UUID,
    chat_id: UUID,
    message_id: UUID,
    model_id: UUID,
    runtime_usage: ChatUsage | None,
    image_usages: list[dict[str, Any]] | None,
    metadata_json: dict[str, Any] | None,
) -> None:
    """Record LLM usage accumulated before cancel/failure (idempotent per task)."""
    task = session.get(ChatGenerationTask, task_id)
    if task is None:
        return
    meta = dict(metadata_json or {}) if isinstance(metadata_json, dict) else {}
    if meta.get("generation_usage_persisted"):
        return

    usage = resolve_generation_usage(runtime_usage, meta)
    image_items = resolve_generation_image_usages(image_usages, meta)

    if usage_has_tokens(usage):
        session.add(
            UsageEvent(
                org_id=org_id,
                user_id=user_id,
                chat_id=chat_id,
                message_id=message_id,
                model_id=model_id,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                total_tokens=usage.total_tokens,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cached_tokens=usage.cached_tokens,
                thinking_tokens=usage.thinking_tokens,
            )
        )

    if image_items:
        persist_tool_model_usage_events(
            session,
            org_id=org_id,
            user_id=user_id,
            chat_id=chat_id,
            message_id=message_id,
            items=image_items,
        )

    if not usage_has_tokens(usage) and not image_items:
        return

    meta["generation_usage_persisted"] = True
    task.metadata_json = meta
    session.add(task)
    session.commit()


def mark_generation_usage_persisted(session: Session, task_id: UUID) -> None:
    task = session.get(ChatGenerationTask, task_id)
    if task is None:
        return
    meta = dict(task.metadata_json or {}) if isinstance(task.metadata_json, dict) else {}
    if meta.get("generation_usage_persisted"):
        return
    meta["generation_usage_persisted"] = True
    task.metadata_json = meta
    session.add(task)


def clear_agent_checkpoint(session: Session, task_id: UUID) -> None:
    task = session.get(ChatGenerationTask, task_id)
    if task is None:
        return
    meta = dict(task.metadata_json or {})
    if "agent_checkpoint" not in meta:
        return
    meta.pop("agent_checkpoint", None)
    task.metadata_json = meta
    session.add(task)
    session.commit()
