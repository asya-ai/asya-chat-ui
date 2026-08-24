from __future__ import annotations

import math
import re
import threading
from datetime import datetime
from typing import Any, Iterable
from uuid import UUID

import numpy as np
from sqlalchemy import delete, func, select
from sqlmodel import Session

from app.core.config import settings
from app.models import AgentChunk, AgentEmbedding, AgentSource, AgentSourceKind, AgentSourceStatus
from app.services.agents.chat_index import chat_source_visible_to_user
from app.services.agents.onnx_embedder import load_onnx_embedder

_embedding_model: Any | None = None
_embedding_model_lock = threading.Lock()


def _get_embedding_model():
    global _embedding_model
    if _embedding_model is not None:
        return _embedding_model
    with _embedding_model_lock:
        if _embedding_model is not None:
            return _embedding_model
        try:
            _embedding_model = load_onnx_embedder(settings.agent_embedding_model)
        except Exception:
            return None
    return _embedding_model


def _encode_documents(texts: list[str]) -> np.ndarray | None:
    model = _get_embedding_model()
    if model is None or not texts:
        return None
    return np.asarray(
        model.embed(texts, batch_size=settings.agent_embedding_batch_size),
        dtype=np.float32,
    )


def _encode_query(text: str) -> np.ndarray | None:
    model = _get_embedding_model()
    if model is None:
        return None
    return np.asarray(model.embed([text])[0], dtype=np.float32)


def chunk_text(content: str, chunk_size: int = 1200, overlap: int = 200) -> list[str]:
    normalized = re.sub(r"\s+", " ", content).strip()
    if not normalized:
        return []
    if len(normalized) <= chunk_size:
        return [normalized]

    chunks: list[str] = []
    step = max(1, chunk_size - overlap)
    start = 0
    while start < len(normalized):
        end = min(len(normalized), start + chunk_size)
        chunks.append(normalized[start:end].strip())
        if end >= len(normalized):
            break
        start += step
    return [chunk for chunk in chunks if chunk]


def estimate_tokens(text: str) -> int:
    # Rough estimate used for chunk metadata and UI hints.
    return max(1, math.ceil(len(text) / 4))


def summarize_source_text(text: str, max_len: int = 320) -> str | None:
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return None
    # Prefer the first few sentence-like spans for a quick preview.
    sentences = re.split(r"(?<=[.!?])\s+", normalized)
    preview = " ".join(sentences[:3]).strip()
    if not preview:
        preview = normalized
    if len(preview) <= max_len:
        return preview
    return preview[: max_len - 1].rstrip() + "..."


def reindex_source(session: Session, source: AgentSource) -> tuple[int, str | None]:
    try:
        source.status = AgentSourceStatus.indexing
        source.error_message = None
        source.updated_at = datetime.utcnow()
        session.add(source)
        session.flush()

        existing_chunk_ids = session.exec(
            select(AgentChunk.id).where(AgentChunk.source_id == source.id)
        ).all()
        existing_chunk_ids = [
            item if isinstance(item, UUID) else item[0]
            for item in existing_chunk_ids
        ]
        if existing_chunk_ids:
            session.exec(delete(AgentEmbedding).where(AgentEmbedding.chunk_id.in_(existing_chunk_ids)))
        session.exec(delete(AgentChunk).where(AgentChunk.source_id == source.id))

        pieces = chunk_text(source.content_text)
        created_chunks: list[AgentChunk] = []
        for idx, piece in enumerate(pieces):
            chunk = AgentChunk(
                agent_id=source.agent_id,
                source_id=source.id,
                chunk_index=idx,
                content=piece,
                token_count_estimate=estimate_tokens(piece),
            )
            session.add(chunk)
            created_chunks.append(chunk)
        session.flush()

        dense_vectors = _encode_documents([chunk.content for chunk in created_chunks])
        if dense_vectors is not None:
            for chunk, vector in zip(created_chunks, dense_vectors):
                session.add(
                    AgentEmbedding(
                        chunk_id=chunk.id,
                        model_name=settings.agent_embedding_model,
                        embedding=vector.astype(np.float32).tolist(),
                    )
                )
        metadata = dict(source.metadata_json or {})
        metadata["summary"] = summarize_source_text(source.content_text)
        source.metadata_json = metadata
        source.status = AgentSourceStatus.ready
        source.updated_at = datetime.utcnow()
        session.add(source)
        session.flush()
        return len(pieces), None
    except Exception as exc:
        source.status = AgentSourceStatus.failed
        source.error_message = str(exc)[:500]
        source.updated_at = datetime.utcnow()
        session.add(source)
        session.flush()
        return 0, str(exc)


