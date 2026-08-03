from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.services.tools.memory_tools import MemoryToolContext, search_past_chats


class _EmptyResult:
    def all(self) -> list[object]:
        return []


class _RecordingSession:
    def __init__(self) -> None:
        self.statement = None

    def exec(self, statement):  # type: ignore[no-untyped-def]
        self.statement = statement
        return _EmptyResult()


@pytest.mark.asyncio
async def test_search_past_chats_excludes_the_active_conversation():
    session = _RecordingSession()
    current_chat_id = uuid4()

    result = await search_past_chats(
        MemoryToolContext(
            session=session,  # type: ignore[arg-type]
            user_id=uuid4(),
            current_chat_id=current_chat_id,
        ),
        query="project details",
    )

    assert result.output["results"] == []
    assert session.statement is not None
    compiled = session.statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "chats.id !=" in sql
    assert current_chat_id in compiled.params.values()
    assert "chats.last_activity_at" in sql
    assert "ORDER BY" in sql.upper()
    # Most recently active chats first (not creation time).
    order_idx = sql.upper().index("ORDER BY")
    order_clause = sql[order_idx:].upper()
    assert "LAST_ACTIVITY_AT" in order_clause
    assert order_clause.index("LAST_ACTIVITY_AT") < order_clause.index("DESC")
