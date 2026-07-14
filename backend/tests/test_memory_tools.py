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
    assert "chats.id !=" in str(compiled)
    assert current_chat_id in compiled.params.values()
