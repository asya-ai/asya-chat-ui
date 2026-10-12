from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.auth import (
    _ensure_offline_access_scope,
    _mint_oidc_app_token,
    _oidc_access_expires_at,
    _persist_oidc_session,
    refresh_token,
)
from app.core.security import create_access_token, decode_access_token_claims
from app.models import OidcSession, Org, User
from starlette.requests import Request


def _session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _seed_user_org(session: Session) -> tuple[User, Org]:
    org = Org(
        name=f"Org-{uuid4().hex[:8]}",
        slug=f"org-{uuid4().hex[:8]}",
        oidc_enabled=True,
        oidc_issuer="https://idp.example.com",
        oidc_client_id="client",
        oidc_client_secret="secret",
    )
    session.add(org)
    session.commit()
    session.refresh(org)
    user = User(
        email=f"user-{uuid4().hex[:8]}@example.com",
        hashed_password="x",
        auth_provider="oidc",
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user, org


def _request_with_auth(token: str) -> Request:
    return Request(
        {
            "type": "http",
            "headers": [(b"authorization", f"Bearer {token}".encode())],
            "method": "POST",
            "path": "/auth/refresh",
        }
    )


def test_ensure_offline_access_scope_appends_when_missing() -> None:
    assert _ensure_offline_access_scope("openid email profile") == (
        "openid email profile offline_access"
    )
    assert _ensure_offline_access_scope("openid offline_access") == "openid offline_access"
    assert _ensure_offline_access_scope("") == "openid offline_access"
    assert _ensure_offline_access_scope(None) == "openid offline_access"


def test_oidc_access_expires_at_prefers_access_expires_in() -> None:
    before = datetime.now(timezone.utc)
    # Access token TTL wins over a longer id_token exp.
    expires = _oidc_access_expires_at(
        {"expires_in": 300},
        {"exp": int(before.timestamp()) + 1800},
    )
    delta = expires - before
    assert timedelta(seconds=295) <= delta <= timedelta(seconds=305)

    # Fall back to id_token exp when expires_in is missing.
    expires_id = _oidc_access_expires_at({}, {"exp": int(before.timestamp()) + 1800})
    delta_id = expires_id - before
    assert timedelta(seconds=1790) <= delta_id <= timedelta(seconds=1810)


def test_mint_oidc_app_token_matches_session_lifetime() -> None:
    with _session() as session:
        user, org = _seed_user_org(session)
        access_expires = datetime.utcnow() + timedelta(seconds=180)
        oidc_session = OidcSession(
            user_id=user.id,
            org_id=org.id,
            access_expires_at=access_expires,
            refresh_token="enc",
        )
        session.add(oidc_session)
        session.commit()
        session.refresh(oidc_session)

        token = _mint_oidc_app_token(user, oidc_session)
        claims = decode_access_token_claims(token)
        assert claims["sid"] == str(oidc_session.id)
        assert claims["sub"] == str(user.id)
        exp = datetime.fromtimestamp(int(claims["exp"]), tz=timezone.utc)
        expected = access_expires.replace(tzinfo=timezone.utc)
        assert abs((exp - expected).total_seconds()) < 2


@pytest.mark.asyncio
async def test_local_login_token_has_no_sid_and_refresh_rejects() -> None:
    with _session() as session:
        user, _org = _seed_user_org(session)
        token = create_access_token(str(user.id), token_version=user.token_version)
        claims = decode_access_token_claims(token)
        assert "sid" not in claims

        with pytest.raises(HTTPException) as exc:
            await refresh_token(_request_with_auth(token), session)
        assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_refresh_succeeds_and_rotates_tokens() -> None:
    with _session() as session:
        user, org = _seed_user_org(session)
        oidc_session = _persist_oidc_session(
            session,
            user_id=user.id,
            org_id=org.id,
            token_payload={
                "access_token": "old-access",
                "refresh_token": "old-refresh",
                "expires_in": 60,
            },
        )
        app_token = _mint_oidc_app_token(user, oidc_session)

        new_payload = {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_in": 120,
        }

        with patch(
            "app.api.auth._get_oidc_config",
            new=AsyncMock(
                return_value={
                    "token_endpoint": "https://idp.example.com/token",
                    "token_endpoint_auth_methods_supported": ["client_secret_post"],
                }
            ),
        ), patch(
            "app.api.auth._post_oidc_token",
            new=AsyncMock(return_value=new_payload),
        ), patch(
            "app.api.auth.decrypt_secret",
            side_effect=lambda value: (
                "old-refresh"
                if value == oidc_session.refresh_token
                else ("secret" if value == org.oidc_client_secret else value)
            ),
        ):
            # Re-load session after persist encrypted values
            oidc_session = session.get(OidcSession, oidc_session.id)
            result = await refresh_token(_request_with_auth(app_token), session)

        assert result.access_token
        claims = decode_access_token_claims(result.access_token)
        assert claims["sid"] == str(oidc_session.id)
        updated = session.get(OidcSession, oidc_session.id)
        assert updated is not None
        # Tokens are encrypted at rest; expiry should advance to ~120s
        remaining = (
            updated.access_expires_at.replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)
        ).total_seconds()
        assert 100 <= remaining <= 130


