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
from app.models import AgentChunk, AgentEmbedding, AgentSource, AgentSourceStatus

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
            from sentence_transformers import SentenceTransformer
        except Exception:
            return None
        _embedding_model = SentenceTransformer(
            settings.agent_embedding_model,
            device=settings.agent_embedding_device,
            local_files_only=True,
        )
    return _embedding_model


def _encode_documents(texts: list[str]) -> np.ndarray | None:
    model = _get_embedding_model()
    if model is None or not texts:
        return None
    encode_kwargs = {
        "convert_to_numpy": True,
        "normalize_embeddings": True,
        "batch_size": settings.agent_embedding_batch_size,
    }
    if hasattr(model, "encode_document"):
        vectors = model.encode_document(texts, **encode_kwargs)
    else:
        vectors = model.encode(texts, **encode_kwargs)
    return np.asarray(vectors, dtype=np.float32)


def _encode_query(text: str) -> np.ndarray | None:
    model = _get_embedding_model()
    if model is None:
        return None
    encode_kwargs = {
        "convert_to_numpy": True,
        "normalize_embeddings": True,
        "batch_size": 1,
    }
    if hasattr(model, "encode_query"):
        vector = model.encode_query([text], **encode_kwargs)[0]
    else:
        vector = model.encode([text], **encode_kwargs)[0]
    return np.asarray(vector, dtype=np.float32)


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


def search_agent_chunks(
    session: Session,
    *,
    agent_id: UUID,
    query: str,
    limit: int = 6,
) -> list[tuple[AgentChunk, AgentSource, float]]:
    cleaned = query.strip()
    if not cleaned:
        return []

    query_vector = _encode_query(cleaned)
    if query_vector is not None:
        rows = session.exec(
            select(AgentChunk, AgentSource, AgentEmbedding.embedding)
            .join(AgentSource, AgentSource.id == AgentChunk.source_id)
            .join(AgentEmbedding, AgentEmbedding.chunk_id == AgentChunk.id)
            .where(
                AgentChunk.agent_id == agent_id,
                AgentSource.status == AgentSourceStatus.ready,
                AgentEmbedding.model_name == settings.agent_embedding_model,
            )
        ).all()
        if rows:
            scored_dense: list[tuple[AgentChunk, AgentSource, float]] = []
            for chunk, source, embedding in rows:
                if not isinstance(embedding, list) or not embedding:
                    continue
                chunk_vec = np.asarray(embedding, dtype=np.float32)
                if chunk_vec.shape != query_vector.shape:
                    continue
                score = float(np.dot(query_vector, chunk_vec))
                scored_dense.append((chunk, source, score))
            scored_dense.sort(key=lambda item: item[2], reverse=True)
            if scored_dense:
                return scored_dense[:limit]

    try:
        rank = func.ts_rank_cd(
            func.to_tsvector("simple", AgentChunk.content),
            func.websearch_to_tsquery("simple", cleaned),
        )
        rows: Iterable[tuple[AgentChunk, AgentSource, float]] = session.exec(
            select(AgentChunk, AgentSource, rank)
            .join(AgentSource, AgentSource.id == AgentChunk.source_id)
            .where(AgentChunk.agent_id == agent_id, AgentSource.status == AgentSourceStatus.ready)
            .order_by(rank.desc(), AgentChunk.chunk_index.asc())
            .limit(limit)
        ).all()
        materialized = list(rows)
        if materialized:
            return materialized
    except Exception:
        # Fallback for non-Postgres environments.
        pass

    chunks = session.exec(
        select(AgentChunk, AgentSource)
        .join(AgentSource, AgentSource.id == AgentChunk.source_id)
        .where(AgentChunk.agent_id == agent_id, AgentSource.status == AgentSourceStatus.ready)
        .order_by(AgentChunk.created_at.desc())
        .limit(max(limit * 8, 20))
    ).all()
    terms = {token for token in re.split(r"\W+", cleaned.lower()) if token}
    scored: list[tuple[AgentChunk, AgentSource, float]] = []
    for chunk, source in chunks:
        body = chunk.content.lower()
        score = float(sum(body.count(term) for term in terms))
        if score > 0:
            scored.append((chunk, source, score))
    scored.sort(key=lambda item: item[2], reverse=True)
    return scored[:limit]
