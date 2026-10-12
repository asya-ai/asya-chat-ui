"""Persistence helpers for integration admin configs, enablement, and installs."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlmodel import Session, select

from app.models import (
    IntegrationAdminConfig,
    IntegrationCatalogEnablement,
    IntegrationUserInstall,
)
from app.services.integrations.catalog import (
    CatalogIntegration,
    get_catalog,
    list_catalog,
)
from app.services.integrations.providers import list_oauth_providers
from app.services.mcp.auth_config import decrypt_auth_config, encrypt_auth_config


def _now() -> datetime:
    return datetime.utcnow()


def _get_admin_row(
    session: Session,
    *,
    target_kind: str,
    target_id: str,
    scope: str,
    org_id: UUID | None,
) -> IntegrationAdminConfig | None:
    stmt = select(IntegrationAdminConfig).where(
        IntegrationAdminConfig.target_kind == target_kind,
        IntegrationAdminConfig.target_id == target_id,
        IntegrationAdminConfig.scope == scope,
    )
    if org_id is None:
        stmt = stmt.where(IntegrationAdminConfig.org_id.is_(None))
    else:
        stmt = stmt.where(IntegrationAdminConfig.org_id == org_id)
    return session.exec(stmt).first()


def get_admin_config_row(
    session: Session,
    *,
    target_kind: str,
    target_id: str,
    scope: str,
    org_id: UUID | None = None,
) -> IntegrationAdminConfig | None:
    return _get_admin_row(
        session, target_kind=target_kind, target_id=target_id, scope=scope, org_id=org_id
    )


def decrypt_admin_config(row: IntegrationAdminConfig | None) -> dict[str, Any]:
    if not row:
        return {}
    return decrypt_auth_config(row.config)


def upsert_admin_config(
    session: Session,
    *,
    target_kind: str,
    target_id: str,
    scope: str,
    org_id: UUID | None,
    is_enabled: bool | None = None,
    config_updates: dict[str, Any] | None = None,
    secret_keys: set[str] | None = None,
) -> IntegrationAdminConfig:
    if scope == "org" and org_id is None:
        raise ValueError("org_id is required for org scope")
    if scope == "instance" and org_id is not None:
        raise ValueError("org_id must be null for instance scope")

    row = _get_admin_row(
        session, target_kind=target_kind, target_id=target_id, scope=scope, org_id=org_id
    )
    existing = decrypt_admin_config(row)
    secrets = secret_keys or set()
    merged = dict(existing)
    if config_updates:
        for key, value in config_updates.items():
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                # Empty string clears non-secrets; leave secrets unchanged.
                if key in secrets:
                    continue
                merged.pop(key, None)
                continue
            merged[key] = value.strip() if isinstance(value, str) else value

    encrypted = encrypt_auth_config(merged) if merged else None
    now = _now()
    if row:
        if is_enabled is not None:
            row.is_enabled = is_enabled
        row.config = encrypted
        row.updated_at = now
        session.add(row)
        session.commit()
        session.refresh(row)
        return row

    row = IntegrationAdminConfig(
        target_kind=target_kind,
        target_id=target_id,
        scope=scope,
        org_id=org_id,
        is_enabled=True if is_enabled is None else is_enabled,
        config=encrypted,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def resolve_effective_admin_config(
    session: Session,
    *,
    target_kind: str,
    target_id: str,
    org_id: UUID | None,
) -> tuple[dict[str, Any], str | None, bool]:
    """Return (config_dict, source_scope, is_enabled).

    Org complete+enabled wins; else instance; else empty.
    """
    if org_id is not None:
        org_row = _get_admin_row(
            session,
            target_kind=target_kind,
            target_id=target_id,
            scope="org",
            org_id=org_id,
        )
        if org_row and org_row.is_enabled:
            cfg = decrypt_admin_config(org_row)
            if cfg:
                return cfg, "org", True

    inst_row = _get_admin_row(
        session,
        target_kind=target_kind,
        target_id=target_id,
        scope="instance",
        org_id=None,
    )
    if inst_row and inst_row.is_enabled:
        cfg = decrypt_admin_config(inst_row)
        if cfg:
            return cfg, "instance", True

    return {}, None, False


def admin_config_public(
    *,
    target_kind: str,
    target_id: str,
    scope: str,
    row: IntegrationAdminConfig | None,
    field_defs: list[dict[str, Any]],
) -> dict[str, Any]:
    raw = decrypt_admin_config(row)
    values: dict[str, Any] = {}
    secret_set: dict[str, bool] = {}
    for field in field_defs:
        key = field["key"]
        if field.get("secret"):
            secret_set[f"{key}_set"] = bool(raw.get(key))
        else:
            values[key] = raw.get(key)
            if values[key] is None and field.get("default") is not None and row is None:
                values[key] = field["default"]
    return {
        "target_kind": target_kind,
        "target_id": target_id,
        "scope": scope,
        "org_id": str(row.org_id) if row and row.org_id else None,
        "is_enabled": row.is_enabled if row else False,
        "configured": bool(row and row.config),
        "values": values,
        **secret_set,
    }


def _enablement_row(
    session: Session, *, catalog_id: str, scope: str, org_id: UUID | None
) -> IntegrationCatalogEnablement | None:
    stmt = select(IntegrationCatalogEnablement).where(
        IntegrationCatalogEnablement.catalog_id == catalog_id,
        IntegrationCatalogEnablement.scope == scope,
    )
    if org_id is None:
        stmt = stmt.where(IntegrationCatalogEnablement.org_id.is_(None))
    else:
        stmt = stmt.where(IntegrationCatalogEnablement.org_id == org_id)
    return session.exec(stmt).first()


def upsert_enablement(
    session: Session,
    *,
    catalog_id: str,
    scope: str,
    org_id: UUID | None,
    is_enabled: bool,
) -> IntegrationCatalogEnablement:
    if scope == "org" and org_id is None:
        raise ValueError("org_id is required for org scope")
    if scope == "instance" and org_id is not None:
        raise ValueError("org_id must be null for instance scope")
    row = _enablement_row(session, catalog_id=catalog_id, scope=scope, org_id=org_id)
    now = _now()
    if row:
        row.is_enabled = is_enabled
        row.updated_at = now
        session.add(row)
        session.commit()
        session.refresh(row)
        return row
    row = IntegrationCatalogEnablement(
        catalog_id=catalog_id,
        scope=scope,
        org_id=org_id,
        is_enabled=is_enabled,
        updated_at=now,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def is_catalog_enabled(
    session: Session, *, catalog_id: str, org_id: UUID | None
) -> bool:
    """Org enablement overrides instance when present; default False until enabled."""
    if org_id is not None:
        org_row = _enablement_row(
            session, catalog_id=catalog_id, scope="org", org_id=org_id
        )
        if org_row is not None:
            return org_row.is_enabled
    inst_row = _enablement_row(
        session, catalog_id=catalog_id, scope="instance", org_id=None
    )
    if inst_row is not None:
        return inst_row.is_enabled
    return False


def catalog_admin_complete(
    entry: CatalogIntegration, config: dict[str, Any]
) -> bool:
    for field in entry.admin_config_fields:
        if not field.required:
            continue
        value = config.get(field.key)
        if value is None or (isinstance(value, str) and not value.strip()):
            if field.default and not config:
                # No stored config: defaults alone can satisfy for display, but
                # require an explicit saved config before installable.
                return False
            return False
    return True


def get_user_install(
    session: Session, *, user_id: UUID, org_id: UUID, catalog_id: str
) -> IntegrationUserInstall | None:
    return session.exec(
        select(IntegrationUserInstall).where(
            IntegrationUserInstall.user_id == user_id,
            IntegrationUserInstall.org_id == org_id,
            IntegrationUserInstall.catalog_id == catalog_id,
        )
    ).first()


def upsert_user_install(
    session: Session,
    *,
    user_id: UUID,
    org_id: UUID,
    catalog_id: str,
    credentials: dict[str, Any],
    status: str = "connected",
    expires_at: datetime | None = None,
    credential_fingerprint: str | None = None,
) -> IntegrationUserInstall:
    encrypted = encrypt_auth_config(credentials)
    if not encrypted:
        raise ValueError("credentials are required")
    existing = get_user_install(
        session, user_id=user_id, org_id=org_id, catalog_id=catalog_id
    )
    now = _now()
    if existing:
        existing.credentials = encrypted
        existing.status = status
        existing.expires_at = expires_at
        existing.updated_at = now
        if credential_fingerprint is not None:
            existing.credential_fingerprint = credential_fingerprint
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing
    row = IntegrationUserInstall(
        user_id=user_id,
        org_id=org_id,
        catalog_id=catalog_id,
        credentials=encrypted,
        credential_fingerprint=credential_fingerprint,
        status=status,
        expires_at=expires_at,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def find_install_by_fingerprint(
    session: Session,
    *,
    catalog_id: str,
    fingerprint: str,
    org_id: UUID | None = None,
) -> IntegrationUserInstall | None:
    stmt = select(IntegrationUserInstall).where(
        IntegrationUserInstall.catalog_id == catalog_id,
        IntegrationUserInstall.credential_fingerprint == fingerprint,
        IntegrationUserInstall.status == "connected",
    )
    if org_id is not None:
        stmt = stmt.where(IntegrationUserInstall.org_id == org_id)
    return session.exec(stmt).first()


def delete_user_install(
    session: Session, *, user_id: UUID, org_id: UUID, catalog_id: str
) -> None:
    row = get_user_install(
        session, user_id=user_id, org_id=org_id, catalog_id=catalog_id
    )
    if not row:
        return
    session.delete(row)
    session.commit()


def build_catalog_status(
    session: Session,
    *,
    org_id: UUID | None,
    user_id: UUID | None,
) -> list[dict[str, Any]]:
    from app.services.integrations.catalog import catalog_schema

    results: list[dict[str, Any]] = []
    for entry in list_catalog():
        cfg, source, cfg_enabled = resolve_effective_admin_config(
            session,
            target_kind="catalog",
            target_id=entry.catalog_id,
            org_id=org_id,
        )
        # Apply field defaults for completeness check when config exists partially
        effective = dict(cfg)
        for field in entry.admin_config_fields:
            if field.default and not effective.get(field.key):
                # Don't invent URL for installability without saved config
                pass
        enabled = is_catalog_enabled(
            session, catalog_id=entry.catalog_id, org_id=org_id
        )
        configured = bool(cfg) and catalog_admin_complete(entry, cfg)
        if entry.oauth_provider_id and org_id is not None:
            from app.services.integrations.oauth import oauth_provider_ready

            configured = configured and oauth_provider_ready(
                session, provider_id=entry.oauth_provider_id, org_id=org_id
            )
        elif entry.oauth_provider_id and org_id is None:
            # Without an org context, provider readiness is unknown — treat as incomplete.
            configured = False

        install = None
        if user_id and org_id:
            install = get_user_install(
                session,
                user_id=user_id,
                org_id=org_id,
                catalog_id=entry.catalog_id,
            )
        connected = bool(install and install.status == "connected")
        installable = enabled and configured and bool(org_id)
        results.append(
            {
                **catalog_schema(entry),
                "enabled": enabled,
                "configured": configured,
                "config_source": source,
                "config_enabled": cfg_enabled,
                "installable": installable,
                "connected": connected,
                "connection_status": install.status if install else "not_connected",
                "effective_mcp_url": (
                    cfg.get(entry.mcp.url_from)
                    if entry.mcp.url_from and cfg.get(entry.mcp.url_from)
                    else None
                ),
                "allowed_embed_origins": cfg.get("allowed_embed_origins") or "",
            }
        )
    return results


def list_provider_public(
    session: Session, *, scope: str, org_id: UUID | None
) -> list[dict[str, Any]]:
    from app.services.integrations.providers import provider_schema

    out: list[dict[str, Any]] = []
    for provider in list_oauth_providers():
        row = get_admin_config_row(
            session,
            target_kind="oauth_provider",
            target_id=provider.provider_id,
            scope=scope,
            org_id=org_id,
        )
        schema = provider_schema(provider)
        public = admin_config_public(
            target_kind="oauth_provider",
            target_id=provider.provider_id,
            scope=scope,
            row=row,
            field_defs=schema["fields"],
        )
        out.append({**schema, **public})
    return out