def _score_dense_rows(
    rows: Iterable[tuple[AgentChunk, AgentSource, list[float] | None]],
    query_vector: np.ndarray,
) -> list[tuple[AgentChunk, AgentSource, float]]:
    scored: list[tuple[AgentChunk, AgentSource, float]] = []
    for chunk, source, embedding in rows:
        if not isinstance(embedding, list) or not embedding:
            continue
        chunk_vec = np.asarray(embedding, dtype=np.float32)
        if chunk_vec.shape != query_vector.shape:
            continue
        scored.append((chunk, source, float(np.dot(query_vector, chunk_vec))))
    scored.sort(key=lambda item: item[2], reverse=True)
    return scored


def _lexical_candidate_ids(
    session: Session,
    *,
    agent_id: UUID,
    query: str,
    limit: int,
) -> list[UUID]:
    try:
        rank = func.ts_rank_cd(
            func.to_tsvector("simple", AgentChunk.content),
            func.websearch_to_tsquery("simple", query),
        )
        rows = session.exec(
            select(AgentChunk.id, rank)
            .join(AgentSource, AgentSource.id == AgentChunk.source_id)
            .where(
                AgentChunk.agent_id == agent_id,
                AgentSource.status == AgentSourceStatus.ready,
                func.to_tsvector("simple", AgentChunk.content).op("@@")(
                    func.websearch_to_tsquery("simple", query)
                ),
            )
            .order_by(rank.desc(), AgentChunk.chunk_index.asc())
            .limit(limit)
        ).all()
        return [row[0] if not isinstance(row, UUID) else row for row in rows]
    except Exception:
        return []


def _lexical_search(
    session: Session,
    *,
    agent_id: UUID,
    query: str,
    limit: int,
) -> list[tuple[AgentChunk, AgentSource, float]]:
    try:
        rank = func.ts_rank_cd(
            func.to_tsvector("simple", AgentChunk.content),
            func.websearch_to_tsquery("simple", query),
        )
        rows: Iterable[tuple[AgentChunk, AgentSource, float]] = session.exec(
            select(AgentChunk, AgentSource, rank)
            .join(AgentSource, AgentSource.id == AgentChunk.source_id)
            .where(AgentChunk.agent_id == agent_id, AgentSource.status == AgentSourceStatus.ready)
            .order_by(rank.desc(), AgentChunk.chunk_index.asc())
            .limit(limit)
        ).all()
        return list(rows)
    except Exception:
        return []


def _filter_visible_matches(
    matches: list[tuple[AgentChunk, AgentSource, float]],
    viewer_user_id: UUID | None,
    *,
    include_chats: bool = True,
) -> list[tuple[AgentChunk, AgentSource, float]]:
    filtered: list[tuple[AgentChunk, AgentSource, float]] = []
    for chunk, source, score in matches:
        if not include_chats and source.kind == AgentSourceKind.chat:
            continue
        if not chat_source_visible_to_user(source, viewer_user_id):
            continue
        filtered.append((chunk, source, score))
    return filtered


