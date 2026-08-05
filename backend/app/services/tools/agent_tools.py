from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from sqlmodel import Session, select

from app.models.entities import AgentSource, AgentSourceStatus
from app.services.agents.runtime import search_agent_chunks
from app.services.tools.code_execution import project_source_exec_path
from app.services.tools.registry import ToolResult

logger = logging.getLogger(__name__)

READ_MAX_CHARS = 8000


@dataclass
class AgentToolContext:
    session: Session
    agent_id: UUID

    def _all_sources(self) -> list[AgentSource]:
        """All sources for the project ordered by creation.

        Numbering is based on this stable order (any status), so a source that
        finishes indexing between a list and a read never shifts another
        source's number. Deleting a source can create a gap, which is fine.
        """
        return list(
            self.session.exec(
                select(AgentSource)
                .where(AgentSource.agent_id == self.agent_id)
                .order_by(AgentSource.created_at)
            ).all()
        )

    def number_for(self, source_id: UUID) -> int | None:
        for idx, source in enumerate(self._all_sources(), start=1):
            if source.id == source_id:
                return idx
        return None

    def resolve(self, ref: str | int) -> AgentSource | None:
        """Resolve a source by its numeric id (1-based) or UUID string."""
        ref_str = str(ref).strip()
        if ref_str.isdigit():
            sources = self._all_sources()
            index = int(ref_str)
            if 1 <= index <= len(sources):
                return sources[index - 1]
            return None
        try:
            sid = UUID(ref_str)
        except (ValueError, TypeError):
            return None
        source = self.session.get(AgentSource, sid)
        if source and source.agent_id == self.agent_id:
            return source
        return None


def _source_summary(source: AgentSource) -> str | None:
    metadata = source.metadata_json or {}
    summary = metadata.get("summary") if isinstance(metadata, dict) else None
    return summary or None


def _status_str(source: AgentSource) -> str:
    status = source.status
    return status.value if hasattr(status, "value") else str(status)


async def list_project_sources(context: AgentToolContext) -> ToolResult:
    sources = context._all_sources()
    results = [
        {
            "id": idx,
            "title": source.title,
            "kind": source.kind.value if hasattr(source.kind, "value") else str(source.kind),
            "url": source.url,
            "status": _status_str(source),
            "readable": source.status == AgentSourceStatus.ready,
            "summary": _source_summary(source),
            "length_chars": len(source.content_text or ""),
            "exec_path": (
                project_source_exec_path(source)
                if source.status == AgentSourceStatus.ready
                else None
            ),
        }
        for idx, source in enumerate(sources, start=1)
    ]
    if not results:
        return ToolResult(
            name="list_project_sources",
            output={"sources": [], "message": "This project has no indexed sources yet."},
        )
    return ToolResult(name="list_project_sources", output={"sources": results})


async def search_project_sources(
    context: AgentToolContext, *, query: str, limit: int = 8
) -> ToolResult:
    query = (query or "").strip()
    if not query:
        return ToolResult(
            name="search_project_sources", output={"error": "A search query is required."}
        )
    capped_limit = max(1, min(int(limit or 8), 20))
    matches = search_agent_chunks(
        context.session, agent_id=context.agent_id, query=query, limit=capped_limit
    )
    results = [
        {
            "id": context.number_for(source.id),
            "title": source.title,
            "chunk_index": chunk.chunk_index,
            "score": round(float(score), 4),
            "snippet": chunk.content[:600],
        }
        for chunk, source, score in matches
    ]
    if not results:
        return ToolResult(
            name="search_project_sources",
            output={
                "results": [],
                "message": (
                    "No matching passages found. Try a different query or use "
                    "read_project_source to review a document in full."
                ),
            },
        )
    return ToolResult(name="search_project_sources", output={"results": results})


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
            output={
                "error": (
                    f"No source with id {source_id} in this project. Call list_project_sources "
                    "to see the available numeric ids."
                )
            },
        )
    if source.status != AgentSourceStatus.ready:
        return ToolResult(
            name="read_project_source",
            output={
                "id": context.number_for(source.id),
                "title": source.title,
                "status": _status_str(source),
                "error": (
                    f"Source {source_id} (\"{source.title}\") is not ready to read yet "
                    f"(status: {_status_str(source)}). Try again shortly or use another source."
                ),
            },
        )

    text = source.content_text or ""
    total = len(text)
    try:
        start = max(0, int(offset or 0))
    except (ValueError, TypeError):
        start = 0
    try:
        window = max(1, min(int(max_chars or READ_MAX_CHARS), READ_MAX_CHARS))
    except (ValueError, TypeError):
        window = READ_MAX_CHARS
    end = min(total, start + window)
    excerpt = text[start:end]
    next_offset = end if end < total else None

    return ToolResult(
        name="read_project_source",
        output={
            "id": context.number_for(source.id),
            "title": source.title,
            "total_length_chars": total,
            "offset": start,
            "returned_chars": len(excerpt),
            "has_more": next_offset is not None,
            "next_offset": next_offset,
            "content": excerpt,
        },
    )
