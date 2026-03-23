from __future__ import annotations

import base64
from dataclasses import dataclass
from io import BytesIO
from typing import Any
from uuid import UUID

from pypdf import PdfReader
from sqlmodel import Session, select

from app.models import ChatMessage, ChatMessageAttachment
from app.services.tools.registry import ToolResult

MAX_PAGE_RANGE = 20
DEFAULT_PREVIEW_PAGES = 5


@dataclass
class PdfToolContext:
    session: Session
    chat_id: str


def _chat_attachments(session: Session, chat_id: str) -> list[ChatMessageAttachment]:
    chat_uuid = UUID(chat_id)
    message_ids = session.exec(
        select(ChatMessage.id).where(ChatMessage.chat_id == chat_uuid)
    ).all()
    if not message_ids:
        return []
    return session.exec(
        select(ChatMessageAttachment).where(
            ChatMessageAttachment.message_id.in_(message_ids)
        )
    ).all()


def _is_pdf_attachment(attachment: ChatMessageAttachment) -> bool:
    content_type = (attachment.content_type or "").lower()
    file_name = (attachment.file_name or "").lower()
    return "pdf" in content_type or file_name.endswith(".pdf")


def _pick_pdf_attachment(
    attachments: list[ChatMessageAttachment],
    *,
    attachment_id: str | None,
    file_name: str | None,
) -> ChatMessageAttachment | None:
    pdf_attachments = [item for item in attachments if _is_pdf_attachment(item)]
    if not pdf_attachments:
        return None

    if attachment_id:
        try:
            wanted = UUID(attachment_id)
        except Exception:
            return None
        for item in pdf_attachments:
            if item.id == wanted:
                return item
        return None

    if file_name:
        wanted_name = file_name.strip().lower()
        for item in reversed(pdf_attachments):
            current_name = (item.file_name or "").strip().lower()
            if current_name == wanted_name:
                return item
        for item in reversed(pdf_attachments):
            current_name = (item.file_name or "").strip().lower()
            if wanted_name in current_name:
                return item
        return None

    # Default: latest PDF attachment in this chat.
    return pdf_attachments[-1]


def _parse_page_selection(
    *,
    page_count: int,
    page: int | None,
    page_from: int | None,
    page_to: int | None,
) -> tuple[list[int], str | None]:
    if page is None and page_from is None and page_to is None:
        return [], "No page selected. Provide `page` or `page_from`/`page_to`."

    if page is not None:
        if page < 1 or page > page_count:
            return [], f"`page` out of range. Valid range: 1..{page_count}."
        return [page], None

    start = page_from if page_from is not None else 1
    end = page_to if page_to is not None else start
    if start < 1 or end < 1 or start > page_count or end > page_count:
        return [], f"`page_from`/`page_to` out of range. Valid range: 1..{page_count}."
    if end < start:
        return [], "`page_to` must be greater than or equal to `page_from`."
    if (end - start + 1) > MAX_PAGE_RANGE:
        return [], (
            f"Requested range too large ({end - start + 1} pages). "
            f"Maximum per call: {MAX_PAGE_RANGE} pages."
        )
    return list(range(start, end + 1)), None


async def extract_pdf(
    context: PdfToolContext,
    *,
    attachment_id: str | None = None,
    file_name: str | None = None,
    page: int | None = None,
    page_from: int | None = None,
    page_to: int | None = None,
) -> ToolResult:
    attachments = _chat_attachments(context.session, context.chat_id)
    attachment = _pick_pdf_attachment(
        attachments,
        attachment_id=attachment_id,
        file_name=file_name,
    )
    if not attachment:
        return ToolResult(
            name="extract_pdf",
            output={"error": "PDF attachment not found for this chat."},
        )

    try:
        data = base64.b64decode(attachment.data_base64)
    except Exception as exc:
        return ToolResult(
            name="extract_pdf",
            output={"error": f"Failed to decode PDF attachment: {exc}"},
        )

    try:
        reader = PdfReader(BytesIO(data))
    except Exception as exc:
        return ToolResult(
            name="extract_pdf",
            output={"error": f"Failed to open PDF: {exc}"},
        )

    page_count = len(reader.pages)
    notice: str | None = None
    if page is None and page_from is None and page_to is None:
        selected_pages = list(range(1, min(page_count, DEFAULT_PREVIEW_PAGES) + 1))
        if page_count > DEFAULT_PREVIEW_PAGES:
            notice = (
                f"Document truncated to first {DEFAULT_PREVIEW_PAGES} pages. "
                "Provide `page` or `page_from`/`page_to` for a specific range."
            )
        selection_error = None
    else:
        selected_pages, selection_error = _parse_page_selection(
            page_count=page_count,
            page=page,
            page_from=page_from,
            page_to=page_to,
        )
    base_output: dict[str, Any] = {
        "attachment_id": str(attachment.id),
        "file_name": attachment.file_name,
        "page_count": page_count,
        "selected_pages": selected_pages,
    }
    if selection_error:
        return ToolResult(
            name="extract_pdf",
            output={
                **base_output,
                "error": selection_error,
            },
        )

    pages: list[dict[str, Any]] = []
    for page_number in selected_pages:
        try:
            text = reader.pages[page_number - 1].extract_text() or ""
        except Exception as exc:
            text = ""
            pages.append(
                {
                    "page": page_number,
                    "text": text,
                    "error": f"Extraction failed: {exc}",
                }
            )
            continue
        pages.append(
            {
                "page": page_number,
                "text": text,
                "char_count": len(text),
            }
        )

    return ToolResult(
        name="extract_pdf",
        output={
            **base_output,
            **({"notice": notice} if notice else {}),
            "pages": pages,
        },
    )
