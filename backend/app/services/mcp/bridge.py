from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings
from app.services.mcp.catalog import (
    MCP_SERVERS_CONFIG_PATH,
    McpServerConfig,
    load_mcp_catalog_soft,
)
from app.services.mcp.client import (
    call_mcp_tool,
    discover_server_capabilities,
    get_mcp_prompt,
    read_mcp_resource,
)
from app.services.tools.registry import ToolRegistry, ToolResult, ToolSpec

logger = logging.getLogger(__name__)

META_LIST_RESOURCES = "list_resources"
META_READ_RESOURCE = "read_resource"
META_LIST_PROMPTS = "list_prompts"
META_GET_PROMPT = "get_prompt"


@dataclass
class DiscoveredMcpTool:
    server: McpServerConfig
    remote_name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class McpServerSnapshot:
    server: McpServerConfig
    tools: list[DiscoveredMcpTool] = field(default_factory=list)
    resources: list[dict[str, Any]] = field(default_factory=list)
    resource_templates: list[dict[str, Any]] = field(default_factory=list)
    prompts: list[dict[str, Any]] = field(default_factory=list)
    has_resources: bool = False
    has_prompts: bool = False


@dataclass
class McpCache:
    snapshots: list[McpServerSnapshot] = field(default_factory=list)
    refreshed_at: float = 0.0
    refreshing: bool = False


_cache = McpCache()
_lock = threading.Lock()
_refresh_lock = asyncio.Lock()


def mcp_tool_name(server_id: str, remote_name: str) -> str:
    return f"{server_id}__{remote_name}"


def parse_mcp_tool_name(name: str) -> tuple[str, str] | None:
    if "__" not in name:
        return None
    server_id, remote = name.split("__", 1)
    if not server_id or not remote:
        return None
    return server_id, remote


def _filter_tools(
    server: McpServerConfig, tools: list[dict[str, Any]]
) -> list[DiscoveredMcpTool]:
    discovered: list[DiscoveredMcpTool] = []
    for tool in tools:
        remote_name = str(tool.get("name") or "").strip()
        if not remote_name:
            continue
        if server.tool_allowlist is not None and remote_name not in server.tool_allowlist:
            continue
        if server.tool_blocklist is not None and remote_name in server.tool_blocklist:
            continue
        schema = tool.get("input_schema")
        if not isinstance(schema, dict):
            schema = {"type": "object", "properties": {}}
        description = str(tool.get("description") or "").strip()
        if server.description:
            description = (
                f"[{server.name}] {description}" if description else f"[{server.name}]"
            )
        discovered.append(
            DiscoveredMcpTool(
                server=server,
                remote_name=remote_name,
                description=description or f"MCP tool {remote_name} on {server.name}",
                input_schema=schema,
            )
        )
    return discovered


def get_mcp_snapshots() -> list[McpServerSnapshot]:
    with _lock:
        return list(_cache.snapshots)


def mcp_cache_age_seconds() -> float | None:
    with _lock:
        if not _cache.refreshed_at:
            return None
        return max(0.0, time.monotonic() - _cache.refreshed_at)


def mcp_cache_is_stale() -> bool:
    age = mcp_cache_age_seconds()
    if age is None:
        return True
    return age >= max(1, int(settings.mcp_cache_ttl_seconds))


def mcp_guidance_for_tools(enabled_tool_names: list[str] | set[str] | None) -> str | None:
    names = set(enabled_tool_names or [])
    snapshots = get_mcp_snapshots()
    if not snapshots:
        return None
    active = [
        snap
        for snap in snapshots
        if any(name.startswith(f"{snap.server.id}__") for name in names)
    ]
    if not active:
        return None
    lines = [
        "Configured MCP data sources are available as tools named `{server_id}__{tool}`.",
        "Prefer these over web_search when the question is about that source's domain.",
        "Servers may also expose list_resources/read_resource and list_prompts/get_prompt meta-tools.",
        "",
        "Enabled MCP servers:",
    ]
    for snap in active:
        blurb = snap.server.description or snap.server.name
        lines.append(f"- {snap.server.id} ({snap.server.name}): {blurb}")
    return "\n".join(lines)


