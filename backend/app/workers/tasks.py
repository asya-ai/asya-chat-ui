from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import inspect
import logging
from typing import Any
from uuid import UUID
import json
from uuid import uuid4

from sqlalchemy import delete, func, select, update
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
    _dedupe_sources,
    _normalize_sources,
    _prepend_tool_guidance,
    _effective_web_tool_enabled,
    _resolve_exec_policy,
    _run_agentic_loop,
    _truncate_messages,
    build_message_usage_map,
)
from app.services.model_capabilities import (
    ensure_model_capabilities,
    persist_responses_api_discovery,
    supports_image_input,
)
from app.core.config import settings
from app.db.session import engine
from app.models.entities import (
    Agent,
    AgentSource,
    AgentSourceStatus,
    Chat,
    ChatGenerationEvent,
    ChatGenerationTask,
    ChatMessage,
    ChatMessageAttachment,
    ChatModel,
    ChatUpload,
    ChatViewEvent,
    GenerationStatus,
    Org,
    UsageEvent,
    User,
    UserMemory,
)
from app.services.org_service import require_provider_enabled
from app.services.team_service import allowed_model_ids
from app.services.file_storage import delete_file
from app.services.agents.runtime import reindex_source
from app.services.agents.chat_index import (
    enqueue_space_chat_index,
    upsert_space_chat_source,
)
from app.services.tools.code_execution import project_source_exec_path
from app.services.langchain_runtime import (
    chat_stream_with_langchain,
    chat_with_langchain,
    retrieve_agent_chunks,
)
from app.services.generation_event_bus import publish_generation_event
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


def _queue_space_chat_index(session: Session, chat: Chat) -> None:
    """Refresh semantic index for a space chat after a successful turn."""
    if not chat.agent_id or chat.is_incognito or chat.is_deleted:
        return
    try:
        source = upsert_space_chat_source(session, chat)
        session.commit()
        if source is not None:
            enqueue_space_chat_index(chat.id)
    except Exception:
        logger.exception("Failed to queue space chat index for chat_id=%s", chat.id)
        try:
            session.rollback()
        except Exception:
            pass


def _persist_generation_event(
    session: Session,
    task_id: UUID,
    sequence_ref: list[int],
    event_type: str,
    payload: dict | None,
) -> None:
    sequence_ref[0] += 1
    sequence = sequence_ref[0]
    event = ChatGenerationEvent(
        task_id=task_id,
        event_type=event_type,
        payload_json=payload,
        sequence=sequence,
    )
    session.add(event)
    try:
        session.commit()
    except Exception:
        # If another DB operation poisoned the transaction, recover and
        # retry once so streaming events don't crash the whole generation.
        session.rollback()
        session.add(event)
        try:
            session.commit()
        except Exception:
            session.rollback()
            logger.warning(
                "Failed to persist %s event for task=%s",
                event_type,
                task_id,
                exc_info=True,
            )
            return
    publish_generation_event(
        task_id,
        sequence=sequence,
        event_type=event_type,
        payload=payload,
    )


class _DbEventSender:
    def __init__(self, session: Session, task_id: UUID, sequence_ref: list[int]) -> None:
        self._session = session
        self._task_id = task_id
        self._sequence_ref = sequence_ref

    async def send(self, payload: dict) -> None:
        _persist_generation_event(
            self._session, self._task_id, self._sequence_ref, "activity", payload
        )


class _DbToolEventSender:
    def __init__(self, session: Session, task_id: UUID, sequence_ref: list[int]) -> None:
        self._session = session
        self._task_id = task_id
        self._sequence_ref = sequence_ref

    async def send(self, payload: dict) -> None:
        _persist_generation_event(
            self._session, self._task_id, self._sequence_ref, "tool_event", payload
        )


