"""Materialize catalog integrations as McpServer rows + user connections."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlmodel import Session, select

from app.models import McpServer, McpUserConnection
from app.services.integrations.catalog import CatalogIntegration, get_catalog
from app.services.integrations.store import (
    catalog_admin_complete,
    is_catalog_enabled,
    resolve_effective_admin_config,
)
from app.services.mcp.registry import store_user_token_connection
from app.services.mcp.store import build_auth_config_payload


def _now() -> datetime:
    return datetime.utcnow()


def find_catalog_mcp_server(
    session: Session, catalog_id: str
) -> McpServer | None:
    return session.exec(
        select(McpServer).where(
            McpServer.origin == "catalog",
            McpServer.catalog_id == catalog_id,
            McpServer.scope == "instance",
        )
    ).first()


def ensure_catalog_mcp_server(
    session: Session,
    entry: CatalogIntegration,
    *,
    mcp_url: str,
) -> McpServer:
    """Create or update the instance McpServer backing a catalog HTTP MCP app."""
    if entry.mcp.transport not in {"http", "sse"}:
        raise ValueError(f"unsupported catalog transport: {entry.mcp.transport}")
    url = mcp_url.strip()
    if not url:
        raise ValueError("mcp_url is required")

    auth_config = build_auth_config_payload(
        "user_provided",
        user_auth_method="bearer",
        user_instructions=entry.user_instructions,
    )
    existing = find_catalog_mcp_server(session, entry.catalog_id)
    now = _now()
    if existing:
        existing.name = entry.name
        existing.description = entry.description
        existing.transport = entry.mcp.transport
        existing.url = url
        existing.auth_type = "user_provided"
        existing.auth_config = auth_config
        existing.is_enabled = True
        existing.updated_at = now
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing

    row = McpServer(
        slug=entry.slug(),
        name=entry.name,
        description=entry.description,
        scope="instance",
        transport=entry.mcp.transport,
        url=url,
        command=None,
        args=[],
        stdio_env={},
        include_tools=True,
        include_resources=True,
        include_prompts=True,
        auth_type="user_provided",
        auth_config=auth_config,
        origin="catalog",
        catalog_id=entry.catalog_id,
        is_enabled=True,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def sync_catalog_mcp_from_admin(
    session: Session, *, catalog_id: str, org_id: UUID | None = None
) -> McpServer | None:
    entry = get_catalog(catalog_id)
    if not entry or not entry.mcp.url_from:
        return None
    cfg, _, enabled = resolve_effective_admin_config(
        session,
        target_kind="catalog",
        target_id=catalog_id,
        org_id=org_id,
    )
    if not enabled or not catalog_admin_complete(entry, cfg):
        return find_catalog_mcp_server(session, catalog_id)
    url = str(cfg.get(entry.mcp.url_from) or "").strip()
    if not url:
        return find_catalog_mcp_server(session, catalog_id)
    return ensure_catalog_mcp_server(session, entry, mcp_url=url)


def connect_bearer_api_key(
    session: Session,
    *,
    user_id: UUID,
    org_id: UUID,
    catalog_id: str,
    api_key: str,
) -> tuple[McpServer, McpUserConnection]:
    from app.services.integrations.store import upsert_user_install

    entry = get_catalog(catalog_id)
    if not entry:
        raise ValueError("Unknown catalog integration")
    if entry.connect_kind != "bearer_api_key":
        raise ValueError("This integration does not use API key connect")
    if not is_catalog_enabled(session, catalog_id=catalog_id, org_id=org_id):
        raise ValueError("Integration is not enabled for this organisation")

    cfg, _, _ = resolve_effective_admin_config(
        session, target_kind="catalog", target_id=catalog_id, org_id=org_id
    )
    if not catalog_admin_complete(entry, cfg):
        raise ValueError("Integration is not configured")
    url_key = entry.mcp.url_from or "mcp_url"
    url = str(cfg.get(url_key) or "").strip()
    if not url:
        raise ValueError("MCP URL is not configured")

    key = api_key.strip()
    if not key:
        raise ValueError("api_key is required")

    from app.services.integrations.fingerprint import credential_fingerprint

    server = ensure_catalog_mcp_server(session, entry, mcp_url=url)
    connection = store_user_token_connection(
        session,
        user_id=user_id,
        server_id=server.id,
        method="bearer",
        token=key,
    )
    upsert_user_install(
        session,
        user_id=user_id,
        org_id=org_id,
        catalog_id=catalog_id,
        credentials={"method": "bearer", "api_key": key},
        status="connected",
        credential_fingerprint=credential_fingerprint(key),
    )
    return server, connection


def connect_oauth(
    session: Session,
    *,
    user_id: UUID,
    org_id: UUID,
    catalog_id: str,
    token_payload: dict[str, Any],
) -> tuple[McpServer, McpUserConnection]:
    from app.services.integrations.oauth import (
        oauth_provider_ready,
        token_expires_at,
    )
    from app.services.integrations.store import upsert_user_install
    from app.services.mcp.auth_config import encrypt_auth_config

    entry = get_catalog(catalog_id)
    if not entry:
        raise ValueError("Unknown catalog integration")
    if entry.connect_kind != "oauth":
        raise ValueError("This integration does not use OAuth")
    if not entry.oauth_provider_id:
        raise ValueError("OAuth provider is not configured for this integration")
    if not is_catalog_enabled(session, catalog_id=catalog_id, org_id=org_id):
        raise ValueError("Integration is not enabled for this organisation")
    if not oauth_provider_ready(
        session, provider_id=entry.oauth_provider_id, org_id=org_id
    ):
        raise ValueError("OAuth provider is not configured")

    cfg, _, _ = resolve_effective_admin_config(
        session, target_kind="catalog", target_id=catalog_id, org_id=org_id
    )
    if not catalog_admin_complete(entry, cfg):
        raise ValueError("Integration is not configured")
    url_key = entry.mcp.url_from or "mcp_url"
    url = str(cfg.get(url_key) or "").strip()
    if not url:
        raise ValueError("MCP URL is not configured")

    access_token = str(token_payload.get("access_token") or "").strip()
    if not access_token:
        raise ValueError("access_token is required")
    refresh_token = str(token_payload.get("refresh_token") or "").strip() or None
    expires = token_expires_at(token_payload)
    scope = token_payload.get("scope")

    server = ensure_catalog_mcp_server(session, entry, mcp_url=url)
    connection_payload: dict[str, Any] = {
        "method": "bearer",
        "token": access_token,
        "oauth_provider_id": entry.oauth_provider_id,
        "catalog_id": catalog_id,
        "org_id": str(org_id),
        "user_id": str(user_id),
    }
    if refresh_token:
        connection_payload["refresh_token"] = refresh_token
    if expires:
        connection_payload["expires_at"] = expires.isoformat()
    if scope:
        connection_payload["scope"] = scope

    encrypted = encrypt_auth_config(connection_payload)
    if not encrypted:
        raise ValueError("access_token is required")

    existing = session.exec(
        select(McpUserConnection).where(
            McpUserConnection.user_id == user_id,
            McpUserConnection.server_id == server.id,
        )
    ).first()
    now = _now()
    if existing:
        existing.auth_config = encrypted
        existing.status = "connected"
        existing.expires_at = expires.replace(tzinfo=None) if expires else None
        existing.updated_at = now
        session.add(existing)
        session.commit()
        session.refresh(existing)
        connection = existing
    else:
        connection = McpUserConnection(
            user_id=user_id,
            server_id=server.id,
            auth_config=encrypted,
            status="connected",
            expires_at=expires.replace(tzinfo=None) if expires else None,
            created_at=now,
            updated_at=now,
        )
        session.add(connection)
        session.commit()
        session.refresh(connection)

    upsert_user_install(
        session,
        user_id=user_id,
        org_id=org_id,
        catalog_id=catalog_id,
        credentials={
            "method": "oauth",
            "oauth_provider_id": entry.oauth_provider_id,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": expires.isoformat() if expires else None,
            "scope": scope,
        },
        status="connected",
        expires_at=expires.replace(tzinfo=None) if expires else None,
    )
    return server, connection


def disconnect_catalog(
    session: Session, *, user_id: UUID, org_id: UUID, catalog_id: str
) -> None:
    from app.services.integrations.store import delete_user_install

    server = find_catalog_mcp_server(session, catalog_id)
    if server:
        connection = session.exec(
            select(McpUserConnection).where(
                McpUserConnection.user_id == user_id,
                McpUserConnection.server_id == server.id,
            )
        ).first()
        if connection:
            session.delete(connection)
            session.commit()
    delete_user_install(
        session, user_id=user_id, org_id=org_id, catalog_id=catalog_id
    )


def catalog_installable_for_org(
    session: Session, *, catalog_id: str, org_id: UUID
) -> bool:
    entry = get_catalog(catalog_id)
    if not entry:
        return False
    if not is_catalog_enabled(session, catalog_id=catalog_id, org_id=org_id):
        return False
    cfg, _, enabled = resolve_effective_admin_config(
        session, target_kind="catalog", target_id=catalog_id, org_id=org_id
    )
    if not enabled or not catalog_admin_complete(entry, cfg):
        return False
    if entry.oauth_provider_id:
        from app.services.integrations.oauth import oauth_provider_ready

        if not oauth_provider_ready(
            session, provider_id=entry.oauth_provider_id, org_id=org_id
        ):
            return False
    return True


def should_expose_catalog_server(
    session: Session, row: McpServer, *, org_id: UUID
) -> bool:
    """Filter instance catalog MCP servers during resolve."""
    if row.origin != "catalog" or not row.catalog_id:
        return True
    return catalog_installable_for_org(
        session, catalog_id=row.catalog_id, org_id=org_id
    )
