from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlmodel import Session, select

from app.models import (
    McpOrgBinding,
    McpOrgSettings,
    McpServer,
    McpSettings,
    McpUserConnection,
)
from app.services.mcp.auth_config import (
    auth_fingerprint,
    encrypt_auth_config,
    headers_from_shared_auth,
    headers_from_user_connection,
)
from app.services.mcp.types import McpInclude, McpServerConfig, ResolvedMcpServer

def get_mcp_settings(session: Session) -> McpSettings:
    settings = session.get(McpSettings, 1)
    if not settings:
        settings = McpSettings(id=1)
        session.add(settings)
        session.commit()
        session.refresh(settings)
    return settings


def get_mcp_org_settings(session: Session, org_id: UUID) -> McpOrgSettings:
    row = session.get(McpOrgSettings, org_id)
    if not row:
        row = McpOrgSettings(org_id=org_id, allow_user_servers=True)
        session.add(row)
        session.commit()
        session.refresh(row)
    return row


def _row_to_config(row: McpServer) -> McpServerConfig:
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
        headers={},
        include=McpInclude(
            tools=row.include_tools,
            resources=row.include_resources,
            prompts=row.include_prompts,
        ),
        tool_allowlist=allowlist,
        tool_blocklist=blocklist,
    )


def _effective_auth(
    row: McpServer,
    binding: McpOrgBinding | None,
) -> tuple[str, str | None]:
    if binding and binding.mode == "override" and binding.auth_type:
        return binding.auth_type, binding.auth_config
    return row.auth_type, row.auth_config


def _user_connection(
    session: Session, user_id: UUID, server_id: UUID
) -> McpUserConnection | None:
    return session.exec(
        select(McpUserConnection).where(
            McpUserConnection.user_id == user_id,
            McpUserConnection.server_id == server_id,
            McpUserConnection.status == "connected",
        )
    ).first()


def resolve_auth_headers(
    session: Session,
    resolved: ResolvedMcpServer,
    *,
    org_id: UUID,
    user_id: UUID,
) -> dict[str, str]:
    row = session.get(McpServer, resolved.db_id)
    if not row:
        return {}

    binding = session.exec(
        select(McpOrgBinding).where(
            McpOrgBinding.org_id == org_id,
            McpOrgBinding.instance_server_id == row.id,
        )
    ).first()

    auth_type, auth_config = _effective_auth(row, binding)

    if auth_type == "user_provided":
        connection = _user_connection(session, user_id, row.id)
        if not connection:
            return {}
        return headers_from_user_connection(connection.auth_config)

    return headers_from_shared_auth(auth_type, auth_config)


def resolve_test_auth(
    session: Session,
    row: McpServer,
    *,
    user_id: UUID,
    org_id: UUID | None = None,
) -> tuple[str, dict[str, str]]:
    """Return (auth_type, headers) for an admin/user connection test."""
    binding = None
    if org_id is not None and row.scope == "instance":
        binding = session.exec(
            select(McpOrgBinding).where(
                McpOrgBinding.org_id == org_id,
                McpOrgBinding.instance_server_id == row.id,
            )
        ).first()
    auth_type, auth_config = _effective_auth(row, binding)
    if auth_type == "user_provided":
        connection = _user_connection(session, user_id, row.id)
        if not connection:
            return auth_type, {}
        return auth_type, headers_from_user_connection(connection.auth_config)
    return auth_type, headers_from_shared_auth(auth_type, auth_config)


