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


def _write_bytes(relative_path: str, data: bytes) -> tuple[str, int]:
    absolute_path = _absolute_path(relative_path)
    absolute_path.parent.mkdir(parents=True, exist_ok=True)
    absolute_path.write_bytes(data)
    return relative_path, len(data)


def write_chat_upload_file(
    *,
    chat_id: UUID,
    upload_id: UUID,
    file_name: str,
    data: bytes | None = None,
    data_base64: str | None = None,
) -> tuple[str, int]:
    payload = data if data is not None else base64.b64decode(data_base64 or "")
    safe_name = _sanitize_filename(file_name)
    relative_path = f"chats/{chat_id}/uploads/{upload_id}_{safe_name}"
    return _write_bytes(relative_path, payload)


def write_chat_attachment_file(
    *,
    chat_id: UUID,
    message_id: UUID,
    attachment_id: UUID,
    file_name: str,
    data: bytes | None = None,
    data_base64: str | None = None,
) -> tuple[str, int]:
    payload = data if data is not None else base64.b64decode(data_base64 or "")
    safe_name = _sanitize_filename(file_name)
    relative_path = (
        f"chats/{chat_id}/attachments/{message_id}/{attachment_id}_{safe_name}"
    )
    return _write_bytes(relative_path, payload)


def write_agent_source_file(
    *,
    agent_id: UUID,
    source_id: UUID,
    file_name: str,
    data: bytes,
) -> tuple[str, int]:
    safe_name = _sanitize_filename(file_name)
    relative_path = f"agents/{agent_id}/sources/{source_id}_{safe_name}"
    return _write_bytes(relative_path, data)


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


def attachment_bytes(
    *,
    file_path: str | None = None,
    data_base64: str | None = None,
) -> bytes | None:
    payload = maybe_read_file_bytes(file_path)
    if payload is not None:
        return payload
    if not data_base64:
        return None
    try:
        return base64.b64decode(data_base64)
    except Exception:
        return None


def attachment_size_bytes(
    *,
    file_path: str | None = None,
    data_base64: str | None = None,
) -> int:
    if file_path:
        try:
            return file_size(file_path)
        except Exception:
            pass
    if not data_base64:
        return 0
    padding = data_base64.count("=")
    return max(len(data_base64) * 3 // 4 - padding, 0)


def delete_file(file_path: str | None) -> None:
    if not file_path:
        return
    try:
        _absolute_path(file_path).unlink(missing_ok=True)
    except (OSError, ValueError):
        return


def store_chat_attachment_bytes(
    *,
    chat_id: UUID,
    message_id: UUID,
    attachment_id: UUID,
    file_name: str,
    data: bytes,
) -> str:
    relative_path, _size = write_chat_attachment_file(
        chat_id=chat_id,
        message_id=message_id,
        attachment_id=attachment_id,
        file_name=file_name,
        data=data,
    )
    return relative_path


def store_chat_upload_bytes(
    *,
    chat_id: UUID,
    upload_id: UUID,
    file_name: str,
    data: bytes,
) -> str:
    relative_path, _size = write_chat_upload_file(
        chat_id=chat_id,
        upload_id=upload_id,
        file_name=file_name,
        data=data,
    )
    return relative_path
