from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, or_
from sqlmodel import Session, select

from app.models.entities import (
    AgentChunk,
    AgentEmbedding,
    AgentSource,
    AgentSourceKind,
    AgentSourceStatus,
    Chat,
    ChatMessage,
)
from app.services.file_storage import delete_file

logger = logging.getLogger(__name__)

CHAT_SOURCE_URL_PREFIX = "chat://"
_MAX_TRANSCRIPT_CHARS = 120_000
_MAX_MESSAGE_CHARS = 4_000


def chat_source_url(chat_id: UUID) -> str:
    return f"{CHAT_SOURCE_URL_PREFIX}{chat_id}"


def parse_chat_source_url(url: str | None) -> UUID | None:
    if not url or not url.startswith(CHAT_SOURCE_URL_PREFIX):
        return None
    raw = url[len(CHAT_SOURCE_URL_PREFIX) :].strip()
    try:
        return UUID(raw)
    except ValueError:
        return None


def build_chat_transcript(session: Session, chat_id: UUID) -> str:
    rows = session.exec(
        select(ChatMessage.role, ChatMessage.content)
        .where(
            ChatMessage.chat_id == chat_id,
            ChatMessage.is_current.is_(True),
        )
        .order_by(ChatMessage.created_at)
    ).all()
    lines: list[str] = []
    total = 0
    for role, content in rows:
        if role not in ("user", "assistant", "system"):
            continue
        text = (content or "").strip()
        if not text:
            continue
        if len(text) > _MAX_MESSAGE_CHARS:
            text = text[: _MAX_MESSAGE_CHARS - 1].rstrip() + "…"
        line = f"{role}: {text}"
        if total + len(line) + 2 > _MAX_TRANSCRIPT_CHARS:
            lines.append("…[transcript truncated]")
            break
        lines.append(line)
        total += len(line) + 2
    return "\n\n".join(lines)


def find_chat_source(session: Session, *, agent_id: UUID, chat_id: UUID) -> AgentSource | None:
    return session.exec(
        select(AgentSource).where(
            AgentSource.agent_id == agent_id,
            AgentSource.kind == AgentSourceKind.chat,
            AgentSource.url == chat_source_url(chat_id),
        )
    ).first()


def _delete_source_chunks(session: Session, source: AgentSource) -> None:
    chunk_ids = session.exec(
        select(AgentChunk.id).where(AgentChunk.source_id == source.id)
    ).all()
    chunk_ids = [item if isinstance(item, UUID) else item[0] for item in chunk_ids]
    if chunk_ids:
        session.exec(delete(AgentEmbedding).where(AgentEmbedding.chunk_id.in_(chunk_ids)))
        session.exec(delete(AgentChunk).where(AgentChunk.id.in_(chunk_ids)))
    delete_file(source.file_path)
    session.delete(source)


def delete_project_chat_source(session: Session, chat_id: UUID) -> None:
    url = chat_source_url(chat_id)
    sources = session.exec(
        select(AgentSource).where(
            AgentSource.kind == AgentSourceKind.chat,
            AgentSource.url == url,
        )
    ).all()
    for source in sources:
        _delete_source_chunks(session, source)


def upsert_project_chat_source(session: Session, chat: Chat) -> AgentSource | None:
    """Create/update a queued chat source for semantic indexing. Caller commits + enqueues."""
    if not chat.agent_id or chat.is_deleted or chat.is_incognito:
        return None

    transcript = build_chat_transcript(session, chat.id)
    if not transcript.strip():
        existing = find_chat_source(session, agent_id=chat.agent_id, chat_id=chat.id)
        if existing:
            _delete_source_chunks(session, existing)
        return None

    now = datetime.utcnow()
    title = (chat.title or "").strip() or "Untitled chat"
    metadata: dict[str, Any] = {
        "chat_id": str(chat.id),
        "user_id": str(chat.user_id),
        "origin": "chat",
    }
    source = find_chat_source(session, agent_id=chat.agent_id, chat_id=chat.id)
    if source is None:
        source = AgentSource(
            agent_id=chat.agent_id,
            kind=AgentSourceKind.chat,
            title=title,
            url=chat_source_url(chat.id),
            file_name=None,
            content_type="text/plain",
            content_text=transcript,
            status=AgentSourceStatus.queued,
            metadata_json=metadata,
            created_at=now,
            updated_at=now,
        )
        session.add(source)
    else:
        source.title = title
        source.content_text = transcript
        source.metadata_json = metadata
        source.status = AgentSourceStatus.queued
        source.error_message = None
        source.updated_at = now
        session.add(source)
    session.flush()
    return source


def enqueue_project_chat_index(chat_id: UUID) -> None:
    from app.workers.celery_app import celery_app

    celery_app.send_task(
        "chatui.index_project_chat",
        args=[str(chat_id)],
        queue="embedding",
    )


def enqueue_missing_project_chat_indexes(
    session: Session,
    *,
    agent_id: UUID,
    user_id: UUID,
    limit: int = 25,
) -> int:
    """Queue indexing for this user's project chats that have no active source yet.

    Creates/updates queued sources on the caller's session (no commit) so repeat
    requests in the same window do not spam duplicate Celery jobs.
    """
    chats = session.exec(
        select(Chat)
        .where(
            Chat.agent_id == agent_id,
            Chat.user_id == user_id,
            Chat.is_deleted.is_(False),
            Chat.is_incognito.is_(False),
        )
        .order_by(Chat.last_activity_at.desc())
        .limit(limit * 3)
    ).all()
    if not chats:
        return 0

    existing_urls = {
        source.url
        for source in session.exec(
            select(AgentSource).where(
                AgentSource.agent_id == agent_id,
                AgentSource.kind == AgentSourceKind.chat,
                AgentSource.url.in_([chat_source_url(chat.id) for chat in chats]),
                or_(
                    AgentSource.status == AgentSourceStatus.ready,
                    AgentSource.status == AgentSourceStatus.queued,
                    AgentSource.status == AgentSourceStatus.indexing,
                ),
            )
        ).all()
        if source.url
    }

    queued = 0
    for chat in chats:
        if chat_source_url(chat.id) in existing_urls:
            continue
        source = upsert_project_chat_source(session, chat)
        if source is None:
            continue
        enqueue_project_chat_index(chat.id)
        queued += 1
        if queued >= limit:
            break
    return queued


def chat_source_visible_to_user(source: AgentSource, viewer_user_id: UUID | None) -> bool:
    if source.kind != AgentSourceKind.chat:
        return True
    if viewer_user_id is None:
        return False
    meta = source.metadata_json if isinstance(source.metadata_json, dict) else {}
    return str(meta.get("user_id") or "") == str(viewer_user_id)
