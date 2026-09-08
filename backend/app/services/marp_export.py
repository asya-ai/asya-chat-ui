"""Export cowork Marp presentations via scraper marp-cli (Chromium print)."""

from __future__ import annotations

import base64
import logging
import re
from typing import Literal
from uuid import UUID

import httpx
from sqlmodel import Session, select

from app.core.config import settings
from app.models.entities import ChatMessage, ChatMessageAttachment
from app.services.file_storage import attachment_bytes
from app.services.tools.web_tools import _resolve_scraper_request_url

logger = logging.getLogger(__name__)

PresentationExportFormat = Literal["pdf", "pptx"]

_MARKDOWN_IMAGE_RE = re.compile(r"(!\[[^\]]*\]\()([^)\s]+)(\))", re.MULTILINE)
_BARE_FILE_RE = re.compile(r"^[^/?#]+$")


def _basename(src: str) -> str:
    cleaned = src.strip().strip("\"'")
    without_query = cleaned.split("?", 1)[0]
    return without_query.rstrip("/").rsplit("/", 1)[-1]


def _chat_image_data_urls(session: Session, chat_id: UUID) -> dict[str, str]:
    """Map attachment file_name / id → data: URL for images in this chat."""
    rows = session.exec(
        select(ChatMessageAttachment)
        .join(ChatMessage, ChatMessage.id == ChatMessageAttachment.message_id)
        .where(ChatMessage.chat_id == chat_id)
        .where(ChatMessageAttachment.content_type.like("image/%"))
    ).all()

    mapping: dict[str, str] = {}
    for attachment in rows:
        content_type = (attachment.content_type or "image/png").split(";")[0].strip()
        if not content_type.startswith("image/"):
            continue
        data = attachment_bytes(
            file_path=attachment.file_path,
            data_base64=attachment.data_base64,
        )
        if not data:
            continue
        encoded = base64.b64encode(data).decode("ascii")
        data_url = f"data:{content_type};base64,{encoded}"
        if attachment.file_name:
            mapping[attachment.file_name] = data_url
            mapping[_basename(attachment.file_name)] = data_url
        mapping[str(attachment.id)] = data_url
    return mapping


def resolve_presentation_markdown_images(markdown: str, image_urls: dict[str, str]) -> str:
    if not markdown or not image_urls:
        return markdown

    def _replace(match: re.Match[str]) -> str:
        prefix, src, suffix = match.group(1), match.group(2), match.group(3)
        key = _basename(src)
        resolved = image_urls.get(src) or image_urls.get(key)
        if not resolved and _BARE_FILE_RE.match(src.strip()):
            resolved = image_urls.get(src.strip())
        if not resolved or resolved == src:
            return match.group(0)
        return f"{prefix}{resolved}{suffix}"

    return _MARKDOWN_IMAGE_RE.sub(_replace, markdown)


def prepare_presentation_markdown(session: Session, *, chat_id: UUID, markdown: str) -> str:
    image_urls = _chat_image_data_urls(session, chat_id)
    return resolve_presentation_markdown_images(markdown or "", image_urls)


async def export_presentation_via_scraper(
    *,
    markdown: str,
    format: PresentationExportFormat,
) -> tuple[bytes, str]:
    """
    Call scraper /marp/export.

    Returns (file_bytes, content_type).
    """
    if not settings.scraper_url:
        raise RuntimeError("SCRAPER_URL is not configured")

    request_url, headers, scraper_host = _resolve_scraper_request_url("/marp/export")
    payload = {"markdown": markdown, "format": format}
    try:
        async with httpx.AsyncClient(timeout=180.0, trust_env=False) as client:
            response = await client.post(request_url, json=payload, headers=headers)
    except OSError as exc:
        logger.warning(
            "marp export scraper DNS failed scraper_host=%s err=%s",
            scraper_host,
            exc,
        )
        raise RuntimeError(f"Scraper unreachable ({scraper_host}): {exc}") from exc
    except httpx.HTTPError as exc:
        logger.warning(
            "marp export scraper transport failed scraper_host=%s err=%s",
            scraper_host,
            exc,
        )
        raise RuntimeError(f"Scraper unreachable ({scraper_host}): {exc}") from exc

    if response.status_code >= 400:
        detail = ""
        try:
            data = response.json()
            if isinstance(data, dict):
                detail = str(data.get("detail") or data.get("error") or "").strip()
        except Exception:
            detail = (response.text or "").strip()
        message = f"Marp export failed ({response.status_code})"
        if detail:
            message = f"{message}: {detail[:500]}"
        raise RuntimeError(message)

    content_type = response.headers.get("content-type") or (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        if format == "pptx"
        else "application/pdf"
    )
    return response.content, content_type.split(";")[0].strip()
