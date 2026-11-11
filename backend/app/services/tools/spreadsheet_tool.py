from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from io import BytesIO
from typing import Any
from uuid import UUID, uuid4

from sqlmodel import Session, select

from app.models import ChatMessage, ChatMessageAttachment
from app.services.file_storage import attachment_bytes
from app.services.tools.pdf_tool import _pending_chat_attachments
from app.services.tools.registry import ToolResult

try:
    from openpyxl import load_workbook
    from openpyxl.workbook.workbook import Workbook
except ImportError:  # pragma: no cover
    load_workbook = None  # type: ignore[misc, assignment]
    Workbook = None  # type: ignore[misc, assignment]

MAX_READ_ROWS = 80
MAX_SEARCH_HITS = 40
_XLSX_CONTENT = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@dataclass
class SpreadsheetToolContext:
    session: Session
    chat_id: str
    pending_attachments: list[dict[str, Any]] | None = None


def _is_spreadsheet_attachment(attachment: ChatMessageAttachment) -> bool:
    content_type = (attachment.content_type or "").lower()
    file_name = (attachment.file_name or "").lower()
    if file_name.endswith(".xlsx"):
        return True
    return "spreadsheetml" in content_type or file_name.endswith(".xlsm")


def _chat_attachments(session: Session, chat_id: str) -> list[ChatMessageAttachment]:
    chat_uuid = UUID(chat_id)
    message_ids = session.exec(
        select(ChatMessage.id).where(
            ChatMessage.chat_id == chat_uuid,
            ChatMessage.is_current.is_(True),
        )
    ).all()
    if not message_ids:
        return []
    return list(
        session.exec(
            select(ChatMessageAttachment).where(
                ChatMessageAttachment.message_id.in_(message_ids)
            )
        ).all()
    )


def chat_has_spreadsheet_attachments(
    session: Session,
    chat_id: str,
    *,
    pending_attachments: list[dict[str, Any]] | None = None,
) -> bool:
    attachments = _chat_attachments(session, chat_id)
    attachments.extend(_pending_chat_attachments(pending_attachments))
    return any(_is_spreadsheet_attachment(item) for item in attachments)


def _pick_spreadsheet_attachment(
    attachments: list[ChatMessageAttachment],
    *,
    attachment_id: str | None,
    file_name: str | None,
) -> ChatMessageAttachment | None:
    spreadsheets = [item for item in attachments if _is_spreadsheet_attachment(item)]
    if not spreadsheets:
        return None

    if attachment_id:
        try:
            wanted = UUID(attachment_id.strip())
        except ValueError:
            wanted = None
        if wanted is not None:
            for item in spreadsheets:
                if item.id == wanted:
                    return item
        for item in spreadsheets:
            if str(item.id) == attachment_id.strip():
                return item

    if file_name:
        wanted_name = file_name.strip().lower()
        for item in reversed(spreadsheets):
            if (item.file_name or "").strip().lower() == wanted_name:
                return item
        for item in reversed(spreadsheets):
            if wanted_name in (item.file_name or "").strip().lower():
                return item
        return None

    if len(spreadsheets) == 1:
        return spreadsheets[0]
    return None


def _load_attachment_workbook(
    attachment: ChatMessageAttachment,
) -> tuple[Any | None, str | None]:
    if load_workbook is None:
        return None, "openpyxl is not installed on the server"
    data = attachment_bytes(
        file_path=attachment.file_path,
        data_base64=attachment.data_base64,
    )
    if not data:
        return None, "Failed to read spreadsheet bytes"
    try:
        return load_workbook(BytesIO(data), data_only=False), None
    except Exception as exc:
        return None, f"Failed to open workbook: {exc}"


def _sheet_names(workbook: Any) -> list[str]:
    return [str(name) for name in workbook.sheetnames]


def _resolve_sheet(workbook: Any, sheet_name: str | None) -> Any:
    names = _sheet_names(workbook)
    if not names:
        raise ValueError("Workbook has no sheets")
    if sheet_name:
        key = sheet_name.strip()
        for name in names:
            if name == key:
                return workbook[name]
        lowered = key.lower()
        for name in names:
            if name.lower() == lowered:
                return workbook[name]
        raise ValueError(f"Sheet not found: {sheet_name}")
    return workbook[names[0]]


