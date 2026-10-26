from uuid import uuid4
from unittest.mock import MagicMock

import pytest

from app.models.entities import AgentSource, AgentSourceKind, AgentSourceStatus
from app.services.tools.agent_tools import AgentToolContext, propose_project_source


def _ctx(sources: list[AgentSource] | None = None) -> AgentToolContext:
    agent_id = uuid4()
    session = MagicMock()
    exec_result = MagicMock()
    exec_result.all.return_value = sources or []
    session.exec.return_value = exec_result
    return AgentToolContext(session=session, agent_id=agent_id, user_id=uuid4())


@pytest.mark.asyncio
async def test_propose_url_requires_http():
    result = await propose_project_source(_ctx(), kind="url", url="ftp://example.com")
    assert result.output["status"] == "error"


@pytest.mark.asyncio
async def test_propose_url_success():
    result = await propose_project_source(
        _ctx(),
        kind="url",
        url="https://example.com/docs",
        title="Docs",
        reason="Useful reference",
    )
    assert result.output["status"] == "proposed"
    assert result.output["kind"] == "url"
    assert result.output["url"] == "https://example.com/docs"
    assert result.output["title"] == "Docs"
    assert result.output["reason"] == "Useful reference"
    assert result.output["agent_id"]


@pytest.mark.asyncio
async def test_propose_text_success():
    result = await propose_project_source(
        _ctx(),
        kind="text",
        content_text="Decision: ship on Friday.",
        title="Ship note",
    )
    assert result.output["status"] == "proposed"
    assert result.output["kind"] == "text"
    assert result.output["content_text"] == "Decision: ship on Friday."


@pytest.mark.asyncio
async def test_propose_dedupes_existing_url():
    existing = AgentSource(
        id=uuid4(),
        agent_id=uuid4(),
        kind=AgentSourceKind.url,
        title="Docs",
        url="https://example.com/docs/",
        content_text="...",
        status=AgentSourceStatus.ready,
    )
    ctx = _ctx([existing])
    # Rebind agent_id so visibility/filter matches.
    existing.agent_id = ctx.agent_id
    result = await propose_project_source(
        ctx, kind="url", url="https://example.com/docs"
    )
    assert result.output["status"] == "already_indexed"


@pytest.mark.asyncio
async def test_propose_rejects_unknown_kind():
    result = await propose_project_source(_ctx(), kind="file")
    assert result.output["status"] == "error"
