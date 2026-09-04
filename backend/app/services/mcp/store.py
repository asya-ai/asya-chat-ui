from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlmodel import Session, select

from app.models import McpOrgBinding, McpServer, McpSettings, McpUserConnection
from app.services.mcp.auth_config import (
    auth_config_token_set,
    decrypt_auth_config,
    encrypt_auth_config,
    merge_auth_config,
    validate_server_auth_payload,
    validate_slug,
)
from app.services.mcp.client import discover_server_capabilities
from app.services.mcp.registry import get_mcp_settings
from app.services.mcp.types import McpInclude, McpServerConfig


def _now() -> datetime:
    return datetime.utcnow()


def server_to_read(row: McpServer, *, connection_status: str | None = None) -> dict[str, Any]:
    auth_type = row.auth_type
    token_set = auth_config_token_set(row.auth_config)
    user_auth_method = None
    auth_data = decrypt_auth_config(row.auth_config)
    if auth_type == "user_provided":
        user_auth_method = auth_data.get("user_auth_method")
    return {
        "id": str(row.id),
        "slug": row.slug,
        "name": row.name,
        "description": row.description,
        "scope": row.scope,
        "org_id": str(row.org_id) if row.org_id else None,
        "user_id": str(row.user_id) if row.user_id else None,
        "transport": row.transport,
        "url": row.url,
        "command": row.command,
        "args": row.args or [],
        "stdio_env": row.stdio_env or {},
        "include_tools": row.include_tools,
        "include_resources": row.include_resources,
        "include_prompts": row.include_prompts,
        "tool_allowlist": row.tool_allowlist,
        "tool_blocklist": row.tool_blocklist,
        "auth_type": auth_type,
        "token_set": token_set,
        "user_auth_method": user_auth_method,
        "user_instructions": auth_data.get("user_instructions"),
        "api_token_header_name": (
            auth_data.get("header_name")
            if auth_type == "api_token"
            else (
                (auth_data.get("api_token_defaults") or {}).get("header_name")
                if auth_type == "user_provided"
                else None
            )
        ),
        "api_token_header_format": (
            auth_data.get("header_format")
            if auth_type == "api_token"
            else (
                (auth_data.get("api_token_defaults") or {}).get("header_format")
                if auth_type == "user_provided"
                else None
            )
        ),
        "is_enabled": row.is_enabled,
        "connection_status": connection_status,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


def build_auth_config_payload(
    auth_type: str,
    *,
    token: str | None = None,
    header_name: str | None = None,
    header_format: str | None = None,
    user_auth_method: str | None = None,
    user_instructions: str | None = None,
    existing_auth_config: str | None = None,
) -> str | None:
    if auth_type == "none":
        return None

    existing = decrypt_auth_config(existing_auth_config)

    if auth_type == "bearer":
        payload: dict[str, Any] = {}
        if token is not None and token.strip():
            payload["token"] = token.strip()
        elif existing.get("token"):
            payload["token"] = existing["token"]
        validate_server_auth_payload(auth_type, payload)
        return encrypt_auth_config(payload)

    if auth_type == "api_token":
        payload = {
            "header_name": header_name or existing.get("header_name") or "Authorization",
            "header_format": header_format
            or existing.get("header_format")
            or "Bearer {token}",
        }
        if token is not None and token.strip():
            payload["token"] = token.strip()
        elif existing.get("token"):
            payload["token"] = existing["token"]
        validate_server_auth_payload(auth_type, payload)
        return encrypt_auth_config(payload)

    if auth_type == "user_provided":
        method = user_auth_method or existing.get("user_auth_method")
        payload = {
            "user_auth_method": method,
            "user_instructions": user_instructions
            if user_instructions is not None
            else existing.get("user_instructions"),
        }
        if method == "api_token":
            defaults = existing.get("api_token_defaults")
            if not isinstance(defaults, dict):
                defaults = {}
            payload["api_token_defaults"] = {
                "header_name": header_name or defaults.get("header_name") or "Authorization",
                "header_format": header_format
                or defaults.get("header_format")
                or "Bearer {token}",
            }
        validate_server_auth_payload(auth_type, payload)
        return encrypt_auth_config(payload)

    raise ValueError(f"unsupported auth_type: {auth_type}")


def create_server(
    session: Session,
    *,
    scope: str,
    slug: str,
    name: str,
    transport: str,
    auth_type: str,
    org_id: UUID | None = None,
    user_id: UUID | None = None,
    description: str | None = None,
    url: str | None = None,
    command: str | None = None,
    args: list[str] | None = None,
    stdio_env: dict[str, str] | None = None,
    include_tools: bool = True,
    include_resources: bool = True,
    include_prompts: bool = True,
    tool_allowlist: list[str] | None = None,
    tool_blocklist: list[str] | None = None,
    is_enabled: bool = True,
    auth_config: str | None = None,
) -> McpServer:
    slug_value = validate_slug(slug)
    if session.exec(select(McpServer).where(McpServer.slug == slug_value)).first():
        raise ValueError("slug already exists")
    if transport == "stdio" and scope != "instance":
        raise ValueError("stdio transport is only allowed for instance servers")
    if transport in {"http", "sse"} and not (url or "").strip():
        raise ValueError("url is required for http/sse transport")
    if transport == "stdio" and not (command or "").strip():
        raise ValueError("command is required for stdio transport")

    row = McpServer(
        slug=slug_value,
        name=name.strip() or slug_value,
        description=(description or "").strip() or None,
        scope=scope,
        org_id=org_id,
        user_id=user_id,
        transport=transport,
        url=(url or "").strip() or None,
        command=(command or "").strip() or None,
        args=args or [],
        stdio_env=stdio_env or {},
        include_tools=include_tools,
        include_resources=include_resources,
        include_prompts=include_prompts,
        tool_allowlist=tool_allowlist,
        tool_blocklist=tool_blocklist,
        auth_type=auth_type,
        auth_config=auth_config,
        is_enabled=is_enabled,
        created_at=_now(),
        updated_at=_now(),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def update_server(session: Session, row: McpServer, **updates: Any) -> McpServer:
    if "slug" in updates and updates["slug"] is not None:
        slug_value = validate_slug(updates["slug"])
        existing = session.exec(
            select(McpServer).where(McpServer.slug == slug_value, McpServer.id != row.id)
        ).first()
        if existing:
            raise ValueError("slug already exists")
        row.slug = slug_value
    for field in (
        "name",
        "description",
        "transport",
        "url",
        "command",
        "args",
        "stdio_env",
        "include_tools",
        "include_resources",
        "include_prompts",
        "tool_allowlist",
        "tool_blocklist",
        "auth_type",
        "auth_config",
        "is_enabled",
    ):
        if field in updates and updates[field] is not None:
            setattr(row, field, updates[field])
    transport = row.transport
    if transport == "stdio" and row.scope != "instance":
        raise ValueError("stdio transport is only allowed for instance servers")
    row.updated_at = _now()
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def delete_server(session: Session, row: McpServer) -> None:
    bindings = session.exec(
        select(McpOrgBinding).where(McpOrgBinding.instance_server_id == row.id)
    ).all()
    for binding in bindings:
        session.delete(binding)
    connections = session.exec(
        select(McpUserConnection).where(McpUserConnection.server_id == row.id)
    ).all()
    for connection in connections:
        session.delete(connection)
    session.delete(row)
    session.commit()


def row_to_runtime_config(row: McpServer, headers: dict[str, str] | None = None) -> McpServerConfig:
    allowlist = frozenset(row.tool_allowlist) if row.tool_allowlist else None
    blocklist = frozenset(row.tool_blocklist) if row.tool_blocklist else None
    return McpServerConfig(
        id=row.slug,
        name=row.name,
        transport=row.transport,  # type: ignore[arg-type]
        enabled=row.is_enabled,
        description=row.description or "",
        url=row.url,
        command=row.command,
        args=tuple(row.args or ()),
        env=dict(row.stdio_env or {}),
        headers=headers or {},
        include=McpInclude(
            tools=row.include_tools,
            resources=row.include_resources,
            prompts=row.include_prompts,
        ),
        tool_allowlist=allowlist,
        tool_blocklist=blocklist,
    )


async def test_server_connection(
    row: McpServer,
    headers: dict[str, str] | None = None,
    *,
    auth_type: str | None = None,
) -> dict[str, Any]:
    effective_auth = auth_type or row.auth_type
    headers = headers or {}
    if effective_auth == "user_provided" and not headers:
        return {
            "status": "configured",
            "detail": "Requires user connection",
            "latency_ms": None,
            "tools": None,
            "resources": None,
            "prompts": None,
        }
    config = row_to_runtime_config(row, headers=headers)
    started = _now()
    try:
        discovered = await discover_server_capabilities(config)
    except Exception as exc:
        return {
            "status": "invalid",
            "detail": str(exc),
            "latency_ms": None,
            "tools": None,
            "resources": None,
            "prompts": None,
        }
    latency = (_now() - started).total_seconds() * 1000
    return {
        "status": "ok",
        "detail": None,
        "latency_ms": round(latency, 1),
        "tools": len(discovered.get("tools") or []),
        "resources": len(discovered.get("resources") or [])
        + len(discovered.get("resource_templates") or []),
        "prompts": len(discovered.get("prompts") or []),
    }


def test_server_connection_sync(row: McpServer, headers: dict[str, str] | None = None) -> dict[str, Any]:
    return asyncio.run(test_server_connection(row, headers))


def ensure_org_servers_allowed(session: Session) -> None:
    settings = get_mcp_settings(session)
    if not settings.allow_org_servers:
        raise ValueError("Org MCP servers are disabled for this instance")


def ensure_user_servers_allowed(session: Session, org_id: UUID | None = None) -> None:
    settings = get_mcp_settings(session)
    if not settings.allow_user_servers:
        raise ValueError("User MCP servers are disabled for this instance")
    if org_id is not None:
        from app.services.mcp.registry import get_mcp_org_settings

        org_settings = get_mcp_org_settings(session, org_id)
        if not org_settings.allow_user_servers:
            raise ValueError("User MCP servers are disabled for this organization")
