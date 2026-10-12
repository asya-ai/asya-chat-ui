from __future__ import annotations

from datetime import datetime

from sqlmodel import Session

from app.models import WhitelabelSettings
from app.services.file_storage import delete_file, read_file_bytes, write_relative_file

ALLOWED_LOGO_TYPES = {
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/svg+xml",
    "image/x-icon",
    "image/vnd.microsoft.icon",
}
MAX_LOGO_BYTES = 2 * 1024 * 1024


def get_whitelabel_settings(session: Session) -> WhitelabelSettings:
    row = session.get(WhitelabelSettings, 1)
    if not row:
        row = WhitelabelSettings(id=1)
        session.add(row)
        session.commit()
        session.refresh(row)
    return row


def update_app_title(session: Session, app_title: str | None) -> WhitelabelSettings:
    row = get_whitelabel_settings(session)
    cleaned = (app_title or "").strip() or None
    row.app_title = cleaned
    row.updated_at = datetime.utcnow()
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def store_logo(
    session: Session,
    *,
    data: bytes,
    content_type: str,
    file_name: str,
) -> WhitelabelSettings:
    if len(data) > MAX_LOGO_BYTES:
        raise ValueError("Logo exceeds maximum size")
    if not data:
        raise ValueError("Empty upload")
    normalized_type = (content_type or "").strip().lower() or "application/octet-stream"
    if normalized_type not in ALLOWED_LOGO_TYPES:
        raise ValueError("Unsupported logo type")

    row = get_whitelabel_settings(session)
    if row.logo_path:
        delete_file(row.logo_path)

    ext = _logo_extension(file_name, normalized_type)
    relative_path = f"whitelabel/logo{ext}"
    write_relative_file(relative_path, data)
    row.logo_path = relative_path
    row.logo_content_type = normalized_type
    row.updated_at = datetime.utcnow()
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def clear_logo(session: Session) -> WhitelabelSettings:
    row = get_whitelabel_settings(session)
    if row.logo_path:
        delete_file(row.logo_path)
    row.logo_path = None
    row.logo_content_type = None
    row.updated_at = datetime.utcnow()
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def read_logo(session: Session) -> tuple[bytes, str] | None:
    row = get_whitelabel_settings(session)
    if not row.logo_path:
        return None
    try:
        data = read_file_bytes(row.logo_path)
    except FileNotFoundError:
        return None
    return data, row.logo_content_type or "application/octet-stream"


def _logo_extension(file_name: str, content_type: str) -> str:
    name = (file_name or "").rsplit(".", 1)
    if len(name) == 2 and name[1].isalnum() and len(name[1]) <= 8:
        return f".{name[1].lower()}"
    return {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
        "image/svg+xml": ".svg",
        "image/x-icon": ".ico",
        "image/vnd.microsoft.icon": ".ico",
    }.get(content_type, ".bin")