def _cell_value_repr(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


async def read_spreadsheet(
    context: SpreadsheetToolContext,
    *,
    attachment_id: str | None = None,
    file_name: str | None = None,
    sheet_name: str | None = None,
    start_row: int | None = None,
    max_rows: int | None = None,
) -> ToolResult:
    attachments = _chat_attachments(context.session, context.chat_id)
    attachments.extend(_pending_chat_attachments(context.pending_attachments))
    attachment = _pick_spreadsheet_attachment(
        attachments, attachment_id=attachment_id, file_name=file_name
    )
    if not attachment:
        return ToolResult(
            name="read_spreadsheet",
            output={
                "error": "Spreadsheet attachment not found. Pass attachment_id or file_name.",
            },
        )
    workbook, err = _load_attachment_workbook(attachment)
    if err or workbook is None:
        return ToolResult(name="read_spreadsheet", output={"error": err})
    all_sheets = _sheet_names(workbook)
    try:
        sheet = _resolve_sheet(workbook, sheet_name)
    except ValueError as exc:
        workbook.close()
        return ToolResult(
            name="read_spreadsheet",
            output={
                "error": str(exc),
                "sheets": all_sheets,
                "attachment_id": str(attachment.id),
                "file_name": attachment.file_name,
            },
        )

    row_limit = max(1, min(int(max_rows or MAX_READ_ROWS), MAX_READ_ROWS))
    start = max(1, int(start_row or 1))
    rows: list[dict[str, Any]] = []
    for offset in range(row_limit):
        row_idx = start + offset
        row_cells = list(
            sheet.iter_rows(min_row=row_idx, max_row=row_idx, values_only=False)
        )
        if not row_cells:
            break
        cells = row_cells[0]
        if not any(cell.value is not None and str(cell.value).strip() for cell in cells):
            if offset > 0:
                break
        values = [_cell_value_repr(cell.value) for cell in cells]
        if any(values):
            rows.append(
                {
                    "row": row_idx,
                    "cells": [
                        {"address": cell.coordinate, "value": _cell_value_repr(cell.value)}
                        for cell in cells
                        if cell.value is not None and str(cell.value).strip()
                    ],
                }
            )
    active_sheet = sheet.title
    workbook.close()
    return ToolResult(
        name="read_spreadsheet",
        output={
            "attachment_id": str(attachment.id),
            "file_name": attachment.file_name,
            "sheets": all_sheets,
            "sheet": active_sheet,
            "start_row": start,
            "rows": rows,
        },
    )


async def search_spreadsheet(
    context: SpreadsheetToolContext,
    *,
    query: str,
    attachment_id: str | None = None,
    file_name: str | None = None,
    sheet_name: str | None = None,
    match_case: bool = False,
    max_results: int | None = None,
) -> ToolResult:
    needle = (query or "").strip()
    if not needle:
        return ToolResult(
            name="search_spreadsheet",
            output={"error": "query is required"},
        )
    attachments = _chat_attachments(context.session, context.chat_id)
    attachments.extend(_pending_chat_attachments(context.pending_attachments))
    attachment = _pick_spreadsheet_attachment(
        attachments, attachment_id=attachment_id, file_name=file_name
    )
    if not attachment:
        return ToolResult(
            name="search_spreadsheet",
            output={"error": "Spreadsheet attachment not found."},
        )
    workbook, err = _load_attachment_workbook(attachment)
    if err or workbook is None:
        return ToolResult(name="search_spreadsheet", output={"error": err})

    limit = max(1, min(int(max_results or MAX_SEARCH_HITS), MAX_SEARCH_HITS))
    hits: list[dict[str, str]] = []
    try:
        sheets = (
            [_resolve_sheet(workbook, sheet_name)] if sheet_name else workbook.worksheets
        )
    except ValueError as exc:
        sheets_hint = _sheet_names(workbook)
        workbook.close()
        return ToolResult(
            name="search_spreadsheet",
            output={"error": str(exc), "sheets": sheets_hint},
        )
    try:
        for sheet in sheets:
            for row in sheet.iter_rows(values_only=False):
                for cell in row:
                    raw = cell.value
                    if raw is None:
                        continue
                    text = _cell_value_repr(raw)
                    if not text:
                        continue
                    if match_case:
                        matched = needle in text
                    else:
                        matched = needle.lower() in text.lower()
                    if not matched:
                        continue
                    hits.append(
                        {
                            "sheet": sheet.title,
                            "cell": cell.coordinate,
                            "value": text[:500],
                        }
                    )
                    if len(hits) >= limit:
                        break
                if len(hits) >= limit:
                    break
            if len(hits) >= limit:
                break
    finally:
        workbook.close()

    return ToolResult(
        name="search_spreadsheet",
        output={
            "attachment_id": str(attachment.id),
            "file_name": attachment.file_name,
            "query": query,
            "match_count": len(hits),
            "truncated": len(hits) >= limit,
            "matches": hits,
        },
    )


async def replace_spreadsheet_text(
    context: SpreadsheetToolContext,
    *,
    find: str,
    replace: str,
    attachment_id: str | None = None,
    file_name: str | None = None,
    sheet_name: str | None = None,
    match_case: bool = False,
    use_regex: bool = False,
) -> ToolResult:
    find_text = find if find is not None else ""
    if not find_text:
        return ToolResult(
            name="replace_spreadsheet_text",
            output={"error": "find is required"},
        )
    attachments = _chat_attachments(context.session, context.chat_id)
    attachments.extend(_pending_chat_attachments(context.pending_attachments))
    attachment = _pick_spreadsheet_attachment(
        attachments, attachment_id=attachment_id, file_name=file_name
    )
    if not attachment:
        return ToolResult(
            name="replace_spreadsheet_text",
            output={"error": "Spreadsheet attachment not found."},
        )
    workbook, err = _load_attachment_workbook(attachment)
    if err or workbook is None:
        return ToolResult(name="replace_spreadsheet_text", output={"error": err})

    pattern: re.Pattern[str] | None = None
    if use_regex:
        flags = 0 if match_case else re.IGNORECASE
        try:
            pattern = re.compile(find_text, flags)
        except re.error as exc:
            workbook.close()
            return ToolResult(
                name="replace_spreadsheet_text",
                output={"error": f"Invalid regex: {exc}"},
            )

    changed: list[dict[str, str]] = []
    try:
        sheets = (
            [_resolve_sheet(workbook, sheet_name)] if sheet_name else workbook.worksheets
        )
    except ValueError as exc:
        sheets_hint = _sheet_names(workbook)
        workbook.close()
        return ToolResult(
            name="replace_spreadsheet_text",
            output={"error": str(exc), "sheets": sheets_hint},
        )
    try:
        for sheet in sheets:
            for row in sheet.iter_rows(values_only=False):
                for cell in row:
                    value = cell.value
                    if not isinstance(value, str) or not value:
                        continue
                    if pattern is not None:
                        new_value, count = pattern.subn(replace, value)
                    elif match_case:
                        if find_text not in value:
                            continue
                        new_value = value.replace(find_text, replace)
                        count = value.count(find_text)
                    else:
                        lower = value.lower()
                        needle = find_text.lower()
                        if needle not in lower:
                            continue
                        new_value, count = _replace_case_insensitive(
                            value, find_text, replace
                        )
                    if count <= 0 or new_value == value:
                        continue
                    cell.value = new_value
                    changed.append(
                        {
                            "sheet": sheet.title,
                            "cell": cell.coordinate,
                            "before": value[:200],
                            "after": new_value[:200],
                        }
                    )
    except ValueError as exc:
        workbook.close()
        return ToolResult(name="replace_spreadsheet_text", output={"error": str(exc)})

    buffer = BytesIO()
    workbook.save(buffer)
    workbook.close()
    out_bytes = buffer.getvalue()
    out_name = attachment.file_name or "workbook.xlsx"
    if not out_name.lower().endswith(".xlsx"):
        out_name = f"{out_name}.xlsx"

    return ToolResult(
        name="replace_spreadsheet_text",
        output={
            "attachment_id": str(attachment.id),
            "source_file_name": attachment.file_name,
            "file_name": out_name,
            "cells_changed": len(changed),
            "changes": changed[:30],
            "more_changes": max(0, len(changed) - 30),
        },
        attachments=[
            {
                "file_name": out_name,
                "content_type": _XLSX_CONTENT,
                "data_base64": base64.b64encode(out_bytes).decode("ascii"),
            }
        ],
    )


def _replace_case_insensitive(text: str, find: str, replace: str) -> tuple[str, int]:
    lower_text = text.lower()
    lower_find = find.lower()
    parts: list[str] = []
    start = 0
    count = 0
    while True:
        idx = lower_text.find(lower_find, start)
        if idx < 0:
            parts.append(text[start:])
            break
        parts.append(text[start:idx])
        parts.append(replace)
        count += 1
        start = idx + len(find)
    return "".join(parts), count