class _DbDeltaSender:
    def __init__(
        self,
        session: Session,
        task_id: UUID,
        sequence_ref: list[int],
        *,
        assistant_message: ChatMessage | None = None,
        chat: Chat | None = None,
    ) -> None:
        self._session = session
        self._task_id = task_id
        self._sequence_ref = sequence_ref
        self._assistant_message = assistant_message
        self._chat = chat
        self._content = ""
        self.emitted = False

    async def send(self, payload: dict) -> None:
        delta = payload.get("delta") if isinstance(payload, dict) else None
        if not delta:
            return
        self.emitted = True
        self._content += str(delta)
        if self._assistant_message is not None:
            self._assistant_message.content = self._content
            self._session.add(self._assistant_message)
            if self._chat is not None:
                self._chat.last_activity_at = datetime.utcnow()
                self._session.add(self._chat)
        _persist_generation_event(
            self._session,
            self._task_id,
            self._sequence_ref,
            "delta",
            {"delta": delta},
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
        if not image_attachments or not supports_image_input(model):
            text = msg.content or ""
            if attachment_lines:
                text += (
                    "\n\nAttachments (available in /inputs for code execution):\n"
                    + "\n".join(attachment_lines)
                )
            items.append({"role": msg.role, "content": text})
            continue
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
    _persist_generation_event(session, task_id, sequence_ref, event_type, payload)


def _aggregated_message_usage_payload(
    session: Session, message_id: UUID
) -> dict[str, int | float | None]:
    usage = build_message_usage_map(session, [message_id]).get(message_id)
    if usage is None:
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "cached_tokens": 0,
            "thinking_tokens": 0,
            "total_tokens": 0,
            "cost_usd": None,
        }
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cached_tokens": usage.cached_tokens,
        "thinking_tokens": usage.thinking_tokens,
        "total_tokens": usage.total_tokens,
        "cost_usd": usage.cost_usd,
    }


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
        summary_response = await chat_with_langchain(provider, model.model_name, summary_request)
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
        model = ensure_model_capabilities(session, model)

        org = session.get(Org, chat.org_id)
        if not org:
            task.status = GenerationStatus.failed
            task.error = "Org not found"
            session.commit()
            return

        enabled = model.id in allowed_model_ids(session, chat.org_id, chat.user_id)
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

        prompt_cache_enabled = not chat.is_incognito
        requested_reasoning_effort = (
            (task.metadata_json or {}).get("reasoning_effort")
            if isinstance(task.metadata_json, dict)
            else None
        )
        provider = get_provider(
            model.provider,
            api_key=provider_config.api_key_override if provider_config else None,
            base_url=provider_config.base_url_override if provider_config else None,
            endpoint=provider_config.endpoint_override if provider_config else None,
            reasoning_effort=requested_reasoning_effort or model.reasoning_effort,
            prompt_cache_key=f"chat:{chat.id}" if prompt_cache_enabled else None,
            prompt_cache_retention=(
                settings.openai_prompt_cache_retention if prompt_cache_enabled else None
            ),
            prompt_cache_enabled=prompt_cache_enabled,
            prefer_responses_api=model.uses_responses_api is True,
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
        chat.last_activity_at = datetime.utcnow()
        session.add(chat)
        session.commit()
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
        requested_web_enabled = (
            task.metadata_json.get("web_search_enabled")
            if isinstance(task.metadata_json, dict)
            else None
        )
        effective_web_search_enabled = _effective_web_tool_enabled(
            org.web_search_enabled,
            requested_web_enabled,
        )
        effective_web_scrape_enabled = _effective_web_tool_enabled(
            org.web_scrape_enabled,
            requested_web_enabled,
        )
        pending_tool_attachments: list[dict[str, Any]] = []
        tool_registry = _build_tool_registry(
            session,
            chat.org_id,
            chat_id=chat.id,
            preferred_provider=model.provider,
            web_tools_enabled=not _grounding_enabled(org, model.provider),
            web_search_enabled=effective_web_search_enabled,
            web_scrape_enabled=effective_web_scrape_enabled,
            exec_policy=(
                _resolve_exec_policy(
                    org.exec_policy,
                    (task.metadata_json or {}).get("code_execution_enabled"),
                )
            ),
            locale=task.metadata_json.get("locale") if task.metadata_json else None,
            memory_enabled=chat_user.memory_enabled if chat_user else False,
            user_id=chat.user_id,
            agent_id=chat.agent_id,
            pending_attachments=pending_tool_attachments,
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
        try:
            messages = _build_provider_messages(
                history=history,
                attachments_by_message=attachments_by_message,
                model=model,
                locale=task.metadata_json.get("locale") if task.metadata_json else None,
                timezone=task.metadata_json.get("timezone") if task.metadata_json else None,
                enabled_tool_names=[spec.name for spec in tool_registry.list_specs()],
                memories=user_memories,
            )
            agent_sources: list[dict[str, Any]] = []
            if chat.agent_id:
                agent = session.get(Agent, chat.agent_id)
                if agent and agent.master_prompt and agent.master_prompt.strip():
                    messages.insert(
                        0,
                        {
                            "role": "system",
                            "content": (
                                "Project instructions (always follow these):\n"
                                f"{agent.master_prompt.strip()}"
                            ),
                        },
                    )
                latest_user_message = next(
                    (
                        item
                        for item in reversed(history)
                        if item.role == "user" and item.content and item.content.strip()
                    ),
                    None,
                )
                if latest_user_message:
                    retrieval_event_id = str(uuid4())
                    _append_event(
                        session,
                        task.id,
                        sequence_ref,
                        "tool_event",
                        {
                            "type": "tool_call",
                            "id": retrieval_event_id,
                            "tool_name": "agent_source_retrieval",
                            "state": "start",
                            "input_preview": (latest_user_message.content or "")[:160],
                        },
                    )
                    chunks = retrieve_agent_chunks(
                        session,
                        agent_id=chat.agent_id,
                        query=latest_user_message.content,
                        limit=6,
                    )
                    context_blocks: list[str] = []
                    seen_agent_source_ids: set[str] = set()
                    for idx, (chunk, source, _score) in enumerate(chunks, start=1):
                        source_key = str(source.id)
                        if source_key not in seen_agent_source_ids:
                            seen_agent_source_ids.add(source_key)
                            agent_sources.append(
                                {
                                    "source_id": source_key,
                                    "title": source.title,
                                    "url": source.url,
                                    "snippet": chunk.content[:300],
                                }
                            )
                        context_blocks.append(f"[Source {idx}] {source.title}\n{chunk.content}")
                    _append_event(
                        session,
                        task.id,
                        sequence_ref,
                        "tool_event",
                        {
                            "type": "tool_call",
                            "id": retrieval_event_id,
                            "tool_name": "agent_source_retrieval",
                            "state": "end",
                            "output": {
                                "status": "ok",
                                "result_preview": (
                                    f"Retrieved {len(chunks)} source chunks"
                                    if chunks
                                    else "No matching source chunks found"
                                ),
                            },
                        },
                    )
                    all_sources = session.scalars(
                        select(AgentSource)
                        .where(AgentSource.agent_id == chat.agent_id)
                        .order_by(AgentSource.created_at)
                    ).all()
                    catalog_lines = []
                    for idx, source in enumerate(all_sources, start=1):
                        if source.status == AgentSourceStatus.ready:
                            catalog_lines.append(f"- [{idx}] \"{source.title}\"")
                        else:
                            status = (
                                source.status.value
                                if hasattr(source.status, "value")
                                else str(source.status)
                            )
                            catalog_lines.append(
                                f"- [{idx}] \"{source.title}\" (not ready: {status})"
                            )
                    guidance = (
                        "You are answering inside a project that has attached documents "
                        "(\"sources\"). Work like a careful research assistant grounded in "
                        "these sources:\n"
                        "- Each source has a small numeric id (shown below). Refer to sources "
                        "by that number - never invent ids.\n"
                        "- Use `search_project_sources` to find relevant passages, and "
                        "`read_project_source` (passing the numeric id) to read the full "
                        "document when a passage matters. Prefer reading the actual document "
                        "over relying on a single snippet.\n"
                        "- For data analysis, CSV/XLSX processing, plotting, or other "
                        "file-based work on project documents, use `code_execution`. Project "
                        "source files are mounted read-only under `/inputs/project/` as "
                        "`<source_id>_<sanitized_name>` (original file bytes when available).\n"
                        "- Run multiple searches and read more than one source when the "
                        "question is broad or comparative. Do not stop after one search.\n"
                        "- Base your answer on the sources and cite them by title. If the "
                        "sources do not cover something, say so instead of guessing.\n"
                    )
                    if catalog_lines:
                        guidance += (
                            "\nAvailable sources in this project:\n"
                            + "\n".join(catalog_lines)
                        )
                        project_paths = []
                        for idx, source in enumerate(all_sources, start=1):
                            if source.status != AgentSourceStatus.ready:
                                continue
                            project_paths.append(
                                f"- [{idx}] \"{source.title}\" -> "
                                f"{project_source_exec_path(source)}"
                            )
                        if project_paths:
                            guidance += (
                                "\n\nProject source files for code_execution "
                                "(/inputs/project/):\n" + "\n".join(project_paths)
                            )
                    if context_blocks:
                        guidance += (
                            "\n\nRelevant passages for the latest question (starting point - "
                            "read the full sources if needed):\n"
                            + "\n\n".join(context_blocks)
                        )
                    messages.append({"role": "system", "content": guidance})
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
                            session=session, org_id=str(chat.org_id), chat_id=str(chat.id), user_id=str(chat.user_id)
                        ),
                        prompt=image_prompt,
                        image_id=str(latest_image_attachment.id),
                        model_override=model,
                    )
                else:
                    image_result = await generate_image(
                        ImageToolContext(
                            session=session, org_id=str(chat.org_id), chat_id=str(chat.id), user_id=str(chat.user_id)
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
                if image_result.attachments:
                    _append_event(
                        session,
                        task.id,
                        sequence_ref,
                        "tool_event",
                        {
                            "type": "tool_call",
                            "id": f"image:{assistant_message.id}",
                            "tool_name": "generate_image",
                            "state": "end",
                            "action_summary": "Generating image",
                            "output": {
                                "status": "ok",
                                "attachments": image_result.attachments,
                            },
                        },
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
                        "usage": _aggregated_message_usage_payload(
                            session, assistant_message.id
                        ),
                    },
                )
                assistant_message.status = "done"
                assistant_message.completed_at = datetime.utcnow()
                session.add(assistant_message)
                task.status = GenerationStatus.completed
                task.completed_at = datetime.utcnow()
                session.commit()
                _queue_space_chat_index(session, chat)
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
                delta_sender = _DbDeltaSender(
                    session,
                    task.id,
                    sequence_ref,
                    assistant_message=assistant_message,
                    chat=chat,
                )
                content, tool_attachments, tool_sources, image_usages, last_usage = (
                    await _run_agentic_loop(
                        provider=provider,
                        model=model,
                        messages=messages,
                        tool_registry=tool_registry,
                        pending_attachments=pending_tool_attachments,
                        activity_sender=activity_sender,
                        tool_event_sender=tool_event_sender,
                        delta_sender=delta_sender,
                    )
                )
                _ensure_task_not_cancelled(session, task.id)
                assistant_message.content = content
                session.add(assistant_message)
                session.commit()
                usage = last_usage or usage
                if tool_sources:
                    tool_sources = await _normalize_sources(tool_sources)
                merged_sources: list[dict[str, Any]] = []
                if tool_sources:
                    merged_sources.extend(tool_sources)
                if agent_sources:
                    merged_sources.extend(agent_sources)
                if merged_sources:
                    assistant_message.sources = _dedupe_sources(merged_sources)
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
                if not delta_sender.emitted:
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
                    async for chunk in chat_stream_with_langchain(provider, model.model_name, messages):
                        _ensure_task_not_cancelled(session, task.id)
                        if chunk.content:
                            assistant_content += chunk.content
                            assistant_message.content = assistant_content
                            session.add(assistant_message)
                            chat.last_activity_at = datetime.utcnow()
                            session.add(chat)
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
                    response = await chat_with_langchain(provider, model.model_name, messages)
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
                    "sources": assistant_message.sources or [],
                    "usage": _aggregated_message_usage_payload(
                        session, assistant_message.id
                    ),
                },
            )
            assistant_message.status = "done"
            assistant_message.completed_at = datetime.utcnow()
            session.add(assistant_message)
            task.status = GenerationStatus.completed
            task.completed_at = datetime.utcnow()
            session.commit()
            _queue_space_chat_index(session, chat)
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
            try:
                persist_responses_api_discovery(session, model, provider)
            except Exception:
                logger.debug(
                    "Failed to persist uses_responses_api for model=%s",
                    getattr(model, "id", None),
                    exc_info=True,
                )
            await _maybe_close_provider(provider)


