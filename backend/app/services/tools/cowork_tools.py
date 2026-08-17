from __future__ import annotations

import difflib
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlmodel import Session, select

from app.models.entities import ChatCoworkDocument, CoworkFormat
from app.services.tools.registry import ToolResult

logger = logging.getLogger(__name__)

COWORK_EVENT_CONTENT_LIMIT = 50_000
USER_EDIT_DIFF_LIMIT = 12_000

_LANGUAGE_EXTENSIONS: dict[str, str] = {
    "python": ".py",
    "py": ".py",
    "javascript": ".js",
    "js": ".js",
    "typescript": ".ts",
    "ts": ".ts",
    "tsx": ".tsx",
    "jsx": ".jsx",
    "html": ".html",
    "css": ".css",
    "scss": ".scss",
    "json": ".json",
    "yaml": ".yaml",
    "yml": ".yml",
    "toml": ".toml",
    "rust": ".rs",
    "go": ".go",
    "java": ".java",
    "kotlin": ".kt",
    "swift": ".swift",
    "ruby": ".rb",
    "php": ".php",
    "c": ".c",
    "cpp": ".cpp",
    "csharp": ".cs",
    "cs": ".cs",
    "shell": ".sh",
    "bash": ".sh",
    "sql": ".sql",
    "r": ".r",
    "markdown": ".md",
    "md": ".md",
}

_FORMAT_MIME: dict[CoworkFormat, str] = {
    CoworkFormat.markdown: "text/markdown; charset=utf-8",
    CoworkFormat.code: "text/plain; charset=utf-8",
    CoworkFormat.text: "text/plain; charset=utf-8",
    CoworkFormat.json: "application/json; charset=utf-8",
    CoworkFormat.csv: "text/csv; charset=utf-8",
    CoworkFormat.presentation: "text/markdown; charset=utf-8",
}

_LANGUAGE_MIME: dict[str, str] = {
    "python": "text/x-python; charset=utf-8",
    "javascript": "text/javascript; charset=utf-8",
    "typescript": "text/typescript; charset=utf-8",
    "html": "text/html; charset=utf-8",
    "css": "text/css; charset=utf-8",
    "json": "application/json; charset=utf-8",
    "sql": "application/sql; charset=utf-8",
}


@dataclass
class CoworkToolContext:
    session: Session
    chat_id: UUID


def _now() -> datetime:
    return datetime.utcnow()


def _sanitize_file_name(name: str) -> str:
    base = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", base).strip("._")
    return cleaned or "document"


def _parse_format(value: str | None) -> CoworkFormat:
    raw = (value or "text").strip().lower()
    try:
        return CoworkFormat(raw)
    except ValueError:
        return CoworkFormat.text


def default_extension(fmt: CoworkFormat, language: str | None) -> str:
    if fmt == CoworkFormat.markdown:
        return ".md"
    if fmt == CoworkFormat.presentation:
        return ".md"
    if fmt == CoworkFormat.json:
        return ".json"
    if fmt == CoworkFormat.csv:
        return ".csv"
    if fmt == CoworkFormat.code and language:
        key = language.strip().lower()
        return _LANGUAGE_EXTENSIONS.get(key, ".txt")
    return ".txt"


def ensure_file_name(
    file_name: str | None, *, title: str, fmt: CoworkFormat, language: str | None
) -> str:
    if file_name and file_name.strip():
        return _sanitize_file_name(file_name.strip())
    stem = _sanitize_file_name(title.strip() or "document")
    if "." in stem:
        return stem
    return f"{stem}{default_extension(fmt, language)}"


def mime_for_document(doc: ChatCoworkDocument) -> str:
    if doc.format == CoworkFormat.code and doc.language:
        key = doc.language.strip().lower()
        if key in _LANGUAGE_MIME:
            return _LANGUAGE_MIME[key]
    return _FORMAT_MIME.get(doc.format, "text/plain; charset=utf-8")


