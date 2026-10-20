"""Detect archive / image project sources and guess member MIME types."""

from __future__ import annotations

import mimetypes
from pathlib import Path

IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
    ".heic",
    ".heif",
    ".avif",
}

ARCHIVE_EXTENSIONS = {
    ".zip",
    ".tar",
    ".tgz",
    ".tar.gz",
}

_ARCHIVE_MIME = {
    "application/zip",
    "application/x-zip-compressed",
    "application/x-tar",
    "application/gzip",
    "application/x-gzip",
    "application/x-gtar",
}


def _suffixes(file_name: str | None) -> str:
    name = (file_name or "").lower().strip()
    if name.endswith(".tar.gz"):
        return ".tar.gz"
    return Path(name).suffix


def is_image_file(*, file_name: str | None = None, content_type: str | None = None) -> bool:
    mime = (content_type or "").split(";")[0].strip().lower()
    if mime.startswith("image/") and mime != "image/svg+xml":
        return True
    return _suffixes(file_name) in IMAGE_EXTENSIONS


def is_archive_file(*, file_name: str | None = None, content_type: str | None = None) -> bool:
    mime = (content_type or "").split(";")[0].strip().lower()
    if mime in _ARCHIVE_MIME:
        return True
    return _suffixes(file_name) in ARCHIVE_EXTENSIONS


def guess_content_type(file_name: str | None, fallback: str = "application/octet-stream") -> str:
    mime, _ = mimetypes.guess_type(file_name or "")
    if mime:
        return mime
    if is_image_file(file_name=file_name):
        ext = _suffixes(file_name)
        if ext in {".jpg", ".jpeg"}:
            return "image/jpeg"
        if ext == ".png":
            return "image/png"
        if ext == ".gif":
            return "image/gif"
        if ext == ".webp":
            return "image/webp"
        return "image/png"
    if is_archive_file(file_name=file_name):
        ext = _suffixes(file_name)
        if ext == ".zip":
            return "application/zip"
        if ext in {".tgz", ".tar.gz"}:
            return "application/gzip"
        return "application/x-tar"
    return fallback
