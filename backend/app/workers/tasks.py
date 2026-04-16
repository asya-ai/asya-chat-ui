from __future__ import annotations

import asyncio
from datetime import datetime
import inspect
import logging
from typing import Any
from uuid import UUID
import json
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlmodel import Session

from app.api.chats import (
    MAX_CONTEXT_MESSAGES,
    _attachment_image_url,
    _attachment_lines,
    _build_tool_registry,
    _estimate_tokens,
    _grounding_enabled,
    _is_image_output_model,
    _maybe_update_chat_title,
    _normalize_sources,
    _prepend_tool_guidance,
    _resolve_exec_policy,
    _run_agentic_loop,
    _truncate_messages,
)
from app.core.config import settings
from app.db.session import engine
from app.models.entities import (
    Chat,
    ChatGenerationEvent,
    ChatGenerationTask,
    ChatMessage,
    ChatMessageAttachment,
    ChatModel,
    GenerationStatus,
    Org,
    OrgModel,
    UsageEvent,
    User,
    UserMemory,
)
from app.services.org_service import require_provider_enabled
from app.services.providers.base import ChatUsage
from app.services.providers.registry import get_provider
from app.services.tools.image_tool import ImageToolContext, edit_image, generate_image
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)
SUMMARY_HEAD_MESSAGES = 4
SUMMARY_TAIL_MESSAGES = 12
SUMMARY_MAX_TRANSCRIPT_CHARS = 24000


class GenerationCancelledError(Exception):
    pass


class _DbEventSender:
    def __init__(self, session: Session, task_id: UUID, sequence_ref: list[int]) -> None:
        self._session = session
        self._task_id = task_id
        self._sequence_ref = sequence_ref

    async def send(self, payload: dict) -> None:
        self._sequence_ref[0] += 1
        event = ChatGenerationEvent(
            task_id=self._task_id,
            event_type="activity",
            payload_json=payload,
            sequence=self._sequence_ref[0],
        )
        self._session.add(event)
        try:
            self._session.commit()
        except Exception:
            # If another DB operation poisoned the transaction, recover and
            # retry once so streaming events don't crash the whole generation.
            self._session.rollback()
            self._session.add(event)
            try:
                self._session.commit()
            except Exception:
                self._session.rollback()
                logger.warning(
                    "Failed to persist activity event for task=%s",
                    self._task_id,
                    exc_info=True,
                )


class _DbToolEventSender:
    def __init__(self, session: Session, task_id: UUID, sequence_ref: list[int]) -> None:
        self._session = session
        self._task_id = task_id
        self._sequence_ref = sequence_ref

    async def send(self, payload: dict) -> None:
        self._sequence_ref[0] += 1
        event = ChatGenerationEvent(
            task_id=self._task_id,
            event_type="tool_event",
            payload_json=payload,
            sequence=self._sequence_ref[0],
        )
        self._session.add(event)
        try:
            self._session.commit()
        except Exception:
            self._session.rollback()
            self._session.add(event)
            try:
                self._session.commit()
            except Exception:
                self._session.rollback()
                logger.warning(
                    "Failed to persist tool event for task=%s",
                    self._task_id,
                    exc_info=True,
                )


def _build_provider_messages(
    *,
    history: list[ChatMessage],
    attachments_by_message: dict[UUID, list[ChatMessageAttachment]],
    model: ChatModel,
    locale: str | None,
    timezone: str | None = None,
    enabled_tool_names: list[str] | None,
    memories: list[dict[str, str]] | None = None,
) -> list[dict]:
    items: list[dict[str, Any]] = []
    latest_user_image_id: UUID | None = None
    for msg in reversed(history):
        if msg.role != "user":
            continue
        msg_attachments = attachments_by_message.get(msg.id, [])
        latest_image = next(
            (
                attachment
                for attachment in reversed(msg_attachments)
                if attachment.content_type.startswith("image/")
            ),
            None,
        )
        if latest_image:
            latest_user_image_id = latest_image.id
            break

    for msg in history:
        if msg.role != "user":
            items.append({"role": msg.role, "content": msg.content})
            continue
        msg_attachments = attachments_by_message.get(msg.id, [])
        if not msg_attachments:
            items.append({"role": msg.role, "content": msg.content})
            continue
        image_attachments = [
            attachment
            for attachment in msg_attachments
            if attachment.content_type.startswith("image/")
        ]
        # Keep only one latest image across the whole history to reduce remote fetch timeouts.
        if latest_user_image_id is not None:
            image_attachments = [
                attachment for attachment in image_attachments if attachment.id == latest_user_image_id
            ]
        else:
            image_attachments = []
        attachment_lines = _attachment_lines(msg_attachments)
        if not image_attachments:
            text = msg.content or ""
            if attachment_lines:
                text += (
                    "\n\nAttachments (available in /inputs for code execution):\n"
                    + "\n".join(attachment_lines)
                )
            items.append({"role": msg.role, "content": text})
            continue
        if model.provider not in {"openai", "azure", "gemini"}:
            raise ValueError("Images are not supported for this model provider")
        content_parts: list[dict[str, Any]] = []
        if msg.content:
            content_parts.append({"type": "text", "text": msg.content})
        if attachment_lines:
            content_parts.append(
                {
                    "type": "text",
                    "text": "Attachments (available in /inputs for code execution):\n"
                    + "\n".join(attachment_lines),
                }
            )
        for attachment in image_attachments:
            content_parts.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": _attachment_image_url(attachment)
                    },
                }
            )
        items.append({"role": msg.role, "content": content_parts})
    return _prepend_tool_guidance(
        items,
        locale=locale,
        timezone=timezone,
        enabled_tool_names=enabled_tool_names,
        memories=memories,
    )


