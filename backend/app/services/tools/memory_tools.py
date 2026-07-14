from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlmodel import Session

from app.models.entities import Chat, ChatMessage, UserMemory
from app.services.tools.registry import ToolResult

logger = logging.getLogger(__name__)


@dataclass
class MemoryToolContext:
    session: Session
    user_id: UUID
    current_chat_id: UUID | None = None


async def store_memory(context: MemoryToolContext, *, content: str) -> ToolResult:
    content = (content or "").strip()
    if not content:
        return ToolResult(name="store_memory", output={"error": "Memory content is required."})

    memory = UserMemory(user_id=context.user_id, content=content)
    context.session.add(memory)
    context.session.commit()
    context.session.refresh(memory)
    return ToolResult(
        name="store_memory",
        output={"status": "stored", "memory_id": str(memory.id), "content": content},
    )


async def remove_memory(context: MemoryToolContext, *, memory_id: str) -> ToolResult:
    try:
        mid = UUID(memory_id)
    except (ValueError, TypeError):
        return ToolResult(name="remove_memory", output={"error": "Invalid memory_id."})

    memory = context.session.get(UserMemory, mid)
    if not memory or memory.user_id != context.user_id:
        return ToolResult(name="remove_memory", output={"error": "Memory not found."})

    context.session.delete(memory)
    context.session.commit()
    return ToolResult(name="remove_memory", output={"status": "removed", "memory_id": memory_id})


async def search_past_chats(
    context: MemoryToolContext, *, query: str, limit: int = 10
) -> ToolResult:
    query = (query or "").strip()
    if not query:
        return ToolResult(name="search_past_chats", output={"error": "Query is required."})

    capped_limit = max(1, min(limit, 20))
    session = context.session

    base_chat_filters = [
        Chat.user_id == context.user_id,
        Chat.is_deleted.is_(False),
    ]
    if context.current_chat_id:
        base_chat_filters.append(Chat.id != context.current_chat_id)
    eligible_chats_subq = select(Chat.id).where(*base_chat_filters).subquery()
    search_query = func.plainto_tsquery("simple", query)
    title_vector = func.to_tsvector("simple", func.coalesce(Chat.title, ""))
    title_rank = func.ts_rank_cd(title_vector, search_query)
    title_match = title_vector.op("@@")(search_query)

    message_rank_subq = (
        select(
            ChatMessage.chat_id.label("chat_id"),
            func.max(
                func.ts_rank_cd(
                    func.to_tsvector("simple", func.coalesce(ChatMessage.content, "")),
                    search_query,
                )
            ).label("message_rank"),
        )
        .where(
            ChatMessage.is_current.is_(True),
            ChatMessage.chat_id.in_(select(eligible_chats_subq.c.id)),
            func.to_tsvector("simple", func.coalesce(ChatMessage.content, "")).op("@@")(
                search_query
            ),
        )
        .group_by(ChatMessage.chat_id)
        .subquery()
    )

    chats = session.exec(
        select(
            Chat.id,
            Chat.title,
            Chat.created_at,
            (title_rank * 2.0 + func.coalesce(message_rank_subq.c.message_rank, 0.0)).label(
                "rank"
            ),
        )
        .outerjoin(message_rank_subq, message_rank_subq.c.chat_id == Chat.id)
        .where(*base_chat_filters)
        .where(title_match | (message_rank_subq.c.message_rank.is_not(None)))
        .order_by(
            (title_rank * 2.0 + func.coalesce(message_rank_subq.c.message_rank, 0.0)).desc()
        )
        .limit(capped_limit)
    ).all()

    results = []
    for chat_id, title, created_at, _rank in chats:
        messages = session.exec(
            select(ChatMessage.role, ChatMessage.content)
            .where(ChatMessage.chat_id == chat_id, ChatMessage.is_current.is_(True))
            .order_by(ChatMessage.created_at)
            .limit(6)
        ).all()
        snippet_lines = []
        for role, content in messages:
            text = (content or "").strip()
            if len(text) > 200:
                text = text[:200] + "…"
            snippet_lines.append(f"{role}: {text}")

        results.append({
            "chat_id": str(chat_id),
            "chat_title": title or "(untitled)",
            "created_at": created_at.isoformat() if created_at else None,
            "messages_preview": "\n".join(snippet_lines),
        })

    if not results:
        return ToolResult(
            name="search_past_chats",
            output={"results": [], "message": "No matching chats found."},
        )

    return ToolResult(name="search_past_chats", output={"results": results})
