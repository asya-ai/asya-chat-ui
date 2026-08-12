from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

logger = logging.getLogger(__name__)

# Host-mounted catalog (compose: ./config:/config:ro). Not overridable by env.
MCP_SERVERS_CONFIG_PATH = "/config/mcp_servers.yaml"

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

Transport = Literal["http", "sse", "stdio"]


@dataclass(frozen=True)
class McpInclude:
    tools: bool = True
    resources: bool = True
    prompts: bool = True


@dataclass(frozen=True)
class McpServerConfig:
    id: str
    name: str
    transport: Transport
    enabled: bool = True
    description: str = ""
    url: str | None = None
    command: str | None = None
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    include: McpInclude = field(default_factory=McpInclude)
    tool_allowlist: frozenset[str] | None = None
    tool_blocklist: frozenset[str] | None = None


class CatalogLoadError(Exception):
    """Raised when a server entry cannot be resolved (e.g. missing env var)."""


def expand_env_string(value: str, *, env: dict[str, str] | None = None) -> str:
    source = env if env is not None else os.environ

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in source or source[key] == "":
            raise CatalogLoadError(f"Missing environment variable: {key}")
        return source[key]

    return _ENV_PATTERN.sub(_replace, value)


def _expand_mapping(
    raw: dict[str, Any] | None, *, env: dict[str, str] | None = None
) -> dict[str, str]:
    if not raw:
        return {}
    result: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise CatalogLoadError("headers/env values must be strings")
        result[key] = expand_env_string(value, env=env)
    return result


def _parse_include(raw: Any) -> McpInclude:
    if not isinstance(raw, dict):
        return McpInclude()
    return McpInclude(
        tools=bool(raw.get("tools", True)),
        resources=bool(raw.get("resources", True)),
        prompts=bool(raw.get("prompts", True)),
    )


def _parse_name_set(raw: Any) -> frozenset[str] | None:
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise CatalogLoadError("tool_allowlist/tool_blocklist must be a list or null")
    names = [str(item).strip() for item in raw if str(item).strip()]
    return frozenset(names)


def _parse_server(raw: dict[str, Any], *, env: dict[str, str] | None = None) -> McpServerConfig:
    server_id = str(raw.get("id") or "").strip()
    if not server_id:
        raise CatalogLoadError("server id is required")
    if "__" in server_id:
        raise CatalogLoadError(f"server id must not contain '__': {server_id}")

    transport_raw = str(raw.get("transport") or "http").strip().lower()
    if transport_raw not in {"http", "sse", "stdio"}:
        raise CatalogLoadError(f"unsupported transport for {server_id}: {transport_raw}")
    transport: Transport = transport_raw  # type: ignore[assignment]

    name = str(raw.get("name") or server_id).strip() or server_id
    description = str(raw.get("description") or "").strip()
    enabled = bool(raw.get("enabled", True))

    url = raw.get("url")
    url_str = str(url).strip() if isinstance(url, str) and url.strip() else None
    command = raw.get("command")
    command_str = (
        str(command).strip() if isinstance(command, str) and command.strip() else None
    )
    args_raw = raw.get("args") or []
    if not isinstance(args_raw, list):
        raise CatalogLoadError(f"args must be a list for {server_id}")
    args = tuple(str(item) for item in args_raw)

    if transport in {"http", "sse"} and not url_str:
        raise CatalogLoadError(f"{server_id}: url is required for transport={transport}")
    if transport == "stdio" and not command_str:
        raise CatalogLoadError(f"{server_id}: command is required for transport=stdio")

    headers = _expand_mapping(raw.get("headers"), env=env)
    stdio_env = _expand_mapping(raw.get("env"), env=env)

    return McpServerConfig(
        id=server_id,
        name=name,
        transport=transport,
        enabled=enabled,
        description=description,
        url=url_str,
        command=command_str,
        args=args,
        env=stdio_env,
        headers=headers,
        include=_parse_include(raw.get("include")),
        tool_allowlist=_parse_name_set(raw.get("tool_allowlist")),
        tool_blocklist=_parse_name_set(raw.get("tool_blocklist")),
    )


def load_mcp_catalog(
    path: str | Path,
    *,
    env: dict[str, str] | None = None,
) -> list[McpServerConfig]:
    """Load enabled MCP servers from YAML. Missing file → empty list.

    Invalid YAML / catalog structure raises CatalogLoadError so callers
    (e.g. system diagnosis) can alert; the tool bridge catches and logs.
    """
    config_path = Path(path)
    if not config_path.is_file():
        logger.info("MCP catalog not found at %s; no MCP servers loaded", config_path)
        return []

    try:
        text = config_path.read_text(encoding="utf-8")
    except Exception as exc:
        raise CatalogLoadError(f"Cannot read catalog {config_path}: {exc}") from exc

    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise CatalogLoadError(f"Invalid YAML in {config_path}: {exc}") from exc
    except Exception as exc:
        raise CatalogLoadError(f"Failed to parse catalog {config_path}: {exc}") from exc

    if raw is None or raw == "":
        return []
    if not isinstance(raw, dict):
        raise CatalogLoadError(
            f"MCP catalog root must be a mapping (got {type(raw).__name__}): {config_path}"
        )

    servers_raw = raw.get("servers")
    if servers_raw is None:
        return []
    if not isinstance(servers_raw, list):
        raise CatalogLoadError(
            f"MCP catalog 'servers' must be a list (got {type(servers_raw).__name__}): {config_path}"
        )

    servers: list[McpServerConfig] = []
    seen_ids: set[str] = set()
    for index, entry in enumerate(servers_raw):
        if not isinstance(entry, dict):
            logger.warning("Skipping non-object MCP server entry at index %s", index)
            continue
        try:
            server = _parse_server(entry, env=env)
        except CatalogLoadError as exc:
            logger.warning("Skipping MCP server entry %s: %s", entry.get("id"), exc)
            continue
        if not server.enabled:
            continue
        if server.id in seen_ids:
            logger.warning("Duplicate MCP server id %s; keeping first", server.id)
            continue
        seen_ids.add(server.id)
        servers.append(server)
    return servers


def load_mcp_catalog_soft(
    path: str | Path,
    *,
    env: dict[str, str] | None = None,
) -> list[McpServerConfig]:
    """Like load_mcp_catalog, but invalid YAML returns [] after logging."""
    try:
        return load_mcp_catalog(path, env=env)
    except CatalogLoadError as exc:
        logger.warning("MCP catalog unavailable: %s", exc)
        return []
