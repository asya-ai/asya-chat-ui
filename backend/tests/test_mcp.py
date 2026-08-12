from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.services.mcp.bridge import (
    DiscoveredMcpTool,
    McpServerSnapshot,
    mcp_action_summary,
    mcp_guidance_for_tools,
    mcp_tool_name,
    parse_mcp_tool_name,
    register_mcp_tools,
)
from app.services.mcp.catalog import (
    CatalogLoadError,
    expand_env_string,
    load_mcp_catalog,
    load_mcp_catalog_soft,
)
from app.services.mcp.client import open_mcp_session, truncate_json_text
from app.services.tools.registry import ToolRegistry
from app.services.tools.previews import tool_call_action_summary


def test_expand_env_string_replaces_vars() -> None:
    assert (
        expand_env_string("Bearer ${TOKEN}", env={"TOKEN": "abc"})
        == "Bearer abc"
    )


def test_expand_env_string_missing_var_raises() -> None:
    with pytest.raises(CatalogLoadError, match="Missing environment variable"):
        expand_env_string("Bearer ${MISSING}", env={})


def test_load_catalog_skips_disabled_and_missing_env(tmp_path: Path) -> None:
    path = tmp_path / "mcp.yaml"
    path.write_text(
        """
servers:
  - id: public
    name: Public
    transport: http
    url: https://example.com/mcp
    enabled: true
  - id: off
    name: Off
    transport: http
    url: https://example.com/off
    enabled: false
  - id: authed
    name: Authed
    transport: http
    url: https://example.com/auth
    enabled: true
    headers:
      Authorization: "Bearer ${MCP_TOKEN}"
""",
        encoding="utf-8",
    )
    servers = load_mcp_catalog(path, env={})
    assert [s.id for s in servers] == ["public"]

    servers = load_mcp_catalog(path, env={"MCP_TOKEN": "secret"})
    assert [s.id for s in servers] == ["public", "authed"]
    assert servers[1].headers["Authorization"] == "Bearer secret"


def test_load_catalog_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_mcp_catalog(tmp_path / "missing.yaml") == []


def test_load_catalog_invalid_yaml_raises(tmp_path: Path) -> None:
    path = tmp_path / "mcp.yaml"
    path.write_text("servers: [\n  - id: broken\n", encoding="utf-8")
    with pytest.raises(CatalogLoadError, match="Invalid YAML"):
        load_mcp_catalog(path)
    assert load_mcp_catalog_soft(path) == []


def test_load_catalog_servers_must_be_list(tmp_path: Path) -> None:
    path = tmp_path / "mcp.yaml"
    path.write_text("servers: not-a-list\n", encoding="utf-8")
    with pytest.raises(CatalogLoadError, match="must be a list"):
        load_mcp_catalog(path)


def test_load_catalog_stdio_requires_command(tmp_path: Path) -> None:
    path = tmp_path / "mcp.yaml"
    path.write_text(
        """
servers:
  - id: local
    transport: stdio
    enabled: true
""",
        encoding="utf-8",
    )
    assert load_mcp_catalog(path) == []


def test_load_catalog_stdio_ok(tmp_path: Path) -> None:
    path = tmp_path / "mcp.yaml"
    path.write_text(
        """
servers:
  - id: local
    transport: stdio
    command: npx
    args: ["-y", "demo"]
    env:
      FOO: "${FOO_VAL}"
    enabled: true
""",
        encoding="utf-8",
    )
    servers = load_mcp_catalog(path, env={"FOO_VAL": "bar"})
    assert len(servers) == 1
    assert servers[0].transport == "stdio"
    assert servers[0].command == "npx"
    assert servers[0].args == ("-y", "demo")
    assert servers[0].env == {"FOO": "bar"}


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


@pytest.mark.asyncio
async def test_open_mcp_session_selects_http_transport() -> None:
    from app.services.mcp.catalog import McpServerConfig

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
    from app.services.mcp.catalog import McpServerConfig

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

    with patch("app.services.mcp.bridge.ensure_mcp_cache_sync", return_value=[snap]):
        with patch("app.services.mcp.bridge.get_mcp_snapshots", return_value=[snap]):
            registry = ToolRegistry()
            register_mcp_tools(registry)
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
                result = await registry.execute(
                    "data-lv__search_datasets", {"query": "transport"}
                )
                assert result.output["content"][0]["text"] == "ok"
                call_mock.assert_awaited_once()

            listed = await registry.execute("data-lv__list_resources", {})
            assert listed.output["resources"][0]["uri"] == "res://one"

            with patch(
                "app.services.mcp.bridge.read_mcp_resource",
                new=AsyncMock(return_value={"uri": "res://one", "contents": []}),
            ):
                read = await registry.execute(
                    "data-lv__read_resource", {"uri": "res://one"}
                )
                assert read.output["uri"] == "res://one"

            with patch(
                "app.services.mcp.bridge.get_mcp_prompt",
                new=AsyncMock(side_effect=RuntimeError("boom")),
            ):
                failed = await registry.execute(
                    "data-lv__get_prompt", {"name": "intro"}
                )
                assert "error" in failed.output


def test_mcp_source_items_from_tool_result_adds_server_and_urls() -> None:
    from app.services.mcp.bridge import (
        McpServerSnapshot,
        mcp_source_items_from_tool_result,
    )
    from app.services.mcp.catalog import McpServerConfig

    server = McpServerConfig(
        id="data-lv",
        name="Latvia Open Data",
        transport="http",
        url="https://gateway.pipeworx.io/data-lv/mcp",
    )
    snap = McpServerSnapshot(server=server)
    with patch("app.services.mcp.bridge.get_mcp_snapshots", return_value=[snap]):
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
        mcp_source_items_from_tool_result(
            "data-lv__search_datasets", {"error": "boom"}
        )
        == []
    )
    assert mcp_source_items_from_tool_result("web_search", {"queries": []}) == []


def test_mcp_guidance_for_tools() -> None:
    from app.services.mcp.bridge import McpServerSnapshot, mcp_guidance_for_tools
    from app.services.mcp.catalog import McpServerConfig

    server = McpServerConfig(
        id="data-lv",
        name="Latvia Open Data",
        transport="http",
        url="https://example.com/mcp",
        description="Open data portal",
    )
    snap = McpServerSnapshot(server=server, tools=[], has_resources=False, has_prompts=False)
    with patch("app.services.mcp.bridge.get_mcp_snapshots", return_value=[snap]):
        text = mcp_guidance_for_tools(["data-lv__search_datasets", "web_search"])
        assert text is not None
        assert "data-lv" in text
        assert "Open data portal" in text
        assert mcp_guidance_for_tools(["web_search"]) is None