def document_payload(
    doc: ChatCoworkDocument, *, include_content: bool = True
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "document_id": str(doc.id),
        "chat_id": str(doc.chat_id),
        "title": doc.title,
        "file_name": doc.file_name,
        "format": doc.format.value if isinstance(doc.format, CoworkFormat) else doc.format,
        "language": doc.language,
        "version": doc.version,
        "is_active": doc.is_active,
        "last_assistant_version": doc.last_assistant_version,
        "user_edited": doc.version > doc.last_assistant_version,
        "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
    }
    if include_content:
        payload["content"] = doc.content
    return payload


def coworking_tool_event(
    doc: ChatCoworkDocument,
    *,
    action: str,
    call_id: str | None = None,
) -> dict[str, Any]:
    include_content = len(doc.content or "") <= COWORK_EVENT_CONTENT_LIMIT
    event: dict[str, Any] = {
        "type": "coworking",
        "action": action,
        "document_id": str(doc.id),
        "title": doc.title,
        "file_name": doc.file_name,
        "format": doc.format.value if isinstance(doc.format, CoworkFormat) else doc.format,
        "language": doc.language,
        "version": doc.version,
        "last_assistant_version": doc.last_assistant_version,
        "user_edited": doc.version > doc.last_assistant_version,
    }
    if call_id:
        event["id"] = call_id
    if include_content:
        event["content"] = doc.content
    return event


def get_active_document(
    session: Session, chat_id: UUID
) -> ChatCoworkDocument | None:
    return session.exec(
        select(ChatCoworkDocument)
        .where(
            ChatCoworkDocument.chat_id == chat_id,
            ChatCoworkDocument.is_active.is_(True),
        )
        .order_by(ChatCoworkDocument.updated_at.desc())
    ).first()


def list_documents(session: Session, chat_id: UUID) -> list[ChatCoworkDocument]:
    # Stable order by creation time so updates / re-opens don't reshuffle the UI.
    return list(
        session.exec(
            select(ChatCoworkDocument)
            .where(ChatCoworkDocument.chat_id == chat_id)
            .order_by(ChatCoworkDocument.created_at.asc(), ChatCoworkDocument.id.asc())
        ).all()
    )


def get_document(
    session: Session, chat_id: UUID, doc_id: UUID
) -> ChatCoworkDocument | None:
    doc = session.get(ChatCoworkDocument, doc_id)
    if not doc or doc.chat_id != chat_id:
        return None
    return doc


def _deactivate_others(session: Session, chat_id: UUID, keep_id: UUID | None = None) -> None:
    docs = session.exec(
        select(ChatCoworkDocument).where(
            ChatCoworkDocument.chat_id == chat_id,
            ChatCoworkDocument.is_active.is_(True),
        )
    ).all()
    for doc in docs:
        if keep_id and doc.id == keep_id:
            continue
        doc.is_active = False
        doc.updated_at = _now()
        session.add(doc)


def activate_document(
    session: Session, chat_id: UUID, doc_id: UUID
) -> ChatCoworkDocument | None:
    doc = get_document(session, chat_id, doc_id)
    if not doc:
        return None
    _deactivate_others(session, chat_id, keep_id=doc.id)
    doc.is_active = True
    doc.updated_at = _now()
    session.add(doc)
    session.commit()
    session.refresh(doc)
    return doc


def delete_document(
    session: Session, chat_id: UUID, doc_id: UUID
) -> ChatCoworkDocument | None:
    """Delete a cowork document. If it was active, activate a stable neighbor.

    Returns the newly active document (if any), else None when the chat has no docs left.
    Raises KeyError when the document is missing.
    """
    docs = list_documents(session, chat_id)
    index = next((i for i, item in enumerate(docs) if item.id == doc_id), None)
    if index is None:
        raise KeyError("document_not_found")
    target = docs[index]
    was_active = bool(target.is_active)
    session.delete(target)
    session.commit()
    if not was_active:
        return get_active_document(session, chat_id)

    remaining = list_documents(session, chat_id)
    if not remaining:
        return None
    # Prefer the previous item in creation order; otherwise the next (now at index).
    replacement = remaining[index - 1] if index > 0 else remaining[0]
    return activate_document(session, chat_id, replacement.id)


