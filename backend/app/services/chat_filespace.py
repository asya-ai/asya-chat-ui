"""Shared disk filespace for a parent chat and its subagents."""

from __future__ import annotations

from uuid import UUID

from sqlmodel import Session, select

from app.models.entities import Chat


def filespace_chat_id(chat: Chat | None, *, chat_id: UUID | str | None = None) -> UUID:
    """Root chat id used for uploads, exec runs, and MCP spill paths."""
    if chat is not None:
        if chat.parent_chat_id is not None:
            return chat.parent_chat_id
        return chat.id
    if chat_id is None:
        raise ValueError("chat or chat_id is required")
    return UUID(str(chat_id))


def resolve_filespace_chat_id(session: Session, chat_id: UUID | str) -> UUID:
    chat_uuid = UUID(str(chat_id))
    chat = session.get(Chat, chat_uuid)
    if chat is None:
        return chat_uuid
    return filespace_chat_id(chat)


def family_chat_ids(session: Session, root_chat_id: UUID | str) -> list[UUID]:
    """Root plus all non-deleted subagent children."""
    root_uuid = UUID(str(root_chat_id))
    children = session.exec(
        select(Chat.id).where(
            Chat.parent_chat_id == root_uuid,
            Chat.is_deleted.is_(False),
        )
    ).all()
    ids = [root_uuid]
    for child_id in children:
        if isinstance(child_id, UUID):
            ids.append(child_id)
        else:
            ids.append(UUID(str(child_id)))
    return ids
