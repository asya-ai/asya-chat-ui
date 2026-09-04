from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.services.mcp.auth_config import validate_server_auth_payload, validate_slug
from app.services.mcp.bridge import (
    DiscoveredMcpTool,
    McpServerSnapshot,
    _register_snapshots,
    mcp_action_summary,
    mcp_guidance_for_tools,
    mcp_tool_name,
    parse_mcp_tool_name,
    set_active_mcp_snapshots,
)
from app.services.mcp.client import open_mcp_session, truncate_json_text
from app.services.mcp.types import McpServerConfig
from app.services.tools.registry import ToolRegistry
from app.services.tools.previews import tool_call_action_summary


def test_validate_slug() -> None:
    assert validate_slug("Latvia-Stats") == "latvia-stats"
    with pytest.raises(ValueError, match="__"):
        validate_slug("bad__slug")


def test_validate_server_auth_payload_bearer() -> None:
    validate_server_auth_payload("bearer", {"token": "secret"})
    with pytest.raises(ValueError):
        validate_server_auth_payload("bearer", {})


@pytest.mark.asyncio
async def test_server_connection_user_provided_uses_headers_when_present() -> None:
    from types import SimpleNamespace

    from app.services.mcp.store import test_server_connection

    row = SimpleNamespace(
        auth_type="user_provided",
        slug="demo",
        name="Demo",
        transport="http",
        is_enabled=True,
        description="",
        url="https://example.com/mcp",
        command=None,
        args=None,
        stdio_env=None,
        include_tools=True,
        include_resources=False,
        include_prompts=False,
        tool_allowlist=None,
        tool_blocklist=None,
    )
    without = await test_server_connection(row, headers={})
    assert without["status"] == "configured"
    assert without["detail"] == "Requires user connection"

    with patch(
        "app.services.mcp.store.discover_server_capabilities",
        new=AsyncMock(
            return_value={"tools": [{"name": "a"}], "resources": [], "resource_templates": [], "prompts": []}
        ),
    ) as discover:
        with_headers = await test_server_connection(
            row, headers={"Authorization": "Bearer secret"}
        )
    assert with_headers["status"] == "ok"
    assert with_headers["tools"] == 1
    discover.assert_awaited_once()


def test_mcp_tool_name_helpers() -> None:
    assert mcp_tool_name("data-lv", "search_datasets") == "data-lv__search_datasets"
    assert parse_mcp_tool_name("data-lv__search_datasets") == (
        "data-lv",
        "search_datasets",
    )
    assert parse_mcp_tool_name("web_search") is None


def test_mcp_action_summary_and_preview() -> None:
    assert (
        mcp_action_summary("data-lv__search_datasets", {"query": "buses"})
        == "Querying data-lv: search_datasets"
    )
    assert (
        tool_call_action_summary("stat-lv__query_table", {"path": "foo"})
        == "Querying stat-lv: query_table"
    )
    assert (
        tool_call_action_summary("data-lv__list_resources", {})
        == "Listing data-lv resources"
    )


def test_truncate_json_text() -> None:
    assert truncate_json_text("abcdef", max_chars=10) == "abcdef"
    out = truncate_json_text("abcdefghijklmnop", max_chars=14)
    assert out.endswith("...[truncated]")
    assert len(out) == 14


def test_compact_mcp_tool_payload_dedupes_content_and_structured() -> None:
    from app.services.mcp.client import compact_mcp_tool_payload

    entity = {
        "entity": "company",
        "key": "40203171916",
        "sources": [{"id": "register", "total": 1, "records": [{"name": "Asya"}]}],
    }
    raw = json.dumps(entity)
    out = compact_mcp_tool_payload(
        {
            "content": [{"type": "text", "text": raw}],
            "structured_content": {"result": raw},
            "is_error": False,
        }
    )
    assert out == {"is_error": False, "data": entity}
    assert "content" not in out
    assert "structured_content" not in out