def resolve_effective_mcp_servers(
    session: Session,
    *,
    org_id: UUID,
    user_id: UUID,
) -> list[ResolvedMcpServer]:
    settings = get_mcp_settings(session)
    org_settings = get_mcp_org_settings(session, org_id)

    bindings = {
        binding.instance_server_id: binding
        for binding in session.exec(
            select(McpOrgBinding).where(McpOrgBinding.org_id == org_id)
        ).all()
    }

    resolved: list[ResolvedMcpServer] = []
    seen_slugs: set[str] = set()

    instance_rows = session.exec(
        select(McpServer).where(
            McpServer.scope == "instance",
            McpServer.is_enabled.is_(True),
        )
    ).all()
    for row in instance_rows:
        binding = bindings.get(row.id)
        if binding and binding.mode == "disabled":
            continue
        if row.slug in seen_slugs:
            continue
        auth_type, auth_config = _effective_auth(row, binding)
        if auth_type == "user_provided":
            connection = _user_connection(session, user_id, row.id)
            if not connection:
                continue
            fingerprint = auth_fingerprint(auth_type, auth_config, connection.auth_config)
        else:
            fingerprint = auth_fingerprint(auth_type, auth_config)
        config = _row_to_config(row)
        headers = resolve_auth_headers(
            session,
            ResolvedMcpServer(db_id=row.id, config=config, auth_type=auth_type, auth_fingerprint=fingerprint),
            org_id=org_id,
            user_id=user_id,
        )
        config = McpServerConfig(
            id=config.id,
            name=config.name,
            transport=config.transport,
            enabled=config.enabled,
            description=config.description,
            url=config.url,
            command=config.command,
            args=config.args,
            env=config.env,
            headers=headers,
            include=config.include,
            tool_allowlist=config.tool_allowlist,
            tool_blocklist=config.tool_blocklist,
        )
        resolved.append(
            ResolvedMcpServer(
                db_id=row.id,
                config=config,
                auth_type=auth_type,  # type: ignore[arg-type]
                auth_fingerprint=fingerprint,
            )
        )
        seen_slugs.add(row.slug)

    if settings.allow_org_servers:
        org_rows = session.exec(
            select(McpServer).where(
                McpServer.scope == "org",
                McpServer.org_id == org_id,
                McpServer.is_enabled.is_(True),
            )
        ).all()
        for row in org_rows:
            if row.slug in seen_slugs:
                continue
            auth_type = row.auth_type
            auth_config = row.auth_config
            if auth_type == "user_provided":
                connection = _user_connection(session, user_id, row.id)
                if not connection:
                    continue
                fingerprint = auth_fingerprint(auth_type, auth_config, connection.auth_config)
                headers = headers_from_user_connection(connection.auth_config)
            else:
                fingerprint = auth_fingerprint(auth_type, auth_config)
                headers = headers_from_shared_auth(auth_type, auth_config)
            config = _row_to_config(row)
            config = McpServerConfig(
                id=config.id,
                name=config.name,
                transport=config.transport,
                enabled=config.enabled,
                description=config.description,
                url=config.url,
                command=config.command,
                args=config.args,
                env=config.env,
                headers=headers,
                include=config.include,
                tool_allowlist=config.tool_allowlist,
                tool_blocklist=config.tool_blocklist,
            )
            resolved.append(
                ResolvedMcpServer(
                    db_id=row.id,
                    config=config,
                    auth_type=auth_type,  # type: ignore[arg-type]
                    auth_fingerprint=fingerprint,
                )
            )
            seen_slugs.add(row.slug)

    if settings.allow_user_servers and org_settings.allow_user_servers:
        user_rows = session.exec(
            select(McpServer).where(
                McpServer.scope == "user",
                McpServer.user_id == user_id,
                McpServer.is_enabled.is_(True),
            )
        ).all()
        for row in user_rows:
            if row.slug in seen_slugs:
                continue
            auth_type = row.auth_type
            auth_config = row.auth_config
            if auth_type == "user_provided":
                connection = _user_connection(session, user_id, row.id)
                if not connection:
                    continue
                fingerprint = auth_fingerprint(auth_type, auth_config, connection.auth_config)
                headers = headers_from_user_connection(connection.auth_config)
            else:
                fingerprint = auth_fingerprint(auth_type, auth_config)
                headers = headers_from_shared_auth(auth_type, auth_config)
            config = _row_to_config(row)
            config = McpServerConfig(
                id=config.id,
                name=config.name,
                transport=config.transport,
                enabled=config.enabled,
                description=config.description,
                url=config.url,
                command=config.command,
                args=config.args,
                env=config.env,
                headers=headers,
                include=config.include,
                tool_allowlist=config.tool_allowlist,
                tool_blocklist=config.tool_blocklist,
            )
            resolved.append(
                ResolvedMcpServer(
                    db_id=row.id,
                    config=config,
                    auth_type=auth_type,  # type: ignore[arg-type]
                    auth_fingerprint=fingerprint,
                )
            )
            seen_slugs.add(row.slug)

    return resolved


def store_user_token_connection(
    session: Session,
    *,
    user_id: UUID,
    server_id: UUID,
    method: str,
    token: str,
    header_name: str | None = None,
    header_format: str | None = None,
) -> McpUserConnection:
    payload: dict[str, Any] = {"method": method, "token": token.strip()}
    if method == "api_token":
        if header_name:
            payload["header_name"] = header_name
        if header_format:
            payload["header_format"] = header_format
    encrypted = encrypt_auth_config(payload)
    if not encrypted:
        raise ValueError("token is required")

    existing = session.exec(
        select(McpUserConnection).where(
            McpUserConnection.user_id == user_id,
            McpUserConnection.server_id == server_id,
        )
    ).first()
    if existing:
        existing.auth_config = encrypted
        existing.status = "connected"
        existing.expires_at = None
        existing.updated_at = datetime.utcnow()
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing

    row = McpUserConnection(
        user_id=user_id,
        server_id=server_id,
        auth_config=encrypted,
        status="connected",
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row
