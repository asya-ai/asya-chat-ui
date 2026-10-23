"""Prepare project sources before embedding: expand archives, describe images."""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import tarfile
import zipfile
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any
from uuid import UUID

from sqlmodel import Session, select

from app.core.config import settings
from app.models import (
    Agent,
    AgentChunk,
    AgentEmbedding,
    AgentSource,
    AgentSourceKind,
    AgentSourceStatus,
    ChatModel,
    OrgModel,
    UsageEvent,
)
from app.services.agents.source_media import (
    guess_content_type,
    is_archive_file,
    is_image_file,
)
from app.services.agents.source_parsing import extract_text_from_file
from app.services.file_storage import delete_file, maybe_read_file_bytes, write_agent_source_file
from app.services.image_normalize import normalize_image_attachment
from app.services.instance_providers import get_provider_for_org
from app.services.model_capabilities import ensure_model_capabilities, supports_image_input
from app.services.org_service import require_provider_enabled

logger = logging.getLogger(__name__)

_SKIP_ARCHIVE_NAME_PARTS = {"__macosx", ".ds_store"}
_IMAGE_DESCRIBE_PROMPT = (
    "Extract any readable text in this image (OCR). "
    "Then describe the image briefly for search and retrieval. "
    "Reply in this exact format:\n"
    "Description: <1-3 sentences>\n"
    "Extracted text:\n"
    "<text or (none)>"
)
_IMAGE_TEXT_MAX_CHARS = 100_000

# Reuse one loop per process (same rationale as workers/tasks._run_coro).
_prepare_loop: asyncio.AbstractEventLoop | None = None


def _run_coro(coro: Any) -> Any:
    global _prepare_loop
    if _prepare_loop is None or _prepare_loop.is_closed():
        _prepare_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_prepare_loop)
    return _prepare_loop.run_until_complete(coro)


def _safe_archive_member_name(raw_name: str) -> str | None:
    name = (raw_name or "").replace("\\", "/").strip()
    if not name or name.endswith("/"):
        return None
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        return None
    lowered_parts = {part.lower() for part in path.parts}
    if lowered_parts & _SKIP_ARCHIVE_NAME_PARTS:
        return None
    cleaned = str(path).lstrip("./")
    return cleaned or None


def _delete_source_record(session: Session, source: AgentSource) -> None:
    chunks = session.exec(select(AgentChunk).where(AgentChunk.source_id == source.id)).all()
    chunk_ids = [chunk.id for chunk in chunks]
    if chunk_ids:
        embeddings = session.exec(
            select(AgentEmbedding).where(AgentEmbedding.chunk_id.in_(chunk_ids))
        ).all()
        for embedding in embeddings:
            session.delete(embedding)
        session.flush()
    for chunk in chunks:
        session.delete(chunk)
    session.flush()
    delete_file(source.file_path)
    session.delete(source)
    session.flush()


def delete_source_tree(session: Session, source: AgentSource) -> None:
    """Delete a source and any nested archive children."""
    children = session.exec(
        select(AgentSource).where(AgentSource.parent_source_id == source.id)
    ).all()
    for child in children:
        delete_source_tree(session, child)
    _delete_source_record(session, source)


def _listing_text(archive_title: str, children: list[AgentSource]) -> str:
    lines = [f"Archive: {archive_title}", f"Files ({len(children)}):"]
    for child in children:
        name = child.file_name or child.title
        status = child.status.value if hasattr(child.status, "value") else str(child.status)
        lines.append(f"- {name} [{status}]")
    return "\n".join(lines)


