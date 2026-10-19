from __future__ import annotations

import io
import logging
from pathlib import Path

from PIL import Image, UnidentifiedImageError

logger = logging.getLogger(__name__)

# Formats vision providers accept without conversion.
_PASSTHROUGH_MIME = frozenset(
    {
        "image/jpeg",
        "image/jpg",
        "image/png",
        "image/gif",
        "image/webp",
    }
)

_IMAGE_EXTENSIONS = frozenset(
    {
        ".avif",
        ".bmp",
        ".gif",
        ".heic",
        ".heics",
        ".heif",
        ".ico",
        ".j2k",
        ".jp2",
        ".jpeg",
        ".jpg",
        ".jxl",
        ".png",
        ".tga",
        ".tif",
        ".tiff",
        ".webp",
    }
)

_HEIF_BRANDS = frozenset(
    {
        b"heic",
        b"heix",
        b"hevc",
        b"hevx",
        b"heim",
        b"heis",
        b"hevm",
        b"hevs",
        b"mif1",
        b"msf1",
    }
)

_heif_registered = False


def _ensure_heif_support() -> None:
    global _heif_registered
    if _heif_registered:
        return
    try:
        from pillow_heif import register_heif_opener

        register_heif_opener()
    except Exception:
        logger.debug("pillow-heif unavailable; HEIC/HEIF conversion disabled", exc_info=True)
    _heif_registered = True


def _normalize_mime(content_type: str | None) -> str:
    return (content_type or "").split(";", 1)[0].strip().lower() or "application/octet-stream"


def _extension(file_name: str | None) -> str:
    return Path(file_name or "").suffix.lower()


def _looks_like_heif(data: bytes) -> bool:
    if len(data) < 12:
        return False
    if data[4:8] != b"ftyp":
        return False
    return data[8:12] in _HEIF_BRANDS


def _looks_like_image(file_name: str, content_type: str, data: bytes) -> bool:
    mime = _normalize_mime(content_type)
    if mime.startswith("image/") and mime != "image/svg+xml":
        return True
    if _extension(file_name) in _IMAGE_EXTENSIONS:
        return True
    return _looks_like_heif(data)


def _replace_extension(file_name: str, suffix: str) -> str:
    path = Path(file_name or "attachment")
    stem = path.stem or "attachment"
    return f"{stem}{suffix}"


def _to_jpeg_bytes(data: bytes) -> bytes:
    _ensure_heif_support()
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        if image.mode in {"RGBA", "LA"} or (
            image.mode == "P" and "transparency" in image.info
        ):
            rgba = image.convert("RGBA")
            background = Image.new("RGB", rgba.size, (255, 255, 255))
            background.paste(rgba, mask=rgba.getchannel("A"))
            rgb = background
        else:
            rgb = image.convert("RGB")
        out = io.BytesIO()
        rgb.save(out, format="JPEG", quality=90, optimize=True)
        return out.getvalue()


def normalize_image_attachment(
    file_name: str,
    content_type: str,
    data: bytes,
) -> tuple[str, str, bytes]:
    """Pass through known image formats; convert other images to JPEG."""
    if not data:
        return file_name, content_type, data

    mime = _normalize_mime(content_type)
    if mime in _PASSTHROUGH_MIME:
        if mime == "image/jpg":
            return file_name, "image/jpeg", data
        return file_name, mime, data

    if mime == "image/svg+xml" or _extension(file_name) == ".svg":
        return file_name, content_type, data

    if not _looks_like_image(file_name, content_type, data):
        return file_name, content_type, data

    try:
        jpeg = _to_jpeg_bytes(data)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        logger.info(
            "Skipping image normalize for %s (%s): %s",
            file_name,
            mime,
            exc,
        )
        return file_name, content_type, data
    except Exception:
        logger.exception("Unexpected image normalize failure for %s", file_name)
        return file_name, content_type, data

    return _replace_extension(file_name, ".jpg"), "image/jpeg", jpeg