def test_compact_mcp_tool_payload_keeps_non_text_blocks() -> None:
    from app.services.mcp.client import compact_mcp_tool_payload

    out = compact_mcp_tool_payload(
        {
            "content": [
                {"type": "text", "text": '{"ok": true}'},
                {"type": "image", "data": "abc"},
            ],
            "structured_content": {"ok": True},
            "is_error": False,
        }
    )
    assert out["data"] == {"ok": True}
    assert out["content"] == [{"type": "image", "data": "abc"}]


@pytest.mark.asyncio
async def test_open_mcp_session_selects_http_transport() -> None:
    server = McpServerConfig(
        id="demo",
        name="Demo",
        transport="http",
        url="https://example.com/mcp",
        headers={"Authorization": "Bearer x"},
    )

    fake_session = AsyncMock()
    fake_session.initialize = AsyncMock()

    class _SessionCM:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return fake_session

        async def __aexit__(self, *args):
            return False

    class _TransportCM:
        async def __aenter__(self):
            return (object(), object(), lambda: None)

        async def __aexit__(self, *args):
            return False

    with (
        patch(
            "app.services.mcp.client.streamablehttp_client",
            return_value=_TransportCM(),
        ) as http_mock,
        patch("app.services.mcp.client.ClientSession", _SessionCM),
    ):
        async with open_mcp_session(server) as opened:
            assert opened is fake_session
        fake_session.initialize.assert_awaited_once()
        http_mock.assert_called_once()
        assert http_mock.call_args.args[0] == "https://example.com/mcp"
        assert http_mock.call_args.kwargs["headers"] == {"Authorization": "Bearer x"}