def _append_event(
    session: Session,
    task_id: UUID,
    sequence_ref: list[int],
    event_type: str,
    payload: dict | None,
) -> None:
    sequence_ref[0] += 1
    session.add(
        ChatGenerationEvent(
            task_id=task_id,
            event_type=event_type,
            payload_json=payload,
            sequence=sequence_ref[0],
        )
    )
    session.commit()


def _to_int_scalar(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        return int(value)
    if isinstance(value, tuple):
        return _to_int_scalar(value[0] if value else None, default=default)
    mapping = getattr(value, "_mapping", None)
    if mapping:
        first = next(iter(mapping.values()), None)
        return _to_int_scalar(first, default=default)
    return default


def _ensure_task_not_cancelled(session: Session, task_id: UUID) -> None:
    current = session.get(ChatGenerationTask, task_id)
    if current and current.status == GenerationStatus.cancelled:
        raise GenerationCancelledError("Generation cancelled by user")


async def _maybe_close_provider(provider: Any) -> None:
    async def _close(target: Any) -> bool:
        for method_name in ("aclose", "close"):
            fn = getattr(target, method_name, None)
            if not callable(fn):
                continue
            try:
                result = fn()
                if inspect.isawaitable(result):
                    await result
            except Exception:
                logger.debug(
                    "Provider cleanup failed for %s.%s",
                    target.__class__.__name__,
                    method_name,
                    exc_info=True,
                )
            return True
        return False

    if provider is None:
        return
    if await _close(provider):
        return
    client = getattr(provider, "client", None)
    if client is None:
        return
    await _close(client)


def _message_text_for_summary(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") in {"text", "input_text"} and item.get("text"):
                parts.append(str(item.get("text")))
            elif item.get("type") == "image_url":
                parts.append("[image]")
        return "\n".join(parts)
    return ""


def _build_summary_transcript(messages: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for message in messages:
        role = str(message.get("role", "user")).upper()
        text = _message_text_for_summary(message).strip()
        if not text:
            continue
        lines.append(f"{role}: {text}")
    transcript = "\n\n".join(lines)
    if len(transcript) > SUMMARY_MAX_TRANSCRIPT_CHARS:
        return transcript[-SUMMARY_MAX_TRANSCRIPT_CHARS:]
    return transcript


async def _summarize_context_if_needed(
    *,
    session: Session,
    task_id: UUID,
    sequence_ref: list[int],
    provider: Any,
    model: ChatModel,
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    token_limit = model.context_length
    if token_limit is None:
        return messages
    if len(messages) <= MAX_CONTEXT_MESSAGES and _estimate_tokens(messages) <= token_limit:
        return messages

    head = messages[:SUMMARY_HEAD_MESSAGES]
    tail = messages[-SUMMARY_TAIL_MESSAGES:]
    middle = messages[SUMMARY_HEAD_MESSAGES:-SUMMARY_TAIL_MESSAGES]
    if not middle:
        return _truncate_messages(messages, token_limit=token_limit)

    transcript = _build_summary_transcript(middle)
    if not transcript.strip():
        return _truncate_messages(messages, token_limit=token_limit)

    summary_request = [
        {
            "role": "system",
            "content": (
                "Summarize the earlier chat context so another assistant can continue seamlessly. "
                "Preserve user intent, constraints, decisions, unresolved tasks, and key facts. "
                "Use concise markdown bullets."
            ),
        },
        {
            "role": "user",
            "content": f"Summarize this prior conversation segment:\n\n{transcript}",
        },
    ]
    try:
        summary_response = await provider.chat(model.model_name, summary_request)
        summary_text = (summary_response.content or "").strip()
        summary_usage = getattr(summary_response, "usage", None)
        if summary_usage:
            task_row = session.get(ChatGenerationTask, task_id)
            chat_row = session.get(Chat, task_row.chat_id) if task_row else None
            if chat_row:
                session.add(
                    UsageEvent(
                        org_id=chat_row.org_id,
                        user_id=chat_row.user_id,
                        chat_id=chat_row.id,
                        message_id=task_row.assistant_message_id if task_row else None,
                        model_id=model.id,
                        prompt_tokens=summary_usage.prompt_tokens,
                        completion_tokens=summary_usage.completion_tokens,
                        total_tokens=summary_usage.total_tokens,
                        input_tokens=summary_usage.input_tokens,
                        output_tokens=summary_usage.output_tokens,
                        cached_tokens=summary_usage.cached_tokens,
                        thinking_tokens=summary_usage.thinking_tokens,
                    )
                )
                session.commit()
    except Exception:
        logger.exception("Context summarization failed for task=%s", task_id)
        return _truncate_messages(messages, token_limit=token_limit)

    if not summary_text:
        return _truncate_messages(messages, token_limit=token_limit)

    summary_message = {
        "role": "system",
        "content": f"Conversation summary so far:\n{summary_text}",
    }
    summarized_messages = [*head, summary_message, *tail]
    if _estimate_tokens(summarized_messages) > token_limit:
        summarized_messages = [summary_message, *tail]
    if _estimate_tokens(summarized_messages) > token_limit:
        summarized_messages = _truncate_messages(summarized_messages, token_limit=token_limit)

    _append_event(
        session,
        task_id,
        sequence_ref,
        "tool_event",
        {
            "type": "context_summary",
            "id": str(uuid4()),
            "summary": summary_text,
            "output": {
                "original_message_count": len(messages),
                "used_message_count": len(summarized_messages),
            },
        },
    )
    return summarized_messages


async def _run_generation(task_id: UUID) -> None:
    with Session(engine) as session:
        task = session.get(ChatGenerationTask, task_id)
        if not task:
            logger.warning("Generation task not found: %s", task_id)
            return
        if task.status != GenerationStatus.queued:
            logger.info(
                "Skipping task=%s with status=%s (already claimed or finished)",
                task_id,
                task.status,
            )
            return

        chat = session.get(Chat, task.chat_id)
        if not chat or chat.is_deleted:
            task.status = GenerationStatus.failed
            task.error = "Chat not found"
            session.commit()
            return

        model = session.get(ChatModel, chat.model_id) if chat.model_id else None
        if not model:
            task.status = GenerationStatus.failed
            task.error = "Model not found"
            session.commit()
            return

        org = session.get(Org, chat.org_id)
        if not org:
            task.status = GenerationStatus.failed
            task.error = "Org not found"
            session.commit()
            return

        enabled = session.scalars(
            select(OrgModel).where(
                OrgModel.org_id == chat.org_id,
                OrgModel.model_id == model.id,
                OrgModel.is_enabled.is_(True),
            )
        ).first()
        if not enabled:
            task.status = GenerationStatus.failed
            task.error = "Model is not enabled for this organization"
            session.commit()
            return

        history = session.scalars(
            select(ChatMessage)
            .where(ChatMessage.chat_id == chat.id)
            .where(ChatMessage.is_current.is_(True))
            .order_by(ChatMessage.created_at)
        ).all()
        history = [msg for msg in history if msg.id != task.assistant_message_id]
        history_attachments = session.scalars(
            select(ChatMessageAttachment).where(
                ChatMessageAttachment.message_id.in_([message.id for message in history])
            )
        ).all()
        attachments_by_message: dict[UUID, list[ChatMessageAttachment]] = {}
        for attachment in history_attachments:
            attachments_by_message.setdefault(attachment.message_id, []).append(attachment)

        provider: Any | None = None
        provider_config = require_provider_enabled(session, chat.org_id, model.provider)
        config = None
        if provider_config and provider_config.config_json:
            try:
                config = json.loads(provider_config.config_json)
            except json.JSONDecodeError:
                config = None

        prompt_cache_key = f"chat:{chat.id}"
        provider = get_provider(
            model.provider,
            api_key=provider_config.api_key_override if provider_config else None,
            base_url=provider_config.base_url_override if provider_config else None,
            endpoint=provider_config.endpoint_override if provider_config else None,
            reasoning_effort=model.reasoning_effort,
            prompt_cache_key=prompt_cache_key,
            prompt_cache_retention=settings.openai_prompt_cache_retention,
            config=config,
        )

        sequence = session.exec(
            select(func.max(ChatGenerationEvent.sequence)).where(
                ChatGenerationEvent.task_id == task.id
            )
        ).one_or_none()
        sequence_ref = [_to_int_scalar(sequence, default=0)]

        claimed = session.exec(
            update(ChatGenerationTask)
            .where(
                ChatGenerationTask.id == task.id,
                ChatGenerationTask.status == GenerationStatus.queued,
            )
            .values(
                status=GenerationStatus.running,
                started_at=datetime.utcnow(),
                metadata_json={
                    "model_id": str(model.id),
                    "model_name": model.display_name,
                    **(task.metadata_json or {}),
                },
            )
        )
        session.commit()
        if (claimed.rowcount or 0) == 0:
            logger.info("Task=%s was claimed by another worker; skipping", task.id)
            await _maybe_close_provider(provider)
            return
        task = session.get(ChatGenerationTask, task.id) or task

        assistant_message = session.get(ChatMessage, task.assistant_message_id)
        if not assistant_message:
            task.status = GenerationStatus.failed
            task.error = "Assistant message not found"
            session.commit()
            await _maybe_close_provider(provider)
            return

        task.status = GenerationStatus.streaming
        assistant_message.status = "generating"
        assistant_message.started_at = datetime.utcnow()
        session.add(assistant_message)
        session.commit()

        chat_user = session.get(User, chat.user_id)
        tool_registry = _build_tool_registry(
            session,
            chat.org_id,
            chat_id=chat.id,
            preferred_provider=model.provider,
            web_tools_enabled=not _grounding_enabled(org, model.provider),
            web_search_enabled=(
                org.web_search_enabled
                if not task.metadata_json
                or task.metadata_json.get("web_search_enabled") is None
                else org.web_search_enabled
                and bool(task.metadata_json.get("web_search_enabled"))
            ),
            web_scrape_enabled=org.web_scrape_enabled,
            exec_policy=(
                _resolve_exec_policy(
                    org.exec_policy,
                    (task.metadata_json or {}).get("code_execution_enabled"),
                )
            ),
            exec_network_enabled=org.exec_network_enabled,
            locale=task.metadata_json.get("locale") if task.metadata_json else None,
            memory_enabled=chat_user.memory_enabled if chat_user else False,
            user_id=chat.user_id,
        )
        user_memories = None
        if chat_user and chat_user.memory_enabled:
            mem_rows = session.scalars(
                select(UserMemory)
                .where(UserMemory.user_id == chat_user.id)
                .order_by(UserMemory.created_at)
            ).all()
            if mem_rows:
                user_memories = [{"id": str(m.id), "content": m.content} for m in mem_rows]
        messages = _build_provider_messages(
            history=history,
            attachments_by_message=attachments_by_message,
            model=model,
            locale=task.metadata_json.get("locale") if task.metadata_json else None,
            timezone=task.metadata_json.get("timezone") if task.metadata_json else None,
            enabled_tool_names=[spec.name for spec in tool_registry.list_specs()],
            memories=user_memories,
        )
        messages = await _summarize_context_if_needed(
            session=session,
            task_id=task.id,
            sequence_ref=sequence_ref,
            provider=provider,
            model=model,
            messages=messages,
        )

        usage = ChatUsage(0, 0, 0, 0, 0, 0, 0)
        tool_attachments: list[dict] | None = None
        tool_sources: list[dict] | None = None
        image_usages: list[dict] = []

        try:
            _ensure_task_not_cancelled(session, task.id)
            if _is_image_output_model(model):
                latest_user_message = next(
                    (item for item in reversed(history) if item.role == "user"),
                    None,
                )
                latest_user_attachments = (
                    attachments_by_message.get(latest_user_message.id, [])
                    if latest_user_message
                    else []
                )
                latest_image_attachment = next(
                    (
                        item
                        for item in reversed(latest_user_attachments)
                        if item.content_type.startswith("image/")
                    ),
                    None,
                )
                image_prompt = (
                    latest_user_message.content
                    if latest_user_message and latest_user_message.content
                    else (history[-1].content if history else "")
                )
                if latest_user_attachments:
                    attachment_lines = "\n".join(
                        f"- {attachment.file_name} ({attachment.content_type})"
                        for attachment in latest_user_attachments
                    )
                    image_prompt = (
                        f"{image_prompt}\n\nUser-attached files:\n{attachment_lines}"
                    ).strip()
                _append_event(
                    session,
                    task.id,
                    sequence_ref,
                    "activity",
                    {"label": "Generating image", "state": "start"},
                )
                if latest_image_attachment:
                    image_result = await edit_image(
                        ImageToolContext(
                            session=session, org_id=str(chat.org_id), chat_id=str(chat.id)
                        ),
                        prompt=image_prompt,
                        image_id=str(latest_image_attachment.id),
                        model_override=model,
                    )
                else:
                    image_result = await generate_image(
                        ImageToolContext(
                            session=session, org_id=str(chat.org_id), chat_id=str(chat.id)
                        ),
                        prompt=image_prompt,
                        model_override=model,
                    )
                _ensure_task_not_cancelled(session, task.id)
                if image_result.attachments:
                    session.add_all(
                        [
                            ChatMessageAttachment(
                                message_id=assistant_message.id,
                                file_name=item["file_name"],
                                content_type=item["content_type"],
                                data_base64=item["data_base64"],
                            )
                            for item in image_result.attachments
                        ]
                    )
                    session.commit()
                usage_event = UsageEvent(
                    org_id=chat.org_id,
                    user_id=chat.user_id,
                    chat_id=chat.id,
                    message_id=assistant_message.id,
                    model_id=model.id,
                    prompt_tokens=0,
                    completion_tokens=0,
                    total_tokens=0,
                    input_tokens=0,
                    output_tokens=0,
                    cached_tokens=0,
                    thinking_tokens=0,
                    image_width=image_result.output.get("image_width"),
                    image_height=image_result.output.get("image_height"),
                    image_count=image_result.output.get("image_count"),
                    image_format=image_result.output.get("image_format"),
                )
                session.add(usage_event)
                session.commit()
                await _maybe_update_chat_title(
                    session=session,
                    chat=chat,
                    provider=provider,
                    model=model,
                    history=history + [assistant_message],
                )
                _append_event(
                    session,
                    task.id,
                    sequence_ref,
                    "done",
                    {
                        "done": True,
                        "message_id": str(assistant_message.id),
                        "content": "",
                        "model_name": model.display_name,
                        "model_id": str(model.id),
                        "attachments": image_result.attachments or [],
                    },
                )
                assistant_message.status = "done"
                assistant_message.completed_at = datetime.utcnow()
                session.add(assistant_message)
                task.status = GenerationStatus.completed
                task.completed_at = datetime.utcnow()
                session.commit()
                return

            grounding_enabled = _grounding_enabled(org, model.provider)
            if grounding_enabled and hasattr(provider, "chat_grounded"):
                response = await provider.chat_grounded(model.model_name, messages)
                _ensure_task_not_cancelled(session, task.id)
                response.sources = await _normalize_sources(response.sources or [])
                assistant_message.content = response.content or ""
                session.add(assistant_message)
                session.commit()
                usage = response.usage
                tool_sources = response.sources or None
                _append_event(
                    session,
                    task.id,
                    sequence_ref,
                    "delta",
                    {"delta": assistant_message.content},
                )
            elif tool_registry and hasattr(provider, "chat_with_tools"):
                activity_sender = _DbEventSender(session, task.id, sequence_ref)
                tool_event_sender = _DbToolEventSender(session, task.id, sequence_ref)
                content, tool_attachments, tool_sources, image_usages, last_usage = (
                    await _run_agentic_loop(
                        provider=provider,
                        model=model,
                        messages=messages,
                        tool_registry=tool_registry,
                        activity_sender=activity_sender,
                        tool_event_sender=tool_event_sender,
                    )
                )
                _ensure_task_not_cancelled(session, task.id)
                assistant_message.content = content
                session.add(assistant_message)
                session.commit()
                usage = last_usage or usage
                if tool_sources:
                    tool_sources = await _normalize_sources(tool_sources)
                    assistant_message.sources = tool_sources
                    session.add(assistant_message)
                    session.commit()
                if tool_attachments:
                    session.add_all(
                        [
                            ChatMessageAttachment(
                                message_id=assistant_message.id,
                                file_name=item["file_name"],
                                content_type=item["content_type"],
                                data_base64=item["data_base64"],
                            )
                            for item in tool_attachments
                        ]
                    )
                    session.commit()
                _append_event(
                    session,
                    task.id,
                    sequence_ref,
                    "delta",
                    {"delta": content},
                )
            else:
                if hasattr(provider, "chat_stream"):
                    assistant_content = ""
                    async for chunk in provider.chat_stream(model.model_name, messages):
                        _ensure_task_not_cancelled(session, task.id)
                        if chunk.content:
                            assistant_content += chunk.content
                            assistant_message.content = assistant_content
                            session.add(assistant_message)
                            session.commit()
                            _append_event(
                                session,
                                task.id,
                                sequence_ref,
                                "delta",
                                {"delta": chunk.content},
                            )
                        if chunk.usage:
                            usage = chunk.usage
                    assistant_message.content = assistant_content
                    session.add(assistant_message)
                    session.commit()
                else:
                    response = await provider.chat(model.model_name, messages)
                    _ensure_task_not_cancelled(session, task.id)
                    assistant_message.content = response.content or ""
                    session.add(assistant_message)
                    session.commit()
                    usage = response.usage
                    _append_event(
                        session,
                        task.id,
                        sequence_ref,
                        "delta",
                        {"delta": assistant_message.content},
                    )

            usage_event = UsageEvent(
                org_id=chat.org_id,
                user_id=chat.user_id,
                chat_id=chat.id,
                message_id=assistant_message.id,
                model_id=model.id,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                total_tokens=usage.total_tokens,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cached_tokens=usage.cached_tokens,
                thinking_tokens=usage.thinking_tokens,
            )
            session.add(usage_event)
            session.commit()
            if image_usages:
                for item in image_usages:
                    session.add(
                        UsageEvent(
                            org_id=chat.org_id,
                            user_id=chat.user_id,
                            chat_id=chat.id,
                            message_id=assistant_message.id,
                            model_id=UUID(item["model_id"]),
                            prompt_tokens=item["prompt_tokens"],
                            completion_tokens=item["completion_tokens"],
                            total_tokens=item["total_tokens"],
                            input_tokens=item["input_tokens"],
                            output_tokens=item["output_tokens"],
                            cached_tokens=item["cached_tokens"],
                            thinking_tokens=item["thinking_tokens"],
                            image_width=item.get("image_width"),
                            image_height=item.get("image_height"),
                            image_count=item.get("image_count"),
                            image_format=item.get("image_format"),
                        )
                    )
                session.commit()

            await _maybe_update_chat_title(
                session=session,
                chat=chat,
                provider=provider,
                model=model,
                history=history + [assistant_message],
            )

            _append_event(
                session,
                task.id,
                sequence_ref,
                "done",
                {
                    "done": True,
                    "message_id": str(assistant_message.id),
                    "content": assistant_message.content,
                    "model_name": model.display_name,
                    "model_id": str(model.id),
                    "attachments": tool_attachments or [],
                    "sources": tool_sources or [],
                },
            )
            assistant_message.status = "done"
            assistant_message.completed_at = datetime.utcnow()
            session.add(assistant_message)
            task.status = GenerationStatus.completed
            task.completed_at = datetime.utcnow()
            session.commit()
        except GenerationCancelledError:
            logger.info("Generation cancelled for task=%s", task_id)
            return
        except Exception as exc:  # noqa: BLE001
            exc_name = type(exc).__name__
            if exc_name in ("SoftTimeLimitExceeded", "TimeLimitExceeded"):
                logger.error("Generation timed out for task=%s", task_id)
                exc = Exception("Generation timed out. The request took too long to complete.")
            else:
                logger.exception("Generation failed for task=%s", task_id)
            # Clear failed transaction state before any ORM writes.
            try:
                session.rollback()
            except Exception:
                pass
            assistant_message.status = "failed"
            assistant_message.completed_at = datetime.utcnow()
            assistant_message.error_message = str(exc)
            session.add(assistant_message)
            task.status = GenerationStatus.failed
            task.error = str(exc)
            session.commit()
            _append_event(
                session,
                task.id,
                sequence_ref,
                "error",
                {"error": str(exc)},
            )
        finally:
            await _maybe_close_provider(provider)


@celery_app.task(name="chatui.generate_chat_response")
def generate_chat_response(task_id: str) -> None:
    asyncio.run(_run_generation(UUID(task_id)))
