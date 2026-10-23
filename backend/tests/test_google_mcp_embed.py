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
        assert "gmail_list_messages" in names
        assert "gmail_send_message" in names
        assert "gmail_reply_message" in names
        assert "drive_list_files" not in names
    finally:
        _toolsets.reset(token)


@pytest.mark.asyncio
async def test_write_tools_present_per_toolset() -> None:
    server = _build_server()
    expected = {
        "gmail": {
            "gmail_send_message",
            "gmail_create_draft",
            "gmail_reply_message",
            "gmail_trash_message",
            "gmail_modify_labels",
        },
        "drive": {
            "drive_create_folder",
            "drive_move_file",
            "drive_trash_file",
            "drive_copy_file",
            "drive_export_file",
        },
        "calendar": {
            "calendar_create_event",
            "calendar_update_event",
            "calendar_delete_event",
        },
        "meet": {"meet_create_event"},
        "tasks": {
            "tasks_create_task",
            "tasks_complete_task",
            "tasks_delete_task",
        },
        "docs": {
            "docs_replace_text",
            "docs_insert_text",
            "docs_append_text",
            "docs_create_document",
        },
        "sheets": {
            "sheets_update_values",
            "sheets_append_values",
            "sheets_clear_values",
            "sheets_add_sheet",
            "sheets_delete_sheet",
            "sheets_rename_sheet",
            "sheets_delete_dimension",
            "sheets_copy_sheet",
            "sheets_create_spreadsheet",
        },
        "slides": {
            "slides_create_presentation",
            "slides_add_slide",
            "slides_delete_slide",
            "slides_replace_all_text",
            "slides_insert_text",
        },
        "chat": {
            "chat_send_message",
            "chat_create_space",
            "chat_delete_message",
        },
    }
    for toolset, must_have in expected.items():
        token = _toolsets.set(frozenset({toolset}))
        try:
            tools = await server.list_tools()
            names = {t.name for t in tools}
            missing = must_have - names
            assert not missing, f"{toolset} missing {missing}"
            for name in names:
                assert name.startswith(f"{toolset}_"), name
        finally:
            _toolsets.reset(token)


def test_google_sheets_scopes_include_write() -> None:
    from app.services.integrations.catalog import get_catalog

    sheets = get_catalog("google_sheets")
    assert sheets is not None
    assert any(s.endswith("/spreadsheets") for s in sheets.oauth_scopes)
    assert not any(s.endswith("/spreadsheets.readonly") for s in sheets.oauth_scopes)

    meet = get_catalog("google_meet")
    assert meet is not None
    assert any(s.endswith("/calendar") for s in meet.oauth_scopes)
    assert not any(s.endswith("/calendar.readonly") for s in meet.oauth_scopes)

    chat = get_catalog("google_chat")
    assert chat is not None
    assert any(s.endswith("/chat.spaces") for s in chat.oauth_scopes)
    assert not any(s.endswith("/chat.spaces.readonly") for s in chat.oauth_scopes)


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
