from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from uuid import UUID

from sqlmodel import Session, select

from app.models.entities import AgentSource, AgentSourceKind, AgentSourceStatus
from app.services.agents.chat_index import chat_source_visible_to_user
from app.services.agents.runtime import search_agent_chunks
from app.services.agents.source_media import is_archive_file, is_image_file
from app.services.file_storage import maybe_read_file_bytes
from app.services.tools.code_execution import project_source_exec_path
from app.services.tools.registry import ToolResult

logger = logging.getLogger(__name__)

READ_MAX_CHARS = 8000


@dataclass
class AgentToolContext:
    session: Session
    agent_id: UUID
    user_id: UUID | None = None

    def _all_sources(self, *, include_chats: bool = True) -> list[AgentSource]:
        """Visible sources for the project ordered by creation.

        Includes uploaded/url/text sources for everyone with access, plus this
        user's indexed project chats (unless include_chats is False). Numbering is
        based on this stable filtered order.
        """
        sources = list(
            self.session.exec(
                select(AgentSource)
                .where(AgentSource.agent_id == self.agent_id)
                .order_by(AgentSource.created_at)
            ).all()
        )
        visible = [
            source
            for source in sources
            if chat_source_visible_to_user(source, self.user_id)
        ]
        if include_chats:
            return visible
        return [source for source in visible if source.kind != AgentSourceKind.chat]

    def number_for(self, source_id: UUID, *, include_chats: bool = True) -> int | None:
        for idx, source in enumerate(self._all_sources(include_chats=include_chats), start=1):
            if source.id == source_id:
                return idx
        return None

    def resolve(self, ref: str | int, *, include_chats: bool = True) -> AgentSource | None:
        """Resolve a source by its numeric id (1-based) or UUID string."""
        ref_str = str(ref).strip()
        if ref_str.isdigit():
            sources = self._all_sources(include_chats=include_chats)
            index = int(ref_str)
            if 1 <= index <= len(sources):
                return sources[index - 1]
            return None
        try:
            sid = UUID(ref_str)
        except (ValueError, TypeError):
            return None
        source = self.session.get(AgentSource, sid)
        if (
            source
            and source.agent_id == self.agent_id
            and chat_source_visible_to_user(source, self.user_id)
            and (include_chats or source.kind != AgentSourceKind.chat)
        ):
            return source
        return None


def _source_summary(source: AgentSource) -> str | None:
    metadata = source.metadata_json or {}
    summary = metadata.get("summary") if isinstance(metadata, dict) else None
    return summary or None


def _status_str(source: AgentSource) -> str:
    status = source.status
    return status.value if hasattr(status, "value") else str(status)


def _coerce_bool(value: object, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "y", "on"}:
            return True
        if lowered in {"0", "false", "no", "n", "off"}:
            return False
    return default


async def list_project_sources(
    context: AgentToolContext, *, include_chats: bool = True
) -> ToolResult:
    from app.services.agents.cloud_sources import maybe_sync_agent_cloud_roots

    maybe_sync_agent_cloud_roots(context.session, context.agent_id, force=False)
    sources = context._all_sources(include_chats=include_chats)
    results = []
    for idx, source in enumerate(sources, start=1):
        meta = source.metadata_json if isinstance(source.metadata_json, dict) else {}
        is_image = bool(meta.get("is_image")) or is_image_file(
            file_name=source.file_name, content_type=source.content_type
        )
        is_archive = bool(meta.get("is_archive")) or is_archive_file(
            file_name=source.file_name, content_type=source.content_type
        )
        results.append(
            {
                "id": idx,
                "title": source.title,
                "kind": source.kind.value if hasattr(source.kind, "value") else str(source.kind),
                "url": source.url if source.kind != AgentSourceKind.chat else None,
                "chat_id": (
                    meta.get("chat_id")
                    if source.kind == AgentSourceKind.chat
                    else None
                ),
                "file_name": source.file_name,
                "parent_id": (
                    context.number_for(source.parent_source_id)
                    if source.parent_source_id
                    else None
                ),
                "is_image": is_image,
                "is_archive": is_archive,
                "cloud_provider": (
                    meta.get("provider") if source.kind == AgentSourceKind.cloud else None
                ),
                "cloud_remote_kind": (
                    meta.get("remote_kind") if source.kind == AgentSourceKind.cloud else None
                ),
                "status": _status_str(source),
                "readable": source.status == AgentSourceStatus.ready,
                "summary": _source_summary(source),
                "length_chars": len(source.content_text or ""),
                "exec_path": (
                    project_source_exec_path(source)
                    if source.status == AgentSourceStatus.ready
                    and source.kind != AgentSourceKind.chat
                    else None
                ),
                "presentation_ref": (
                    source.file_name
                    if is_image and source.status == AgentSourceStatus.ready
                    else None
                ),
                "note": (
                    "Live cloud file — edit via Drive/OneDrive tools; index refreshes on sync."
                    if source.kind == AgentSourceKind.cloud
                    else None
                ),
            }
        )
    if not results:
        scope = (
            "uploaded files/URLs/cloud links"
            if not include_chats
            else "files, URLs, cloud links, or prior chats"
        )
        return ToolResult(
            name="list_project_sources",
            output={
                "sources": [],
                "include_chats": include_chats,
                "message": f"This project has no indexed sources yet ({scope}).",
            },
        )
    return ToolResult(
        name="list_project_sources",
        output={"sources": results, "include_chats": include_chats},
    )