def bump_content(
    doc: ChatCoworkDocument,
    content: str,
    *,
    sync_assistant_snapshot: bool = False,
) -> None:
    doc.content = content
    doc.version = int(doc.version or 0) + 1
    doc.updated_at = _now()
    if sync_assistant_snapshot:
        doc.last_assistant_version = doc.version
        doc.content_at_assistant_version = content


def apply_user_patch(
    session: Session,
    doc: ChatCoworkDocument,
    *,
    content: str,
    base_version: int,
) -> tuple[ChatCoworkDocument | None, ChatCoworkDocument]:
    """Apply user edit. Returns (updated_doc, latest_doc). updated_doc is None on conflict."""
    if doc.version != base_version:
        return None, doc
    bump_content(doc, content, sync_assistant_snapshot=False)
    session.add(doc)
    session.commit()
    session.refresh(doc)
    return doc, doc


def mark_assistant_synced(session: Session, chat_id: UUID) -> ChatCoworkDocument | None:
    doc = get_active_document(session, chat_id)
    if not doc:
        return None
    doc.last_assistant_version = doc.version
    doc.content_at_assistant_version = doc.content
    doc.updated_at = _now()
    session.add(doc)
    session.commit()
    session.refresh(doc)
    return doc


def build_user_edit_context_message(doc: ChatCoworkDocument) -> str | None:
    if doc.version <= doc.last_assistant_version:
        return None
    before = doc.content_at_assistant_version or ""
    after = doc.content or ""
    diff_lines = list(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile=f"{doc.file_name}@v{doc.last_assistant_version}",
            tofile=f"{doc.file_name}@v{doc.version}",
            lineterm="",
        )
    )
    diff_text = "\n".join(diff_lines)
    if len(diff_text) > USER_EDIT_DIFF_LIMIT:
        half = USER_EDIT_DIFF_LIMIT // 2
        diff_text = (
            diff_text[:half]
            + "\n… [diff truncated] …\n"
            + diff_text[-half:]
        )
    if not diff_text.strip():
        diff_text = "(content changed but no line-level diff available)"
    return (
        f'[Coworking] User edited "{doc.file_name}" '
        f"(v{doc.last_assistant_version}→v{doc.version}). "
        "Review the diff and continue editing with cowork tools if needed.\n"
        f"```diff\n{diff_text}\n```"
    )


async def start_coworking(
    context: CoworkToolContext,
    *,
    title: str | None = None,
    file_name: str | None = None,
    format: str | None = None,
    language: str | None = None,
    content: str | None = None,
) -> ToolResult:
    fmt = _parse_format(format)
    lang = (language or "").strip() or None
    # Language is only stored for code (and optional json/csv hints).
    stored_language = lang if fmt in {CoworkFormat.code, CoworkFormat.json, CoworkFormat.csv} else None
    doc_title = (title or "").strip() or "Untitled"
    resolved_name = ensure_file_name(
        file_name, title=doc_title, fmt=fmt, language=lang
    )
    initial = content if content is not None else ""

    _deactivate_others(context.session, context.chat_id)
    doc = ChatCoworkDocument(
        chat_id=context.chat_id,
        title=doc_title,
        file_name=resolved_name,
        format=fmt,
        language=stored_language,
        content=initial,
        version=1,
        is_active=True,
        last_assistant_version=1,
        content_at_assistant_version=initial,
        created_at=_now(),
        updated_at=_now(),
    )
    context.session.add(doc)
    context.session.commit()
    context.session.refresh(doc)

    payload = document_payload(doc)
    payload["status"] = "started"
    payload["action"] = "open"
    payload["note"] = (
        "Document is open in the chat side panel. Do not invent URLs or markdown "
        "links to file_name; refer to the title as plain text only."
    )
    return ToolResult(name="start_coworking", output=payload)


