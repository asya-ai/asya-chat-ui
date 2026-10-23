"""OAuth connect + token refresh for catalog integrations."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import httpx
from sqlmodel import Session

from app.core.security import create_access_token, decode_access_token_claims
from app.services.integrations.catalog import CatalogIntegration, get_catalog
from app.services.integrations.providers import OAuthProviderDef, get_oauth_provider
from app.services.integrations.store import resolve_effective_admin_config
from app.services.mcp.auth_config import decrypt_auth_config, encrypt_auth_config

logger = logging.getLogger(__name__)

_OAUTH_STATE_PURPOSE = "integration_oauth"
_REFRESH_SKEW = timedelta(minutes=5)


def resolve_provider_config(
    session: Session, *, provider_id: str, org_id: UUID | None
) -> tuple[OAuthProviderDef, dict[str, Any]]:
    provider = get_oauth_provider(provider_id)
    if not provider:
        raise ValueError("Unknown OAuth provider")
    cfg, _, enabled = resolve_effective_admin_config(
        session,
        target_kind="oauth_provider",
        target_id=provider_id,
        org_id=org_id,
    )
    if not enabled or not cfg:
        raise ValueError("OAuth provider is not configured")
    missing = provider.validate_config(
        {
            **cfg,
            **{
                f"{f.key}_set": bool(cfg.get(f.key))
                for f in provider.fields
                if f.secret
            },
        }
    )
    if missing:
        raise ValueError(f"OAuth provider missing: {', '.join(missing)}")
    return provider, cfg


def oauth_provider_ready(
    session: Session, *, provider_id: str, org_id: UUID | None
) -> bool:
    try:
        resolve_provider_config(session, provider_id=provider_id, org_id=org_id)
        return True
    except ValueError:
        return False


def encode_oauth_state(
    *,
    user_id: UUID,
    org_id: UUID,
    catalog_id: str,
    redirect_base: str,
    catalog_ids: list[str] | None = None,
) -> str:
    ids = [str(x) for x in (catalog_ids or [catalog_id]) if str(x).strip()]
    if not ids:
        ids = [catalog_id]
    if catalog_id not in ids:
        ids = [catalog_id, *ids]
    return create_access_token(
        str(user_id),
        expires_minutes=15,
        extra_claims={
            "purpose": _OAUTH_STATE_PURPOSE,
            "org_id": str(org_id),
            "catalog_id": catalog_id,
            "catalog_ids": ids,
            "redirect_base": redirect_base.rstrip("/"),
        },
    )


def decode_oauth_state(state: str) -> dict[str, Any]:
    claims = decode_access_token_claims(state)
    if claims.get("purpose") != _OAUTH_STATE_PURPOSE:
        raise ValueError("Invalid OAuth state")
    user_id = claims.get("sub")
    org_id = claims.get("org_id")
    catalog_id = claims.get("catalog_id")
    redirect_base = claims.get("redirect_base")
    if not user_id or not org_id or not catalog_id or not redirect_base:
        raise ValueError("Invalid OAuth state")
    raw_ids = claims.get("catalog_ids")
    if isinstance(raw_ids, list) and raw_ids:
        catalog_ids = [str(x) for x in raw_ids if str(x).strip()]
    else:
        catalog_ids = [str(catalog_id)]
    if str(catalog_id) not in catalog_ids:
        catalog_ids = [str(catalog_id), *catalog_ids]
    return {
        "user_id": UUID(str(user_id)),
        "org_id": UUID(str(org_id)),
        "catalog_id": str(catalog_id),
        "catalog_ids": catalog_ids,
        "redirect_base": str(redirect_base).rstrip("/"),
    }


def _drop_redundant_readonly(scopes: list[str]) -> list[str]:
    """Drop Foo.readonly / Foo.Read when a broader write scope is already present."""
    present = set(scopes)
    out: list[str] = []
    for scope in scopes:
        if scope.endswith(".readonly"):
            broader = scope[: -len(".readonly")]
            if broader in present:
                continue
        if scope.endswith(".Read") and not scope.endswith(".ReadWrite"):
            write = f"{scope}Write"
            if write in present:
                continue
        if scope.endswith(".Read.All"):
            write = scope[: -len(".Read.All")] + ".ReadWrite.All"
            if write in present:
                continue
        out.append(scope)
    return out


def union_oauth_scopes(entries: list[CatalogIntegration]) -> list[str]:
    """Deduped scopes across catalog cards (order preserved).

    Broader scopes win: e.g. drive drops drive.readonly; Calendars.ReadWrite
    drops Calendars.Read when both appear from different catalog cards.
    """
    seen: set[str] = set()
    out: list[str] = []
    for entry in entries:
        for scope in entry.oauth_scopes:
            if scope in seen:
                continue
            seen.add(scope)
            out.append(scope)
    return _drop_redundant_readonly(out)


def build_authorize_url(
    session: Session,
    *,
    entry: CatalogIntegration,
    org_id: UUID,
    state: str,
    scopes: list[str] | tuple[str, ...] | None = None,
) -> str:
    if not entry.oauth_provider_id:
        raise ValueError("Integration does not use OAuth")
    provider, cfg = resolve_provider_config(
        session, provider_id=entry.oauth_provider_id, org_id=org_id
    )
    resolved = list(scopes) if scopes else list(entry.oauth_scopes)
    if not resolved:
        resolved = list(provider.default_scopes)
    return provider.authorize_url(cfg, state=state, scopes=resolved)


def exchange_authorization_code(
    provider: OAuthProviderDef,
    config: dict[str, Any],
    *,
    code: str,
    scopes: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    token_url = provider.token_url(config)
    data = {
        "client_id": str(config.get("client_id") or "").strip(),
        "client_secret": str(config.get("client_secret") or "").strip(),
        "code": code.strip(),
        "redirect_uri": str(config.get("redirect_uri") or "").strip(),
        "grant_type": "authorization_code",
    }
    if scopes:
        data["scope"] = " ".join(scopes)
    with httpx.Client(timeout=15) as client:
        response = client.post(token_url, data=data)
        if response.status_code >= 400:
            logger.warning(
                "OAuth token exchange failed (%s): %s",
                response.status_code,
                response.text[:500],
            )
            raise ValueError("OAuth token exchange failed")
        payload = response.json()
    if not isinstance(payload, dict) or not payload.get("access_token"):
        raise ValueError("OAuth token exchange failed")
    return payload


def refresh_oauth_tokens(
    provider: OAuthProviderDef,
    config: dict[str, Any],
    *,
    refresh_token: str,
    scopes: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    token_url = provider.token_url(config)
    data = {
        "client_id": str(config.get("client_id") or "").strip(),
        "client_secret": str(config.get("client_secret") or "").strip(),
        "refresh_token": refresh_token.strip(),
        "grant_type": "refresh_token",
    }
    if scopes:
        data["scope"] = " ".join(scopes)
    with httpx.Client(timeout=15) as client:
        response = client.post(token_url, data=data)
        if response.status_code >= 400:
            logger.warning(
                "OAuth token refresh failed (%s): %s",
                response.status_code,
                response.text[:500],
            )
            raise ValueError("OAuth token refresh failed")
        payload = response.json()
    if not isinstance(payload, dict) or not payload.get("access_token"):
        raise ValueError("OAuth token refresh failed")
    return payload


def token_expires_at(token_payload: dict[str, Any]) -> datetime | None:
    expires_in = token_payload.get("expires_in")
    try:
        seconds = int(expires_in)
    except (TypeError, ValueError):
        return None
    return datetime.now(timezone.utc) + timedelta(seconds=max(seconds, 0))


def connection_needs_refresh(auth_config: str | None) -> bool:
    data = decrypt_auth_config(auth_config)
    if not data.get("refresh_token"):
        return False
    raw = data.get("expires_at")
    if not raw:
        return True
    try:
        expires = datetime.fromisoformat(str(raw))
    except ValueError:
        return True
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return expires <= datetime.now(timezone.utc) + _REFRESH_SKEW


def refresh_user_connection_tokens(
    session: Session,
    *,
    auth_config: str | None,
) -> str | None:
    """Refresh OAuth bearer on an MCP user connection when near expiry.

    Returns updated encrypted auth_config, or the original when no refresh needed.
    """
    data = decrypt_auth_config(auth_config)
    if data.get("method") != "bearer" or not data.get("refresh_token"):
        return auth_config
    if not connection_needs_refresh(auth_config):
        return auth_config

    provider_id = str(data.get("oauth_provider_id") or "").strip()
    org_raw = data.get("org_id")
    if not provider_id or not org_raw:
        return auth_config
    try:
        org_id = UUID(str(org_raw))
    except ValueError:
        return auth_config

    try:
        provider, cfg = resolve_provider_config(
            session, provider_id=provider_id, org_id=org_id
        )
        scope_raw = data.get("scope")
        scopes: list[str] | None = None
        if isinstance(scope_raw, str) and scope_raw.strip():
            scopes = scope_raw.split()
        catalog_id = str(data.get("catalog_id") or "").strip()
        if not scopes and catalog_id:
            entry = get_catalog(catalog_id)
            if entry and entry.oauth_scopes:
                scopes = list(entry.oauth_scopes)
        payload = refresh_oauth_tokens(
            provider,
            cfg,
            refresh_token=str(data["refresh_token"]),
            scopes=scopes,
        )
    except ValueError:
        logger.warning("Failed to refresh OAuth token for provider %s", provider_id)
        return auth_config

    data["token"] = str(payload["access_token"])
    if payload.get("refresh_token"):
        data["refresh_token"] = str(payload["refresh_token"])
    expires = token_expires_at(payload)
    if expires:
        data["expires_at"] = expires.isoformat()

    catalog_id = str(data.get("catalog_id") or "").strip()
    user_id_raw = data.get("user_id")
    if catalog_id and user_id_raw and org_raw:
        try:
            from app.services.integrations.store import upsert_user_install

            upsert_user_install(
                session,
                user_id=UUID(str(user_id_raw)),
                org_id=org_id,
                catalog_id=catalog_id,
                credentials={
                    "method": "oauth",
                    "oauth_provider_id": provider_id,
                    "access_token": data["token"],
                    "refresh_token": data.get("refresh_token"),
                    "expires_at": data.get("expires_at"),
                    "scope": payload.get("scope") or data.get("scope"),
                },
                status="connected",
                expires_at=expires.replace(tzinfo=None) if expires else None,
            )
        except Exception:
            logger.exception("Failed to persist refreshed OAuth install")

    return encrypt_auth_config(data)


def entry_for_oauth(catalog_id: str) -> CatalogIntegration:
    entry = get_catalog(catalog_id)
    if not entry:
        raise ValueError("Unknown catalog integration")
    if entry.connect_kind != "oauth":
        raise ValueError("This integration does not use OAuth")
    if not entry.oauth_provider_id:
        raise ValueError("OAuth provider is not configured for this integration")
    return entry