@pytest.mark.asyncio
async def test_register_mcp_tools_from_snapshots() -> None:
    server = McpServerConfig(
        id="data-lv",
        name="Latvia Open Data",
        transport="http",
        url="https://example.com/mcp",
        description="Open data",
    )
    snap = McpServerSnapshot(
        server=server,
        tools=[
            DiscoveredMcpTool(
                server=server,
                remote_name="search_datasets",
                description="Search datasets",
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            )
        ],
        resources=[{"uri": "res://one", "name": "One"}],
        resource_templates=[],
        prompts=[{"name": "intro", "description": "Intro", "arguments": []}],
        has_resources=True,
        has_prompts=True,
    )

    registry = ToolRegistry()
    set_active_mcp_snapshots([snap])
    _register_snapshots(registry, [snap])
    names = {spec.name for spec in registry.list_specs()}
    assert "data-lv__search_datasets" in names
    assert "data-lv__list_resources" in names
    assert "data-lv__read_resource" in names
    assert "data-lv__list_prompts" in names
    assert "data-lv__get_prompt" in names

    with patch(
        "app.services.mcp.bridge.call_mcp_tool",
        new=AsyncMock(
            return_value={"content": [{"type": "text", "text": "ok"}], "is_error": False}
        ),
    ) as call_mock:
        result = await registry.execute("data-lv__search_datasets", {"query": "transport"})
        assert result.output["content"][0]["text"] == "ok"
        call_mock.assert_awaited_once()

    listed = await registry.execute("data-lv__list_resources", {})
    assert listed.output["resources"][0]["uri"] == "res://one"

    with patch(
        "app.services.mcp.bridge.read_mcp_resource",
        new=AsyncMock(return_value={"uri": "res://one", "contents": []}),
    ):
        read = await registry.execute("data-lv__read_resource", {"uri": "res://one"})
        assert read.output["uri"] == "res://one"

    with patch(
        "app.services.mcp.bridge.get_mcp_prompt",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        failed = await registry.execute("data-lv__get_prompt", {"name": "intro"})
        assert "error" in failed.output


def test_mcp_source_items_from_tool_result_adds_server_and_urls() -> None:
    from app.services.mcp.bridge import mcp_source_items_from_tool_result

    server = McpServerConfig(
        id="data-lv",
        name="Latvia Open Data",
        transport="http",
        url="https://gateway.pipeworx.io/data-lv/mcp",
    )
    snap = McpServerSnapshot(server=server)
    set_active_mcp_snapshots([snap])
    items = mcp_source_items_from_tool_result(
        "data-lv__search_datasets",
        {
            "content": [
                {
                    "type": "text",
                    "text": "found",
                    "url": "https://data.gov.lv/dataset/buses",
                    "title": "Buses",
                }
            ],
            "is_error": False,
        },
    )
    assert items[0]["source_id"] == "mcp:data-lv"
    assert items[0]["title"] == "Latvia Open Data"
    assert any(item["url"] == "https://data.gov.lv/dataset/buses" for item in items)


def test_mcp_source_items_skips_errors() -> None:
    from app.services.mcp.bridge import mcp_source_items_from_tool_result

    assert (
        mcp_source_items_from_tool_result("data-lv__search_datasets", {"error": "boom"})
        == []
    )
    assert mcp_source_items_from_tool_result("web_search", {"queries": []}) == []


def test_mcp_guidance_for_tools() -> None:
    server = McpServerConfig(
        id="data-lv",
        name="Latvia Open Data",
        transport="http",
        url="https://example.com/mcp",
        description="Open data portal",
    )
    snap = McpServerSnapshot(server=server, tools=[], has_resources=False, has_prompts=False)
    set_active_mcp_snapshots([snap])
    text = mcp_guidance_for_tools(["data-lv__search_datasets", "web_search"])
    assert text is not None
    assert "data-lv" in text
    assert "Open data portal" in text
    assert "mcp_data_get" in text
    assert mcp_guidance_for_tools(["web_search"]) is None


@pytest.mark.asyncio
async def test_mcp_spill_and_data_tools(tmp_path, monkeypatch) -> None:
    from app.core.config import settings
    from app.services.mcp.spill import (
        describe_shape,
        maybe_spill_mcp_output,
        register_mcp_data_tools,
        resolve_json_path,
        stage_mcp_data_for_exec,
    )
    from app.services.tools.registry import ToolRegistry

    monkeypatch.setattr(settings, "files_base_dir", str(tmp_path))
    monkeypatch.setattr(settings, "mcp_spill_threshold_chars", 200)

    payload = {
        "is_error": False,
        "data": {
            "items": [{"id": i, "name": f"row-{i}"} for i in range(50)],
            "total": 50,
        },
    }
    stub = maybe_spill_mcp_output(
        payload,
        chat_id="chat-1",
        server_id="asya",
        tool_name="search",
    )
    assert stub["stored"] is True
    assert stub["artifact_id"]
    assert stub["path"].startswith("/workspace/data/")
    assert isinstance(stub["shape"], str)
    assert "array" in stub["shape"] or "{" in stub["shape"]
    assert "sample" in stub

    small = maybe_spill_mcp_output(
        {"is_error": False, "data": "ok"},
        chat_id="chat-1",
        server_id="asya",
        tool_name="ping",
    )
    assert small.get("stored") is not True
    assert small["data"] == "ok"

    assert resolve_json_path(payload, "data.items[0:2]") == payload["data"]["items"][:2]
    shape = describe_shape(payload["data"])
    assert shape.startswith("{") or shape.startswith("object")
    assert "items:array[50]" in shape

    # Homogeneous date-keyed maps must collapse, not repeat per key.
    by_date = {
        f"2026-01-{day:02d}": [{"id": 1, "name": "x", "ok": True} for _ in range(3)]
        for day in range(1, 10)
    }
    compact = describe_shape(by_date)
    assert compact.startswith("object[9 keys")
    assert compact.count("agent_name") == 0  # not that dataset
    assert compact.count("{id:int") == 1
    assert "array[3] of" in compact

    registry = ToolRegistry()
    register_mcp_data_tools(registry, chat_id="chat-1")

    listed = await registry.execute("mcp_data_list", {})
    assert listed.output["count"] == 1
    got = await registry.execute(
        "mcp_data_get",
        {
            "artifact_id": stub["artifact_id"],
            "path": "data.items",
            "offset": 0,
            "limit": 3,
        },
    )
    assert got.output["data"] == payload["data"]["items"][:3]

    work = tmp_path / "work"
    work.mkdir()
    staged = stage_mcp_data_for_exec("chat-1", work)
    assert staged
    assert (work / "data" / stub["file_name"]).is_file()