def search_agent_chunks(
    session: Session,
    *,
    agent_id: UUID,
    query: str,
    limit: int = 6,
    viewer_user_id: UUID | None = None,
    include_chats: bool = True,
) -> list[tuple[AgentChunk, AgentSource, float]]:
    cleaned = query.strip()
    if not cleaned:
        return []

    capped_limit = max(1, min(int(limit or 6), 20))
    # Over-fetch slightly so chat-privacy filtering still fills the limit.
    fetch_limit = min(40, capped_limit * 3)
    full_scan_max = max(1, int(settings.agent_embedding_full_scan_max or 400))
    candidate_limit = max(fetch_limit, int(settings.agent_embedding_candidate_limit or 96))

    source_filters = [
        AgentChunk.agent_id == agent_id,
        AgentSource.status == AgentSourceStatus.ready,
    ]
    if not include_chats:
        source_filters.append(AgentSource.kind != AgentSourceKind.chat)

    query_vector = _encode_query(cleaned)
    if query_vector is not None:
        embedding_count = session.exec(
            select(func.count())
            .select_from(AgentEmbedding)
            .join(AgentChunk, AgentChunk.id == AgentEmbedding.chunk_id)
            .join(AgentSource, AgentSource.id == AgentChunk.source_id)
            .where(
                *source_filters,
                AgentEmbedding.model_name == settings.agent_embedding_model,
            )
        ).one()
        # Joined count queries can return a Row instead of a bare int.
        if not isinstance(embedding_count, (int, float)):
            embedding_count = embedding_count[0]
        embedding_count = int(embedding_count or 0)

        dense_query = (
            select(AgentChunk, AgentSource, AgentEmbedding.embedding)
            .join(AgentSource, AgentSource.id == AgentChunk.source_id)
            .join(AgentEmbedding, AgentEmbedding.chunk_id == AgentChunk.id)
            .where(
                *source_filters,
                AgentEmbedding.model_name == settings.agent_embedding_model,
            )
        )

        if embedding_count > full_scan_max:
            candidate_ids = _lexical_candidate_ids(
                session, agent_id=agent_id, query=cleaned, limit=candidate_limit
            )
            if candidate_ids:
                dense_query = dense_query.where(AgentChunk.id.in_(candidate_ids))
            else:
                # No lexical hits: score a recent capped window instead of the full corpus.
                recent_ids = session.exec(
                    select(AgentChunk.id)
                    .join(AgentSource, AgentSource.id == AgentChunk.source_id)
                    .where(*source_filters)
                    .order_by(AgentChunk.created_at.desc())
                    .limit(candidate_limit)
                ).all()
                recent_ids = [
                    item if isinstance(item, UUID) else item[0] for item in recent_ids
                ]
                if not recent_ids:
                    return []
                dense_query = dense_query.where(AgentChunk.id.in_(recent_ids))

        rows = session.exec(dense_query).all()
        scored_dense = _filter_visible_matches(
            _score_dense_rows(rows, query_vector),
            viewer_user_id,
            include_chats=include_chats,
        )
        if scored_dense:
            return scored_dense[:capped_limit]

    lexical = _filter_visible_matches(
        _lexical_search(session, agent_id=agent_id, query=cleaned, limit=fetch_limit),
        viewer_user_id,
        include_chats=include_chats,
    )
    if lexical:
        return lexical[:capped_limit]

    chunks = session.exec(
        select(AgentChunk, AgentSource)
        .join(AgentSource, AgentSource.id == AgentChunk.source_id)
        .where(*source_filters)
        .order_by(AgentChunk.created_at.desc())
        .limit(max(capped_limit * 8, 20))
    ).all()
    terms = {token for token in re.split(r"\W+", cleaned.lower()) if token}
    scored: list[tuple[AgentChunk, AgentSource, float]] = []
    for chunk, source in chunks:
        if not include_chats and source.kind == AgentSourceKind.chat:
            continue
        if not chat_source_visible_to_user(source, viewer_user_id):
            continue
        body = chunk.content.lower()
        score = float(sum(body.count(term) for term in terms))
        if score > 0:
            scored.append((chunk, source, score))
    scored.sort(key=lambda item: item[2], reverse=True)
    return scored[:capped_limit]