@pytest.mark.asyncio
async def test_refresh_invalid_grant_deletes_session() -> None:
    with _session() as session:
        user, org = _seed_user_org(session)
        oidc_session = _persist_oidc_session(
            session,
            user_id=user.id,
            org_id=org.id,
            token_payload={
                "access_token": "old-access",
                "refresh_token": "old-refresh",
                "expires_in": 60,
            },
        )
        sid = oidc_session.id
        app_token = _mint_oidc_app_token(user, oidc_session)

        idp_error = httpx.HTTPStatusError(
            "invalid",
            request=httpx.Request("POST", "https://idp.example.com/token"),
            response=httpx.Response(
                400,
                json={"error": "invalid_grant"},
                request=httpx.Request("POST", "https://idp.example.com/token"),
            ),
        )
        wrapped = HTTPException(status_code=400, detail="SSO token exchange failed")
        wrapped.__cause__ = idp_error

        with patch(
            "app.api.auth._get_oidc_config",
            new=AsyncMock(
                return_value={
                    "token_endpoint": "https://idp.example.com/token",
                    "token_endpoint_auth_methods_supported": ["client_secret_post"],
                }
            ),
        ), patch(
            "app.api.auth._post_oidc_token",
            new=AsyncMock(side_effect=wrapped),
        ), patch(
            "app.api.auth.decrypt_secret",
            side_effect=lambda value: (
                "old-refresh"
                if value == oidc_session.refresh_token
                else ("secret" if value == org.oidc_client_secret else value)
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                await refresh_token(_request_with_auth(app_token), session)
            assert exc.value.status_code == 401

        assert session.get(OidcSession, sid) is None


@pytest.mark.asyncio
async def test_refresh_transient_failure_keeps_session() -> None:
    with _session() as session:
        user, org = _seed_user_org(session)
        oidc_session = _persist_oidc_session(
            session,
            user_id=user.id,
            org_id=org.id,
            token_payload={
                "access_token": "old-access",
                "refresh_token": "old-refresh",
                "expires_in": 60,
            },
        )
        sid = oidc_session.id
        app_token = _mint_oidc_app_token(user, oidc_session)

        with patch(
            "app.api.auth._get_oidc_config",
            new=AsyncMock(
                return_value={
                    "token_endpoint": "https://idp.example.com/token",
                    "token_endpoint_auth_methods_supported": ["client_secret_post"],
                }
            ),
        ), patch(
            "app.api.auth._post_oidc_token",
            new=AsyncMock(
                side_effect=HTTPException(status_code=400, detail="SSO token exchange failed")
            ),
        ), patch(
            "app.api.auth.decrypt_secret",
            side_effect=lambda value: (
                "old-refresh"
                if value == oidc_session.refresh_token
                else ("secret" if value == org.oidc_client_secret else value)
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                await refresh_token(_request_with_auth(app_token), session)
            assert exc.value.status_code == 401

        assert session.get(OidcSession, sid) is not None


@pytest.mark.asyncio
async def test_refresh_concurrent_rotation_reuses_session() -> None:
    with _session() as session:
        user, org = _seed_user_org(session)
        oidc_session = _persist_oidc_session(
            session,
            user_id=user.id,
            org_id=org.id,
            token_payload={
                "access_token": "old-access",
                "refresh_token": "old-refresh",
                "expires_in": 60,
            },
        )
        app_token = _mint_oidc_app_token(user, oidc_session)
        original_enc = oidc_session.refresh_token

        async def _fail_then_rotated(*_args, **_kwargs):
            # Simulate another request rotating the refresh token first.
            oidc_session.refresh_token = "enc-rotated"
            oidc_session.access_expires_at = datetime.utcnow() + timedelta(seconds=600)
            session.add(oidc_session)
            session.commit()
            raise HTTPException(status_code=400, detail="SSO token exchange failed")

        with patch(
            "app.api.auth._get_oidc_config",
            new=AsyncMock(
                return_value={
                    "token_endpoint": "https://idp.example.com/token",
                    "token_endpoint_auth_methods_supported": ["client_secret_post"],
                }
            ),
        ), patch(
            "app.api.auth._post_oidc_token",
            new=AsyncMock(side_effect=_fail_then_rotated),
        ), patch(
            "app.api.auth.decrypt_secret",
            side_effect=lambda value: (
                "old-refresh"
                if value == original_enc
                else ("secret" if value == org.oidc_client_secret else value)
            ),
        ):
            result = await refresh_token(_request_with_auth(app_token), session)

        assert result.access_token
        claims = decode_access_token_claims(result.access_token)
        assert claims["sid"] == str(oidc_session.id)
        assert session.get(OidcSession, oidc_session.id) is not None