async def search_project_sources(
    context: AgentToolContext,
    *,
    query: str,
    limit: int = 8,
    include_chats: bool = True,
) -> ToolResult:
    query = (query or "").strip()
    if not query:
        return ToolResult(
            name="search_project_sources", output={"error": "A search query is required."}
        )
    capped_limit = max(1, min(int(limit or 8), 20))
    matches = search_agent_chunks(
        context.session,
        agent_id=context.agent_id,
        query=query,
        limit=capped_limit,
        viewer_user_id=context.user_id,
        include_chats=include_chats,
    )
    results = [
        {
            "id": context.number_for(source.id, include_chats=include_chats),
            "title": source.title,
            "kind": source.kind.value if hasattr(source.kind, "value") else str(source.kind),
            "chunk_index": chunk.chunk_index,
            "score": round(float(score), 4),
            "snippet": chunk.content[:600],
            "chat_id": (
                (source.metadata_json or {}).get("chat_id")
                if source.kind == AgentSourceKind.chat
                and isinstance(source.metadata_json, dict)
                else None
            ),
        }
        for chunk, source, score in matches
    ]
    if not results:
        scope = "project files" if not include_chats else "project files or indexed chats"
        return ToolResult(
            name="search_project_sources",
            output={
                "results": [],
                "include_chats": include_chats,
                "message": (
                    f"No matching passages found in {scope}. "
                    "Try a different query, use search_past_chats for chat titles, "
                    "or read_project_source for a specific document."
                ),
            },
        )
    return ToolResult(
        name="search_project_sources",
        output={"results": results, "include_chats": include_chats},
    )

async def read_project_source(
    context: AgentToolContext,
    *,
    source_id: str | int,
    offset: int = 0,
    max_chars: int = READ_MAX_CHARS,
) -> ToolResult:
    if source_id is None or str(source_id).strip() == "":
        return ToolResult(
            name="read_project_source",
            output={"error": "A source id is required (use the numeric id from the source list)."},
        )
    source = context.resolve(source_id)
    if not source:
        return ToolResult(
            name="read_project_source",
            output={"error": f"Source {source_id!r} was not found in this project."},
        )
    if source.status != AgentSourceStatus.ready:
        return ToolResult(
            name="read_project_source",
            output={
                "error": (
                    f"Source {context.number_for(source.id)} is not ready yet "
                    f"(status={_status_str(source)})."
                )
            },
        )

    meta = source.metadata_json if isinstance(source.metadata_json, dict) else {}
    is_image = bool(meta.get("is_image")) or is_image_file(
        file_name=source.file_name, content_type=source.content_type
    )
    text = source.content_text or ""
    start = max(0, int(offset or 0))
    cap = max(1, min(int(max_chars or READ_MAX_CHARS), READ_MAX_CHARS))
    slice_ = text[start : start + cap]
    next_offset = start + len(slice_)
    output: dict = {
        "id": context.number_for(source.id),
        "title": source.title,
        "kind": source.kind.value if hasattr(source.kind, "value") else str(source.kind),
        "file_name": source.file_name,
        "is_image": is_image,
        "chat_id": (
            meta.get("chat_id") if source.kind == AgentSourceKind.chat else None
        ),
        "offset": start,
        "next_offset": next_offset if next_offset < len(text) else None,
        "has_more": next_offset < len(text),
        "total_length_chars": len(text),
        "content": slice_,
    }

    attachments: list[dict] | None = None
    if is_image and start == 0:
        data = maybe_read_file_bytes(source.file_path)
        if data:
            content_type = (
                (source.content_type or "image/png").split(";")[0].strip() or "image/png"
            )
            attachments = [
                {
                    "file_name": source.file_name or source.title or "image",
                    "content_type": content_type,
                    "data_base64": base64.b64encode(data).decode("ascii"),
                }
            ]
            output["vision_attached"] = True
            output["message"] = (
                "Indexed description is in content. The original image is attached for "
                "vision on the next turn when the chat model supports image input."
            )
        else:
            output["vision_attached"] = False
            output["message"] = (
                "Image file bytes are missing; only the indexed description is available."
            )

    return ToolResult(
        name="read_project_source",
        output=output,
        attachments=attachments,
    )
