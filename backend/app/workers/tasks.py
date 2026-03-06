from __future__ import annotations

import asyncio
from datetime import datetime
import logging
from typing import Any
from uuid import UUID
import json

from sqlalchemy import func, select
from sqlmodel import Session

from app.api.chats import (
    _attachment_lines,
    _build_tool_registry,
    _grounding_enabled,
    _is_image_output_model,
    _maybe_update_chat_title,
    _normalize_sources,
    _prepend_tool_guidance,
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
)
from app.services.org_service import require_provider_enabled
from app.services.providers.base import ChatUsage
from app.services.providers.registry import get_provider
from app.services.tools.image_tool import ImageToolContext, generate_image
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


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
        self._session.commit()


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
        self._session.commit()


def _build_provider_messages(
    *,
    history: list[ChatMessage],
    attachments_by_message: dict[UUID, list[ChatMessageAttachment]],
    model: ChatModel,
    locale: str | None,
) -> list[dict]:
    items: list[dict[str, Any]] = []
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
                        "url": f"data:{attachment.content_type};base64,{attachment.data_base64}"
                    },
                }
            )
        items.append({"role": msg.role, "content": content_parts})
    return _truncate_messages(_prepend_tool_guidance(items, locale=locale), token_limit=model.context_length)


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


async def _run_generation(task_id: UUID) -> None:
    with Session(engine) as session:
        task = session.get(ChatGenerationTask, task_id)
        if not task:
            logger.warning("Generation task not found: %s", task_id)
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

        task.status = GenerationStatus.running
        task.started_at = datetime.utcnow()
        task.metadata_json = {
            "model_id": str(model.id),
            "model_name": model.display_name,
        }
        session.commit()

        assistant_message = session.get(ChatMessage, task.assistant_message_id)
        if not assistant_message:
            task.status = GenerationStatus.failed
            task.error = "Assistant message not found"
            session.commit()
            return

        task.status = GenerationStatus.streaming
        assistant_message.status = "generating"
        assistant_message.started_at = datetime.utcnow()
        session.add(assistant_message)
        session.commit()

        tool_registry = _build_tool_registry(
            session,
            chat.org_id,
            chat_id=chat.id,
            preferred_provider=model.provider,
            web_tools_enabled=not _grounding_enabled(org, model.provider),
            web_search_enabled=org.web_search_enabled,
            web_scrape_enabled=org.web_scrape_enabled,
            exec_policy=org.exec_policy,
            exec_network_enabled=org.exec_network_enabled,
            locale=task.metadata_json.get("locale") if task.metadata_json else None,
        )
        messages = _build_provider_messages(
            history=history,
            attachments_by_message=attachments_by_message,
            model=model,
            locale=task.metadata_json.get("locale") if task.metadata_json else None,
        )

        usage = ChatUsage(0, 0, 0, 0, 0, 0, 0)
        tool_attachments: list[dict] | None = None
        tool_sources: list[dict] | None = None
        image_usages: list[dict] = []

        try:
            if _is_image_output_model(model):
                _append_event(
                    session,
                    task.id,
                    sequence_ref,
                    "activity",
                    {"label": "Generating image", "state": "start"},
                )
                image_result = await generate_image(
                    ImageToolContext(session=session, org_id=str(chat.org_id)),
                    prompt=history[-1].content if history else "",
                    model_override=model,
                )
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
        except Exception as exc:  # noqa: BLE001
            logger.exception("Generation failed for task=%s", task.id)
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


@celery_app.task(name="chatui.generate_chat_response")
def generate_chat_response(task_id: str) -> None:
    asyncio.run(_run_generation(UUID(task_id)))
