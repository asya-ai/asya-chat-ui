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
) -> str:
    return create_access_token(
        str(user_id),
        expires_minutes=15,
        extra_claims={
            "purpose": _OAUTH_STATE_PURPOSE,
            "org_id": str(org_id),
            "catalog_id": catalog_id,
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
    return {
        "user_id": UUID(str(user_id)),
        "org_id": UUID(str(org_id)),
        "catalog_id": str(catalog_id),
        "redirect_base": str(redirect_base).rstrip("/"),
    }


def build_authorize_url(
    session: Session,
    *,
    entry: CatalogIntegration,
    org_id: UUID,
    state: str,
) -> str:
    if not entry.oauth_provider_id:
        raise ValueError("Integration does not use OAuth")
    provider, cfg = resolve_provider_config(
        session, provider_id=entry.oauth_provider_id, org_id=org_id
    )
    scopes = list(entry.oauth_scopes) or list(provider.default_scopes)
    return provider.authorize_url(cfg, state=state, scopes=scopes)


def exchange_authorization_code(
    provider: OAuthProviderDef,
    config: dict[str, Any],
    *,
    code: str,
) -> dict[str, Any]:
    token_url = provider.token_url(config)
    data = {
        "client_id": str(config.get("client_id") or "").strip(),
        "client_secret": str(config.get("client_secret") or "").strip(),
        "code": code.strip(),
        "redirect_uri": str(config.get("redirect_uri") or "").strip(),
        "grant_type": "authorization_code",
    }
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
) -> dict[str, Any]:
    token_url = provider.token_url(config)
    data = {
        "client_id": str(config.get("client_id") or "").strip(),
        "client_secret": str(config.get("client_secret") or "").strip(),
        "refresh_token": refresh_token.strip(),
        "grant_type": "refresh_token",
    }
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
        payload = refresh_oauth_tokens(
            provider, cfg, refresh_token=str(data["refresh_token"])
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