# Reuse one event loop per Celery worker process. asyncio.run() creates and
# closes a loop per task; OpenAI's httpx wrapper then schedules aclose() on the
# *next* running loop during GC, which raises "Event loop is closed".
_worker_loop: asyncio.AbstractEventLoop | None = None


def _run_coro(coro: Any) -> Any:
    global _worker_loop
    if _worker_loop is None or _worker_loop.is_closed():
        _worker_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_worker_loop)
    return _worker_loop.run_until_complete(coro)


@celery_app.task(
    name="chatui.generate_chat_response",
    queue="generation",
    soft_time_limit=60 * 15,
    time_limit=60 * 20,
)
def generate_chat_response(task_id: str) -> None:
    _run_coro(_run_generation(UUID(task_id)))


@celery_app.task(
    name="chatui.reindex_agent_source",
    queue="embedding",
    soft_time_limit=60 * 60 * 12,
    time_limit=60 * 60 * 12 + 300,
)
def reindex_agent_source(source_id: str) -> None:
    with Session(engine) as session:
        source = session.get(AgentSource, UUID(source_id))
        if not source:
            logger.warning("Agent source not found for reindex: %s", source_id)
            return
        chunks_count, error = reindex_source(session, source)
        session.commit()
        if error:
            logger.error(
                "Agent source reindex failed source_id=%s error=%s",
                source_id,
                error,
            )
            raise RuntimeError(f"Agent source reindex failed: {error}")
        logger.info(
            "Agent source reindex complete source_id=%s chunks=%s",
            source_id,
            chunks_count,
        )