async def cowork_read(
    context: CoworkToolContext,
    *,
    offset: int | None = None,
    limit: int | None = None,
) -> ToolResult:
    doc = get_active_document(context.session, context.chat_id)
    if not doc:
        return ToolResult(
            name="cowork_read",
            output={"error": "No active coworking document. Call start_coworking first."},
        )
    lines = (doc.content or "").splitlines(keepends=True)
    total_lines = len(lines)
    start = max(0, int(offset or 0))
    if limit is None:
        selected = lines[start:]
        end = total_lines
    else:
        capped = max(1, min(int(limit), 2000))
        end = min(total_lines, start + capped)
        selected = lines[start:end]
    return ToolResult(
        name="cowork_read",
        output={
            **document_payload(doc, include_content=False),
            "content": "".join(selected),
            "line_offset": start,
            "line_end": end,
            "total_lines": total_lines,
            "has_more": end < total_lines,
        },
    )


async def cowork_write(
    context: CoworkToolContext,
    *,
    content: str,
) -> ToolResult:
    doc = get_active_document(context.session, context.chat_id)
    if not doc:
        return ToolResult(
            name="cowork_write",
            output={"error": "No active coworking document. Call start_coworking first."},
        )
    bump_content(doc, content if content is not None else "", sync_assistant_snapshot=True)
    context.session.add(doc)
    context.session.commit()
    context.session.refresh(doc)
    payload = document_payload(doc)
    payload["status"] = "written"
    payload["action"] = "update"
    return ToolResult(name="cowork_write", output=payload)


async def cowork_str_replace(
    context: CoworkToolContext,
    *,
    old_str: str,
    new_str: str,
    replace_all: bool = False,
) -> ToolResult:
    doc = get_active_document(context.session, context.chat_id)
    if not doc:
        return ToolResult(
            name="cowork_str_replace",
            output={"error": "No active coworking document. Call start_coworking first."},
        )
    if old_str is None or old_str == "":
        return ToolResult(
            name="cowork_str_replace",
            output={"error": "old_str is required and must be non-empty."},
        )
    content = doc.content or ""
    count = content.count(old_str)
    if count == 0:
        return ToolResult(
            name="cowork_str_replace",
            output={
                "error": "old_str not found in the document. Call cowork_read and retry with exact text.",
                "version": doc.version,
            },
        )
    if count > 1 and not replace_all:
        return ToolResult(
            name="cowork_str_replace",
            output={
                "error": (
                    f"old_str matched {count} times. Provide more surrounding context "
                    "to make it unique, or set replace_all=true."
                ),
                "match_count": count,
                "version": doc.version,
            },
        )
    if replace_all:
        updated = content.replace(old_str, new_str if new_str is not None else "")
        replacements = count
    else:
        updated = content.replace(old_str, new_str if new_str is not None else "", 1)
        replacements = 1
    bump_content(doc, updated, sync_assistant_snapshot=True)
    context.session.add(doc)
    context.session.commit()
    context.session.refresh(doc)
    payload = document_payload(doc)
    payload["status"] = "replaced"
    payload["action"] = "update"
    payload["replacements"] = replacements
    return ToolResult(name="cowork_str_replace", output=payload)


async def cowork_append(
    context: CoworkToolContext,
    *,
    text: str,
) -> ToolResult:
    doc = get_active_document(context.session, context.chat_id)
    if not doc:
        return ToolResult(
            name="cowork_append",
            output={"error": "No active coworking document. Call start_coworking first."},
        )
    bump_content(
        doc,
        (doc.content or "") + (text if text is not None else ""),
        sync_assistant_snapshot=True,
    )
    context.session.add(doc)
    context.session.commit()
    context.session.refresh(doc)
    payload = document_payload(doc)
    payload["status"] = "appended"
    payload["action"] = "update"
    return ToolResult(name="cowork_append", output=payload)
