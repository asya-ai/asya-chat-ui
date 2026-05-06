from __future__ import annotations

from uuid import UUID

from sqlmodel import Session

from app.services.agents.runtime import search_agent_chunks


def retrieve_agent_chunks(
    session: Session,
    *,
    agent_id: UUID,
    query: str,
    limit: int = 6,
):
    return search_agent_chunks(session, agent_id=agent_id, query=query, limit=limit)