async def refresh_mcp_cache(*, force: bool = False) -> list[McpServerSnapshot]:
    async with _refresh_lock:
        if not force and not mcp_cache_is_stale() and get_mcp_snapshots():
            return get_mcp_snapshots()

        servers = load_mcp_catalog_soft(MCP_SERVERS_CONFIG_PATH)
        snapshots: list[McpServerSnapshot] = []
        for server in servers:
            try:
                discovered = await discover_server_capabilities(server)
            except Exception as exc:
                logger.warning(
                    "MCP discovery failed for %s: %s",
                    server.id,
                    exc,
                    exc_info=True,
                )
                continue
            tools = _filter_tools(server, discovered.get("tools") or [])
            resources = list(discovered.get("resources") or [])
            templates = list(discovered.get("resource_templates") or [])
            prompts = list(discovered.get("prompts") or [])
            caps = discovered.get("capabilities")
            resources_cap = getattr(caps, "resources", None) if caps is not None else None
            prompts_cap = getattr(caps, "prompts", None) if caps is not None else None
            has_resources = server.include.resources and (
                bool(resources or templates) or resources_cap is not None
            )
            has_prompts = server.include.prompts and (
                bool(prompts) or prompts_cap is not None
            )
            snapshots.append(
                McpServerSnapshot(
                    server=server,
                    tools=tools,
                    resources=resources,
                    resource_templates=templates,
                    prompts=prompts,
                    has_resources=has_resources,
                    has_prompts=has_prompts,
                )
            )
            logger.info(
                "MCP server ready id=%s tools=%s resources=%s prompts=%s",
                server.id,
                len(tools),
                len(resources) + len(templates),
                len(prompts),
            )

        with _lock:
            _cache.snapshots = snapshots
            _cache.refreshed_at = time.monotonic()
        return snapshots


def ensure_mcp_cache_sync(*, force: bool = False) -> list[McpServerSnapshot]:
    """Refresh cache from sync code when no event loop is running."""
    if not force and not mcp_cache_is_stale() and get_mcp_snapshots():
        return get_mcp_snapshots()
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(refresh_mcp_cache(force=force))
    # Already in an async context: keep current cache; lifespan should have warmed it.
    return get_mcp_snapshots()