def _iter_archive_members(file_name: str | None, data: bytes) -> list[tuple[str, bytes]]:
    name = (file_name or "").lower()
    members: list[tuple[str, bytes]] = []
    max_files = max(1, int(settings.attachments_max_files or 50))
    max_bytes = max(1, int(settings.attachments_max_file_bytes or 50_000_000))

    if name.endswith(".zip") or data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                safe = _safe_archive_member_name(info.filename)
                if not safe:
                    continue
                if info.file_size > max_bytes:
                    logger.warning("Skipping oversized archive member %s", safe)
                    continue
                if len(members) >= max_files:
                    break
                try:
                    payload = archive.read(info)
                except Exception:
                    logger.warning("Failed reading archive member %s", safe, exc_info=True)
                    continue
                if not payload or len(payload) > max_bytes:
                    continue
                members.append((safe, payload))
        return members

    if (
        name.endswith(".tar")
        or name.endswith(".tgz")
        or name.endswith(".tar.gz")
        or tarfile.is_tarfile(io.BytesIO(data))
    ):
        mode = "r:gz" if name.endswith((".tgz", ".tar.gz")) or data[:2] == b"\x1f\x8b" else "r:"
        with tarfile.open(fileobj=io.BytesIO(data), mode=mode) as archive:
            for info in archive.getmembers():
                if not info.isfile():
                    continue
                safe = _safe_archive_member_name(info.name)
                if not safe:
                    continue
                if info.size > max_bytes:
                    logger.warning("Skipping oversized archive member %s", safe)
                    continue
                if len(members) >= max_files:
                    break
                handle = archive.extractfile(info)
                if handle is None:
                    continue
                try:
                    payload = handle.read(max_bytes + 1)
                finally:
                    handle.close()
                if not payload or len(payload) > max_bytes:
                    continue
                members.append((safe, payload))
        return members

    raise ValueError("Unsupported archive format. Upload a .zip or .tar.gz file.")


def _create_child_source(
    session: Session,
    *,
    parent: AgentSource,
    file_name: str,
    content_type: str,
    data: bytes,
) -> AgentSource:
    now = datetime.utcnow()
    member_name = file_name
    member_type = content_type
    member_data = data
    metadata: dict | None = None

    if is_image_file(file_name=member_name, content_type=member_type):
        member_name, member_type, member_data = normalize_image_attachment(
            member_name, member_type, member_data
        )
        metadata = {"is_image": True}
        content_text = ""
    elif is_archive_file(file_name=member_name, content_type=member_type):
        metadata = {"is_archive": True}
        content_text = ""
    else:
        try:
            content_text = extract_text_from_file(
                file_name=member_name,
                content_type=member_type,
                data=member_data,
            )
        except ValueError:
            # Keep binary members listed under the archive but skip indexing text.
            content_text = f"[Binary file: {member_name}]"
            metadata = {"binary_skip": True}

    child = AgentSource(
        agent_id=parent.agent_id,
        kind=AgentSourceKind.file,
        title=member_name,
        file_name=member_name,
        content_type=member_type,
        content_text=content_text,
        parent_source_id=parent.id,
        status=AgentSourceStatus.queued,
        metadata_json=metadata,
        created_at=now,
        updated_at=now,
    )
    session.add(child)
    session.flush()
    relative_path, _size = write_agent_source_file(
        agent_id=child.agent_id,
        source_id=child.id,
        file_name=member_name,
        data=member_data,
    )
    child.file_path = relative_path
    session.add(child)
    session.flush()
    return child


def _prepare_archive(session: Session, source: AgentSource, data: bytes) -> list[UUID]:
    meta = dict(source.metadata_json or {})
    meta["is_archive"] = True
    existing = session.exec(
        select(AgentSource).where(AgentSource.parent_source_id == source.id)
    ).all()
    child_ids_to_index: list[UUID] = []

    if existing:
        source.content_text = _listing_text(source.title or source.file_name or "archive", existing)
        source.metadata_json = meta
        session.add(source)
        session.flush()
        return []

    members = _iter_archive_members(source.file_name, data)
    children: list[AgentSource] = []
    for member_name, payload in members:
        content_type = guess_content_type(member_name)
        child = _create_child_source(
            session,
            parent=source,
            file_name=member_name,
            content_type=content_type,
            data=payload,
        )
        children.append(child)
        child_ids_to_index.append(child.id)

    source.content_text = _listing_text(
        source.title or source.file_name or "archive", children
    )
    meta["archive_file_count"] = len(children)
    source.metadata_json = meta
    session.add(source)
    session.flush()
    return child_ids_to_index


def _pick_vision_model(session: Session, agent: Agent) -> ChatModel | None:
    enabled_ids = list(
        session.exec(
            select(OrgModel.model_id).where(
                OrgModel.org_id == agent.org_id,
                OrgModel.is_enabled.is_(True),
            )
        ).all()
    )
    if not enabled_ids:
        return None

    candidates: list[ChatModel] = []
    if agent.preferred_model_id and agent.preferred_model_id in enabled_ids:
        preferred = session.get(ChatModel, agent.preferred_model_id)
        if preferred and preferred.is_active:
            candidates.append(preferred)

    others = session.exec(
        select(ChatModel).where(
            ChatModel.id.in_(enabled_ids),
            ChatModel.is_active.is_(True),
        )
    ).all()
    for model in others:
        if agent.preferred_model_id and model.id == agent.preferred_model_id:
            continue
        candidates.append(model)

    for model in candidates:
        model = ensure_model_capabilities(session, model)
        if supports_image_input(model) and model.supports_image_output is not True:
            return model
    for model in candidates:
        model = ensure_model_capabilities(session, model)
        if supports_image_input(model):
            return model
    return None


