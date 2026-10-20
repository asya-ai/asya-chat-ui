"""Embedded Google MCP: auth gate + toolset filtering."""

from __future__ import annotations

import pytest

from app.services.integrations.google_mcp_embed import (
    _BearerToolsetMiddleware,
    _build_server,
    _parse_toolsets,
    _toolsets,
    is_embedded_google_mcp_url,
)


def test_is_embedded_google_mcp_url() -> None:
    assert is_embedded_google_mcp_url("http://127.0.0.1:8000/internal/google-mcp/mcp")
    assert is_embedded_google_mcp_url("http://backend:8000/internal/google-mcp/mcp")
    assert not is_embedded_google_mcp_url("https://example.com/mcp")


def test_parse_toolsets() -> None:
    assert _parse_toolsets("") is None
    assert _parse_toolsets("all") is None
    assert _parse_toolsets("gmail,drive") == frozenset({"gmail", "drive"})
    assert _parse_toolsets("docs,sheets,slides,chat") == frozenset(
        {"docs", "sheets", "slides", "chat"}
    )
    assert _parse_toolsets("unknown") == frozenset()


@pytest.mark.asyncio
async def test_list_tools_respects_toolsets() -> None:
    server = _build_server()
    token = _toolsets.set(frozenset({"gmail"}))
    try:
        tools = await server.list_tools()
        names = {t.name for t in tools}
        assert names == {"gmail_list_messages", "gmail_get_message"}
        assert "drive_list_files" not in names
    finally:
        _toolsets.reset(token)


@pytest.mark.asyncio
async def test_list_tools_docs_sheets_slides_chat() -> None:
    server = _build_server()
    token = _toolsets.set(frozenset({"docs", "sheets", "slides", "chat"}))
    try:
        tools = await server.list_tools()
        names = {t.name for t in tools}
        assert names == {
            "docs_list_documents",
            "docs_get_document",
            "sheets_list_spreadsheets",
            "sheets_get_values",
            "slides_list_presentations",
            "slides_get_presentation",
            "chat_list_spaces",
            "chat_list_messages",
        }
    finally:
        _toolsets.reset(token)


@pytest.mark.asyncio
async def test_bearer_middleware_rejects_missing_token() -> None:
    called = False

    async def inner(scope, receive, send):
        nonlocal called
        called = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    app = _BearerToolsetMiddleware(inner)
    messages: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/mcp",
        "raw_path": b"/mcp",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 123),
        "server": ("127.0.0.1", 8000),
    }
    await app(scope, receive, send)
    assert not called
    start = next(m for m in messages if m["type"] == "http.response.start")
    assert start["status"] == 401
