from __future__ import annotations

import base64
import re
from pathlib import Path
from uuid import UUID

from app.core.config import settings


def _sanitize_filename(name: str) -> str:
    base = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", base).strip("._")
    return cleaned or "file"


def _base_dir() -> Path:
    return Path(settings.files_base_dir)


def _absolute_path(relative_path: str) -> Path:
    root = _base_dir().resolve()
    candidate = (root / relative_path).resolve()
    if not str(candidate).startswith(str(root)):
        raise ValueError("Invalid storage path")
    return candidate


def write_chat_upload_file(
    *,
    chat_id: UUID,
    upload_id: UUID,
    file_name: str,
    data_base64: str,
) -> tuple[str, int]:
    payload = base64.b64decode(data_base64)
    safe_name = _sanitize_filename(file_name)
    relative_path = f"chats/{chat_id}/uploads/{upload_id}_{safe_name}"
    absolute_path = _absolute_path(relative_path)
    absolute_path.parent.mkdir(parents=True, exist_ok=True)
    absolute_path.write_bytes(payload)
    return relative_path, len(payload)


def write_chat_attachment_file(
    *,
    chat_id: UUID,
    message_id: UUID,
    attachment_id: UUID,
    file_name: str,
    data_base64: str,
) -> tuple[str, int]:
    payload = base64.b64decode(data_base64)
    safe_name = _sanitize_filename(file_name)
    relative_path = (
        f"chats/{chat_id}/attachments/{message_id}/{attachment_id}_{safe_name}"
    )
    absolute_path = _absolute_path(relative_path)
    absolute_path.parent.mkdir(parents=True, exist_ok=True)
    absolute_path.write_bytes(payload)
    return relative_path, len(payload)


def read_file_bytes(file_path: str) -> bytes:
    return _absolute_path(file_path).read_bytes()


def file_size(file_path: str) -> int:
    return _absolute_path(file_path).stat().st_size


def maybe_read_file_bytes(file_path: str | None) -> bytes | None:
    if not file_path:
        return None
    try:
        return read_file_bytes(file_path)
    except Exception:
        return None