@celery_app.task(
    name="chatui.index_space_chat",
    queue="embedding",
    soft_time_limit=60 * 30,
    time_limit=60 * 35,
)
def index_space_chat(chat_id: str) -> None:
    with Session(engine) as session:
        chat = session.get(Chat, UUID(chat_id))
        if not chat or chat.is_deleted or chat.is_incognito or not chat.agent_id:
            return
        source = upsert_space_chat_source(session, chat)
        if source is None:
            session.commit()
            return
        chunks_count, error = reindex_source(session, source)
        session.commit()
        if error:
            logger.error(
                "Space chat index failed chat_id=%s source_id=%s error=%s",
                chat_id,
                source.id,
                error,
            )
            raise RuntimeError(f"Space chat index failed: {error}")
        logger.info(
            "Space chat index complete chat_id=%s source_id=%s chunks=%s",
            chat_id,
            source.id,
            chunks_count,
        )


def _delete_chat_files(session: Session, chat_ids: list[UUID]) -> None:
    if not chat_ids:
        return

    uploads = session.scalars(
        select(ChatUpload).where(ChatUpload.chat_id.in_(chat_ids))
    ).all()
    for upload in uploads:
        delete_file(upload.file_path)
    session.exec(delete(ChatUpload).where(ChatUpload.chat_id.in_(chat_ids)))

    message_ids = session.scalars(
        select(ChatMessage.id).where(ChatMessage.chat_id.in_(chat_ids))
    ).all()
    if not message_ids:
        return
    attachments = session.scalars(
        select(ChatMessageAttachment).where(
            ChatMessageAttachment.message_id.in_(message_ids)
        )
    ).all()
    for attachment in attachments:
        delete_file(attachment.file_path)
    session.exec(
        delete(ChatMessageAttachment).where(
            ChatMessageAttachment.message_id.in_(message_ids)
        )
    )