async def _describe_image_async(
    session: Session,
    *,
    agent: Agent,
    file_name: str | None,
    content_type: str | None,
    data: bytes,
) -> str:
    model = _pick_vision_model(session, agent)
    if not model:
        return (
            f"Image file: {file_name or 'image'}. "
            "No vision-capable model is enabled for this organization, "
            "so text could not be extracted."
        )

    provider_config = require_provider_enabled(session, agent.org_id, model.provider)
    provider = get_provider_for_org(
        session,
        agent.org_id,
        model.provider,
        org_config=provider_config,
        prefer_responses_api=model.uses_responses_api is True,
        openrouter_endpoint=model.openrouter_endpoint,
        prompt_cache_enabled=False,
    )
    mime = (content_type or "image/png").split(";")[0].strip() or "image/png"
    encoded = base64.b64encode(data).decode("ascii")
    messages = [
        {
            "role": "system",
            "content": "You help index images for search. Be concise and factual.",
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": _IMAGE_DESCRIBE_PROMPT},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{encoded}"},
                },
            ],
        },
    ]
    response = await provider.chat(model.model_name, messages)
    usage = getattr(response, "usage", None)
    if usage:
        session.add(
            UsageEvent(
                org_id=agent.org_id,
                user_id=agent.owner_user_id,
                model_id=model.id,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                total_tokens=usage.total_tokens,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cached_tokens=usage.cached_tokens,
                thinking_tokens=usage.thinking_tokens,
            )
        )
        session.flush()
    text = (response.content or "").strip()
    if not text:
        return f"Image file: {file_name or 'image'}."
    return text[:_IMAGE_TEXT_MAX_CHARS]


def _prepare_image(session: Session, source: AgentSource, data: bytes) -> None:
    agent = session.get(Agent, source.agent_id)
    if not agent:
        raise ValueError("Project not found for image source")

    file_name = source.file_name or source.title or "image"
    content_type = source.content_type or "image/png"
    file_name, content_type, data = normalize_image_attachment(
        file_name, content_type, data
    )
    if file_name != source.file_name or content_type != source.content_type:
        source.file_name = file_name
        source.content_type = content_type
        relative_path, _size = write_agent_source_file(
            agent_id=source.agent_id,
            source_id=source.id,
            file_name=file_name,
            data=data,
        )
        if source.file_path and source.file_path != relative_path:
            delete_file(source.file_path)
        source.file_path = relative_path

    description = _run_coro(
        _describe_image_async(
            session,
            agent=agent,
            file_name=file_name,
            content_type=content_type,
            data=data,
        )
    )
    meta = dict(source.metadata_json or {})
    meta["is_image"] = True
    meta["image_described"] = True
    source.content_text = description
    source.metadata_json = meta
    session.add(source)
    session.flush()


def prepare_source_content(session: Session, source: AgentSource) -> list[UUID]:
    """Mutate source.content_text (and children) before embedding.

    Returns child source ids that should be enqueued for indexing (archives).
    """
    if source.kind == AgentSourceKind.cloud:
        from app.services.agents.cloud_sources import prepare_cloud_source_content

        return prepare_cloud_source_content(session, source)

    meta = dict(source.metadata_json or {})
    data = maybe_read_file_bytes(source.file_path)

    is_archive = bool(meta.get("is_archive")) or is_archive_file(
        file_name=source.file_name, content_type=source.content_type
    )
    is_image = bool(meta.get("is_image")) or is_image_file(
        file_name=source.file_name, content_type=source.content_type
    )

    if is_archive:
        if not data:
            raise ValueError("Archive file data is missing")
        return _prepare_archive(session, source, data)

    if is_image:
        if not data:
            raise ValueError("Image file data is missing")
        _prepare_image(session, source, data)
        return []

    if not (source.content_text or "").strip() and data and source.kind == AgentSourceKind.file:
        source.content_text = extract_text_from_file(
            file_name=source.file_name,
            content_type=source.content_type,
            data=data,
        )
        session.add(source)
        session.flush()
    return []