def register_mcp_tools(registry: ToolRegistry) -> None:
    """Register discovered MCP tools (+ resource/prompt meta-tools) into the registry."""
    ensure_mcp_cache_sync()
    snapshots = get_mcp_snapshots()
    for snap in snapshots:
        server = snap.server
        for tool in snap.tools:
            tool_name = mcp_tool_name(server.id, tool.remote_name)
            remote_name = tool.remote_name

            async def _handler(
                args: dict,
                *,
                _server: McpServerConfig = server,
                _remote: str = remote_name,
            ) -> ToolResult:
                try:
                    output = await call_mcp_tool(_server, _remote, args or {})
                    if output.get("is_error") or output.get("error"):
                        error = output.get("error") or "MCP tool returned an error"
                        return ToolResult(
                            name=mcp_tool_name(_server.id, _remote),
                            output={**output, "error": str(error)},
                        )
                    return ToolResult(
                        name=mcp_tool_name(_server.id, _remote),
                        output=output,
                    )
                except Exception as exc:
                    logger.warning(
                        "MCP tool call failed server=%s tool=%s: %s",
                        _server.id,
                        _remote,
                        exc,
                        exc_info=True,
                    )
                    return ToolResult(
                        name=mcp_tool_name(_server.id, _remote),
                        output={
                            "error": str(exc),
                            "server_id": _server.id,
                            "tool": _remote,
                        },
                    )

            registry.register(
                ToolSpec(
                    name=tool_name,
                    description=tool.description,
                    parameters=tool.input_schema,
                ),
                _handler,
            )

        if snap.has_resources:
            list_name = mcp_tool_name(server.id, META_LIST_RESOURCES)
            read_name = mcp_tool_name(server.id, META_READ_RESOURCE)

            async def _list_resources_handler(
                args: dict, *, _snap: McpServerSnapshot = snap
            ) -> ToolResult:
                return ToolResult(
                    name=mcp_tool_name(_snap.server.id, META_LIST_RESOURCES),
                    output={
                        "resources": _snap.resources,
                        "resource_templates": _snap.resource_templates,
                    },
                )

            async def _read_resource_handler(
                args: dict, *, _server: McpServerConfig = server
            ) -> ToolResult:
                uri = str((args or {}).get("uri") or "").strip()
                if not uri:
                    return ToolResult(
                        name=mcp_tool_name(_server.id, META_READ_RESOURCE),
                        output={"error": "uri is required"},
                    )
                try:
                    output = await read_mcp_resource(_server, uri)
                    return ToolResult(
                        name=mcp_tool_name(_server.id, META_READ_RESOURCE),
                        output=output,
                    )
                except Exception as exc:
                    logger.warning(
                        "MCP read_resource failed server=%s: %s",
                        _server.id,
                        exc,
                        exc_info=True,
                    )
                    return ToolResult(
                        name=mcp_tool_name(_server.id, META_READ_RESOURCE),
                        output={"error": str(exc)},
                    )

            registry.register(
                ToolSpec(
                    name=list_name,
                    description=(
                        f"List MCP resources and URI templates from {server.name} "
                        f"({server.id})."
                    ),
                    parameters={"type": "object", "properties": {}},
                ),
                _list_resources_handler,
            )
            registry.register(
                ToolSpec(
                    name=read_name,
                    description=(
                        f"Read an MCP resource by URI from {server.name} ({server.id})."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "uri": {
                                "type": "string",
                                "description": "Resource URI (or filled URI template)",
                            }
                        },
                        "required": ["uri"],
                    },
                ),
                _read_resource_handler,
            )

        if snap.has_prompts:
            list_name = mcp_tool_name(server.id, META_LIST_PROMPTS)
            get_name = mcp_tool_name(server.id, META_GET_PROMPT)

            async def _list_prompts_handler(
                args: dict, *, _snap: McpServerSnapshot = snap
            ) -> ToolResult:
                return ToolResult(
                    name=mcp_tool_name(_snap.server.id, META_LIST_PROMPTS),
                    output={"prompts": _snap.prompts},
                )

            async def _get_prompt_handler(
                args: dict, *, _server: McpServerConfig = server
            ) -> ToolResult:
                prompt_name = str((args or {}).get("name") or "").strip()
                if not prompt_name:
                    return ToolResult(
                        name=mcp_tool_name(_server.id, META_GET_PROMPT),
                        output={"error": "name is required"},
                    )
                raw_args = (args or {}).get("arguments")
                prompt_args: dict[str, str] | None = None
                if isinstance(raw_args, dict):
                    prompt_args = {
                        str(key): str(value) for key, value in raw_args.items()
                    }
                try:
                    output = await get_mcp_prompt(_server, prompt_name, prompt_args)
                    return ToolResult(
                        name=mcp_tool_name(_server.id, META_GET_PROMPT),
                        output=output,
                    )
                except Exception as exc:
                    logger.warning(
                        "MCP get_prompt failed server=%s: %s",
                        _server.id,
                        exc,
                        exc_info=True,
                    )
                    return ToolResult(
                        name=mcp_tool_name(_server.id, META_GET_PROMPT),
                        output={"error": str(exc)},
                    )

            registry.register(
                ToolSpec(
                    name=list_name,
                    description=(
                        f"List MCP prompt templates from {server.name} ({server.id})."
                    ),
                    parameters={"type": "object", "properties": {}},
                ),
                _list_prompts_handler,
            )
            registry.register(
                ToolSpec(
                    name=get_name,
                    description=(
                        f"Fetch an MCP prompt template from {server.name} ({server.id})."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "Prompt name from list_prompts",
                            },
                            "arguments": {
                                "type": "object",
                                "additionalProperties": {"type": "string"},
                                "description": "Prompt arguments as string values",
                            },
                        },
                        "required": ["name"],
                    },
                ),
                _get_prompt_handler,
            )