def _delete_expired_chat(session: Session, chat: Chat) -> None:
    _delete_chat_files(session, [chat.id])
    message_ids = session.scalars(
        select(ChatMessage.id).where(ChatMessage.chat_id == chat.id)
    ).all()
    session.exec(
        update(UsageEvent)
        .where(UsageEvent.chat_id == chat.id)
        .values(chat_id=None)
    )
    if message_ids:
        session.exec(
            update(UsageEvent)
            .where(UsageEvent.message_id.in_(message_ids))
            .values(message_id=None)
        )
    task_ids = session.scalars(
        select(ChatGenerationTask.id).where(ChatGenerationTask.chat_id == chat.id)
    ).all()
    if task_ids:
        session.exec(
            delete(ChatGenerationEvent).where(ChatGenerationEvent.task_id.in_(task_ids))
        )
    session.exec(delete(ChatGenerationTask).where(ChatGenerationTask.chat_id == chat.id))
    session.exec(delete(ChatViewEvent).where(ChatViewEvent.chat_id == chat.id))
    if message_ids:
        session.exec(
            update(ChatMessage)
            .where(ChatMessage.id.in_(message_ids))
            .values(parent_id=None)
        )
        session.exec(delete(ChatMessage).where(ChatMessage.id.in_(message_ids)))
    session.delete(chat)


