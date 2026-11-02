"""Keep IdP tokens fresh server-side; browser holds a long-lived app JWT."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Any

import httpx
from fastapi import HTTPException, status
from sqlmodel import Session

from app.core.secret_crypto import decrypt_secret
from app.models import OidcSession, Org

logger = logging.getLogger(__name__)

_ACCESS_REFRESH_SKEW = timedelta(seconds=60)

_OIDC_CONFIG_CACHE: dict[str, dict[str, Any]] = {}
_OIDC_CONFIG_EXPIRES: dict[str, float] = {}
_OIDC_CONFIG_TTL_SECONDS = 3600


def _cache_get(key: str) -> dict[str, Any] | None:
    expires_at = _OIDC_CONFIG_EXPIRES.get(key)
    if expires_at is None or expires_at <= time.time():
        _OIDC_CONFIG_CACHE.pop(key, None)
        _OIDC_CONFIG_EXPIRES.pop(key, None)
        return None
    return _OIDC_CONFIG_CACHE.get(key)


def _cache_set(key: str, value: dict[str, Any]) -> None:
    _OIDC_CONFIG_CACHE[key] = value
    _OIDC_CONFIG_EXPIRES[key] = time.time() + _OIDC_CONFIG_TTL_SECONDS


def _normalize_oidc_config_url(value: str) -> str:
    trimmed = value.strip()
    if "/.well-known/openid-configuration" in trimmed:
        return trimmed
    return trimmed.rstrip("/") + "/.well-known/openid-configuration"


def _get_oidc_config_sync(issuer_or_config: str) -> dict[str, Any]:
    config_url = _normalize_oidc_config_url(issuer_or_config)
    cached = _cache_get(config_url)
    if cached is not None:
        return cached
    with httpx.Client(timeout=10) as client:
        response = client.get(config_url)
        response.raise_for_status()
        config = response.json()
    _cache_set(config_url, config)
    return config


def _oidc_client_auth_methods(
    *,
    oidc_client_id: str,
    oidc_client_secret: str,
    supported_methods: list[str],
) -> list[tuple[str, dict[str, Any]]]:
    auth_methods: list[tuple[str, dict[str, Any]]] = []
    if not supported_methods or "client_secret_basic" in supported_methods:
        if oidc_client_secret:
            auth_methods.append(
                (
                    "basic",
                    {
                        "auth": (oidc_client_id, oidc_client_secret),
                        "data": {
                            "client_id": oidc_client_id,
                            "client_secret": oidc_client_secret,
                        },
                    },
                )
            )
    if not supported_methods or "client_secret_post" in supported_methods:
        auth_methods.append(
            (
                "post",
                {
                    "data": {
                        "client_id": oidc_client_id,
                        "client_secret": oidc_client_secret,
                    }
                },
            )
        )
    if not supported_methods or "none" in supported_methods:
        auth_methods.append(
            (
                "none",
                {
                    "data": {
                        "client_id": oidc_client_id,
                    }
                },
            )
        )
    return auth_methods


def _post_oidc_token_sync(
    *,
    token_endpoint: str,
    form_data: dict[str, str],
    oidc_client_id: str,
    oidc_client_secret: str,
    supported_methods: list[str],
) -> dict[str, Any]:
    auth_methods = _oidc_client_auth_methods(
        oidc_client_id=oidc_client_id,
        oidc_client_secret=oidc_client_secret,
        supported_methods=supported_methods,
    )
    token_payload: dict[str, Any] | None = None
    last_exc: httpx.HTTPError | None = None
    with httpx.Client(timeout=10) as client:
        for label, extra in auth_methods:
            try:
                response = client.post(
                    token_endpoint,
                    data={**form_data, **(extra.get("data") or {})},
                    **{k: v for k, v in extra.items() if k != "data"},
                )
                response.raise_for_status()
                token_payload = response.json()
                break
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                logger.error(
                    "OIDC token request failed (%s): %s",
                    label,
                    exc.response.text,
                    exc_info=True,
                )
            except httpx.RequestError as exc:
                last_exc = exc
                logger.error(
                    "OIDC token request failed (%s): %s",
                    label,
                    exc,
                    exc_info=True,
                )
    if token_payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
        ) from last_exc
    return token_payload


def _oidc_token_error_code(exc: BaseException | None) -> str | None:
    if not isinstance(exc, httpx.HTTPStatusError):
        return None
    try:
        body = exc.response.json()
    except Exception:
        return None
    if not isinstance(body, dict):
        return None
    err = body.get("error")
    return str(err) if err else None


def _invalidate_oidc_session(db: Session, oidc_session: OidcSession) -> None:
    db.delete(oidc_session)
    db.commit()


def _access_still_valid(oidc_session: OidcSession, *, now: datetime) -> bool:
    access_expires = oidc_session.access_expires_at
    if access_expires.tzinfo is not None:
        access_expires = access_expires.replace(tzinfo=None)
    return access_expires > now + _ACCESS_REFRESH_SKEW


def ensure_oidc_session_fresh(db: Session, oidc_session: OidcSession) -> None:
    """Refresh IdP tokens when needed; drop session when the grant is dead."""
    now = datetime.utcnow()
    if oidc_session.refresh_expires_at:
        refresh_expires = oidc_session.refresh_expires_at
        if refresh_expires.tzinfo is not None:
            refresh_expires = refresh_expires.replace(tzinfo=None)
        if refresh_expires <= now:
            _invalidate_oidc_session(db, oidc_session)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication credentials",
            )

    if _access_still_valid(oidc_session, now=now):
        return

    refresh = decrypt_secret(oidc_session.refresh_token)
    if not refresh:
        _invalidate_oidc_session(db, oidc_session)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
        )

    org = db.get(Org, oidc_session.org_id)
    if not org or not org.oidc_enabled:
        _invalidate_oidc_session(db, oidc_session)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
        )

    oidc_issuer = (org.oidc_issuer or "").strip()
    oidc_client_id = (org.oidc_client_id or "").strip()
    oidc_client_secret = (decrypt_secret(org.oidc_client_secret) or "").strip()
    config = _get_oidc_config_sync(oidc_issuer)
    token_endpoint = config.get("token_endpoint")
    if not token_endpoint:
        _invalidate_oidc_session(db, oidc_session)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
        )
    supported_methods = config.get("token_endpoint_auth_methods_supported") or []
    if isinstance(supported_methods, list):
        supported_methods = [str(method) for method in supported_methods]
    else:
        supported_methods = []

    used_refresh_enc = oidc_session.refresh_token
    session_id = oidc_session.id
    try:
        from app.api.auth import _persist_oidc_session

        token_payload = _post_oidc_token_sync(
            token_endpoint=token_endpoint,
            form_data={
                "grant_type": "refresh_token",
                "refresh_token": refresh,
            },
            oidc_client_id=oidc_client_id,
            oidc_client_secret=oidc_client_secret,
            supported_methods=supported_methods,
        )
        _persist_oidc_session(
            db,
            user_id=oidc_session.user_id,
            org_id=org.id,
            token_payload=token_payload,
            existing=oidc_session,
        )
    except HTTPException as exc:
        db.expire(oidc_session)
        latest = db.get(OidcSession, session_id)
        if (
            latest
            and latest.refresh_token
            and latest.refresh_token != used_refresh_enc
            and _access_still_valid(latest, now=datetime.utcnow())
        ):
            return
        err = _oidc_token_error_code(exc.__cause__)
        if latest and err in {"invalid_grant", "invalid_token"}:
            _invalidate_oidc_session(db, latest)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
        ) from exc