def mcp_action_summary(name: str, arguments: dict[str, Any] | None = None) -> str | None:
    parsed = parse_mcp_tool_name(name)
    if not parsed:
        return None
    server_id, remote = parsed
    args = arguments if isinstance(arguments, dict) else {}
    if remote == META_LIST_RESOURCES:
        return f"Listing {server_id} resources"
    if remote == META_READ_RESOURCE:
        uri = str(args.get("uri") or "").strip()
        return f"Reading {server_id} resource" + (f": {uri[:80]}" if uri else "")
    if remote == META_LIST_PROMPTS:
        return f"Listing {server_id} prompts"
    if remote == META_GET_PROMPT:
        prompt_name = str(args.get("name") or "").strip()
        return f"Loading {server_id} prompt" + (
            f": {prompt_name}" if prompt_name else ""
        )
    return f"Querying {server_id}: {remote}"


_URL_KEYS = frozenset(
    {"url", "uri", "link", "href", "homepage", "source_url", "web_url", "dataset_url"}
)
_TITLE_KEYS = frozenset({"title", "name", "label", "dataset_title"})


def _host_for_url(url: str) -> str:
    if url.startswith("mcp://"):
        return url.removeprefix("mcp://") or "mcp"
    try:
        from urllib.parse import urlparse

        return urlparse(url).hostname or url
    except Exception:
        return url


def _walk_http_urls(
    value: Any, *, depth: int = 0
) -> list[tuple[str, str | None]]:
    if depth > 8 or value is None:
        return []
    found: list[tuple[str, str | None]] = []
    if isinstance(value, str):
        text = value.strip()
        if text.startswith(("http://", "https://")) and len(text) < 2000:
            found.append((text, None))
        return found
    if isinstance(value, list):
        for item in value:
            found.extend(_walk_http_urls(item, depth=depth + 1))
        return found
    if isinstance(value, dict):
        title: str | None = None
        for key in _TITLE_KEYS:
            raw = value.get(key)
            if isinstance(raw, str) and raw.strip():
                title = raw.strip()
                break
        for key, item in value.items():
            key_l = str(key).lower()
            if key_l in _URL_KEYS and isinstance(item, str):
                text = item.strip()
                if text.startswith(("http://", "https://")):
                    found.append((text, title))
                    continue
            found.extend(_walk_http_urls(item, depth=depth + 1))
        return found
    return found


def mcp_source_items_from_tool_result(
    tool_name: str, output: object
) -> list[dict[str, Any]]:
    """Build Sources-panel items for a successful MCP tool call."""
    parsed = parse_mcp_tool_name(tool_name)
    if not parsed:
        return []
    if isinstance(output, dict):
        if output.get("is_error") is True:
            return []
        if output.get("error") and not (
            output.get("content")
            or output.get("structured_content")
            or output.get("data")
            or output.get("contents")
        ):
            return []

    server_id, _remote = parsed
    server: McpServerConfig | None = None
    for snap in get_mcp_snapshots():
        if snap.server.id == server_id:
            server = snap.server
            break

    items: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    if server is not None:
        url = (server.url or "").strip() or f"mcp://{server.id}"
        seen_urls.add(url)
        items.append(
            {
                "url": url,
                "title": server.name,
                "host": _host_for_url(url),
                "source_id": f"mcp:{server.id}",
            }
        )
    else:
        url = f"mcp://{server_id}"
        seen_urls.add(url)
        items.append(
            {
                "url": url,
                "title": server_id,
                "host": server_id,
                "source_id": f"mcp:{server_id}",
            }
        )

    for url, title in _walk_http_urls(output):
        if url in seen_urls:
            continue
        seen_urls.add(url)
        items.append(
            {
                "url": url,
                "title": title,
                "host": _host_for_url(url),
            }
        )
    return items