@celery_app.task(name="chatui.cleanup_incognito_chats")
def cleanup_incognito_chats() -> None:
    """Permanently remove Incognito chats after 30 minutes of inactivity."""
    cutoff = datetime.utcnow() - timedelta(minutes=30)
    with Session(engine) as session:
        expired_chats = session.scalars(
            select(Chat).where(
                Chat.is_incognito.is_(True),
                Chat.last_activity_at < cutoff,
            )
        ).all()
        for chat in expired_chats:
            _delete_expired_chat(session, chat)
        session.commit()


# Queued rows can outlive their Celery broker message (Redis flush, worker
# downtime, send_task failure after DB commit). Active rows can stay forever if
# a worker is killed before it writes a terminal status.
STALE_QUEUED_AFTER = timedelta(days=1)
STALE_ACTIVE_AFTER = timedelta(days=1)


def _fail_stale_generation_task(
    session: Session, task: ChatGenerationTask, error: str
) -> None:
    now = datetime.utcnow()
    task.status = GenerationStatus.failed
    task.error = error
    task.completed_at = now
    session.add(task)
    assistant = session.get(ChatMessage, task.assistant_message_id)
    if not assistant:
        return
    if assistant.status in {"completed", "failed", "cancelled"}:
        return
    assistant.status = "failed"
    assistant.completed_at = now
    assistant.error_message = error
    if not (assistant.content or "").strip():
        assistant.content = error
    session.add(assistant)


@celery_app.task(name="chatui.cleanup_stale_generation_tasks")
def cleanup_stale_generation_tasks() -> dict[str, int]:
    """Mark orphaned generation tasks as failed so they stop blocking diagnostics/UI."""
    now = datetime.utcnow()
    queued_cutoff = now - STALE_QUEUED_AFTER
    active_cutoff = now - STALE_ACTIVE_AFTER
    failed_queued = 0
    failed_active = 0
    with Session(engine) as session:
        stale_queued = session.scalars(
            select(ChatGenerationTask).where(
                ChatGenerationTask.status == GenerationStatus.queued,
                ChatGenerationTask.created_at < queued_cutoff,
            )
        ).all()
        for task in stale_queued:
            _fail_stale_generation_task(
                session,
                task,
                "Timed out waiting for a worker (task never started)",
            )
            failed_queued += 1

        stale_active = session.scalars(
            select(ChatGenerationTask).where(
                ChatGenerationTask.status.in_(
                    [GenerationStatus.running, GenerationStatus.streaming]
                ),
                ChatGenerationTask.started_at.is_not(None),
                ChatGenerationTask.started_at < active_cutoff,
            )
        ).all()
        stale_active_no_start = session.scalars(
            select(ChatGenerationTask).where(
                ChatGenerationTask.status.in_(
                    [GenerationStatus.running, GenerationStatus.streaming]
                ),
                ChatGenerationTask.started_at.is_(None),
                ChatGenerationTask.created_at < active_cutoff,
            )
        ).all()
        for task in [*stale_active, *stale_active_no_start]:
            _fail_stale_generation_task(
                session,
                task,
                "Generation timed out (worker stopped without finishing)",
            )
            failed_active += 1

        if failed_queued or failed_active:
            session.commit()
            logger.info(
                "Cleaned stale generation tasks queued=%s active=%s",
                failed_queued,
                failed_active,
            )
    return {"queued": failed_queued, "active": failed_active}


@celery_app.task(name="chatui.cleanup_retained_data")
def cleanup_retained_data() -> None:
    """Permanently remove expired chat files and chat history for each organization."""
    now = datetime.utcnow()
    with Session(engine) as session:
        orgs = session.scalars(select(Org)).all()
        for org in orgs:
            if org.file_retention_days is not None:
                file_cutoff = now - timedelta(days=org.file_retention_days)
                expired_file_chats = session.scalars(
                    select(Chat.id).where(
                        Chat.org_id == org.id,
                        Chat.last_activity_at < file_cutoff,
                    )
                ).all()
                _delete_chat_files(session, expired_file_chats)

            if org.chat_retention_days is not None:
                chat_cutoff = now - timedelta(days=org.chat_retention_days)
                expired_chats = session.scalars(
                    select(Chat).where(
                        Chat.org_id == org.id,
                        Chat.last_activity_at < chat_cutoff,
                    )
                ).all()
                for chat in expired_chats:
                    _delete_expired_chat(session, chat)
            session.commit()
