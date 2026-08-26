from __future__ import annotations

import base64
from dataclasses import dataclass
from uuid import UUID

from sqlmodel import Session

from app.core.config import settings
from app.models.entities import ChatMessageAttachment
from app.services.file_storage import (
    attachment_bytes,
    attachment_size_bytes,
    store_chat_attachment_bytes,
)


@dataclass(frozen=True)
class ResolvedAttachmentPayload:
    file_name: str
    content_type: str
    data: bytes


def persist_attachment_bytes(
    session: Session,
    *,
    chat_id: UUID,
    message_id: UUID,
    file_name: str,
    content_type: str,
    data: bytes,
) -> ChatMessageAttachment:
    attachment = ChatMessageAttachment(
        message_id=message_id,
        file_name=file_name,
        content_type=content_type,
        data_base64=None,
    )
    session.add(attachment)
    session.flush()
    attachment.file_path = store_chat_attachment_bytes(
        chat_id=chat_id,
        message_id=message_id,
        attachment_id=attachment.id,
        file_name=file_name,
        data=data,
    )
    return attachment


def persist_resolved_attachments(
    session: Session,
    *,
    chat_id: UUID,
    message_id: UUID,
    items: list[ResolvedAttachmentPayload],
) -> list[ChatMessageAttachment]:
    if not items:
        return []
    attachments = [
        persist_attachment_bytes(
            session,
            chat_id=chat_id,
            message_id=message_id,
            file_name=item.file_name,
            content_type=item.content_type,
            data=item.data,
        )
        for item in items
    ]
    session.commit()
    return attachments


def persist_tool_attachment_dicts(
    session: Session,
    *,
    chat_id: UUID,
    message_id: UUID,
    items: list[dict],
) -> list[ChatMessageAttachment]:
    if not items:
        return []
    attachments: list[ChatMessageAttachment] = []
    for item in items:
        raw = item.get("data_base64")
        if not isinstance(raw, str) or not raw:
            continue
        try:
            data = base64.b64decode(raw)
        except Exception:
            continue
        if len(data) > settings.attachments_max_file_bytes:
            continue
        attachments.append(
            persist_attachment_bytes(
                session,
                chat_id=chat_id,
                message_id=message_id,
                file_name=str(item.get("file_name") or "attachment"),
                content_type=str(item.get("content_type") or "application/octet-stream"),
                data=data,
            )
        )
    if attachments:
        session.commit()
    return attachments


def copy_message_attachments(
    session: Session,
    *,
    chat_id: UUID,
    source_attachments: list[ChatMessageAttachment],
    new_message_id: UUID,
) -> list[ChatMessageAttachment]:
    if not source_attachments:
        return []
    copied: list[ChatMessageAttachment] = []
    for source in source_attachments:
        data = attachment_bytes(
            file_path=source.file_path,
            data_base64=source.data_base64,
        )
        if data is None:
            continue
        copied.append(
            persist_attachment_bytes(
                session,
                chat_id=chat_id,
                message_id=new_message_id,
                file_name=source.file_name,
                content_type=source.content_type,
                data=data,
            )
        )
    if copied:
        session.commit()
    return copied


def load_attachment_payload(
    *,
    file_name: str,
    content_type: str,
    file_path: str | None = None,
    data_base64: str | None = None,
) -> ResolvedAttachmentPayload | None:
    data = attachment_bytes(file_path=file_path, data_base64=data_base64)
    if data is None:
        return None
    return ResolvedAttachmentPayload(
        file_name=file_name,
        content_type=content_type,
        data=data,
    )


def payload_size_bytes(item: ResolvedAttachmentPayload) -> int:
    return len(item.data)


def legacy_base64_size(file_path: str | None, data_base64: str | None) -> int:
    return attachment_size_bytes(file_path=file_path, data_base64=data_base64)
