from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.models import (
    McpSettings,
    Org,
    OrgMembership,
    Role,
    User,
)
from app.services.integrations.catalog import get_catalog, list_catalog
from app.services.integrations.materialize import (
    connect_bearer_api_key,
    connect_oauth,
    disconnect_catalog,
    find_catalog_mcp_server,
    should_expose_catalog_server,
)
from app.services.integrations.oauth import (
    build_authorize_url,
    connection_needs_refresh,
    decode_oauth_state,
    encode_oauth_state,
    exchange_authorization_code,
    refresh_user_connection_tokens,
    union_oauth_scopes,
)
from app.services.integrations.providers import get_oauth_provider, list_oauth_providers
from app.services.integrations.store import (
    build_catalog_status,
    is_catalog_enabled,
    resolve_effective_admin_config,
    upsert_admin_config,
    upsert_enablement,
)
from app.services.mcp.auth_config import encrypt_auth_config, headers_from_user_connection
from app.services.mcp.registry import resolve_auth_headers, resolve_effective_mcp_servers


def _session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _seed(session: Session) -> tuple[Org, User, User]:
    org = Org(name=f"Org-{uuid4().hex[:8]}")
    session.add(org)
    session.commit()
    session.refresh(org)

    admin = User(email=f"admin-{uuid4().hex[:8]}@example.com", hashed_password="x")
    member = User(email=f"member-{uuid4().hex[:8]}@example.com", hashed_password="x")
    session.add(admin)
    session.add(member)
    session.commit()
    session.refresh(admin)
    session.refresh(member)

    admin_role = Role(org_id=org.id, name="admin", is_default=False)
    member_role = Role(org_id=org.id, name="member", is_default=True)
    session.add(admin_role)
    session.add(member_role)
    session.commit()
    session.refresh(admin_role)
    session.refresh(member_role)

    session.add(OrgMembership(org_id=org.id, user_id=admin.id, role_id=admin_role.id))
    session.add(OrgMembership(org_id=org.id, user_id=member.id, role_id=member_role.id))
    session.add(McpSettings(id=1, allow_org_servers=False, allow_user_servers=False))
    session.commit()
    return org, admin, member


def test_catalog_includes_pitchpatterns() -> None:
    ids = {entry.catalog_id for entry in list_catalog()}
    assert "pitchpatterns" in ids
    entry = get_catalog("pitchpatterns")
    assert entry is not None
    assert entry.connect_kind == "bearer_api_key"
    assert entry.mcp.url_from == "mcp_url"


def test_oauth_providers_registered() -> None:
    ids = {p.provider_id for p in list_oauth_providers()}
    assert ids == {"microsoft"}
    ms = get_oauth_provider("microsoft")
    assert ms is not None
    by_key = {f.key: f for f in ms.fields}
    assert by_key["redirect_uri"].readonly is True
    assert by_key["tenant_id"].required is False
    url = ms.authorize_url(
        {"client_id": "abc", "tenant_id": "common", "redirect_uri": "https://x/cb"},
        state="s",
        scopes=["openid"],
    )
    assert "login.microsoftonline.com" in url


def test_pitchpatterns_config_enable_connect_resolve() -> None:
    session = _session()
    org, _admin, member = _seed(session)

    upsert_admin_config(
        session,
        target_kind="catalog",
        target_id="pitchpatterns",
        scope="instance",
        org_id=None,
        is_enabled=True,
        config_updates={"mcp_url": "http://host.docker.internal:9020/mcp"},
    )
    upsert_enablement(
        session,
        catalog_id="pitchpatterns",
        scope="instance",
        org_id=None,
        is_enabled=True,
    )
    assert is_catalog_enabled(session, catalog_id="pitchpatterns", org_id=org.id)

    status = build_catalog_status(session, org_id=org.id, user_id=member.id)
    pp = next(item for item in status if item["catalog_id"] == "pitchpatterns")
    assert pp["configured"] is True
    assert pp["enabled"] is True
    assert pp["installable"] is True
    assert pp["connected"] is False

    server, _conn = connect_bearer_api_key(
        session,
        user_id=member.id,
        org_id=org.id,
        catalog_id="pitchpatterns",
        api_key="pp-secret",
    )
    assert server.origin == "catalog"
    assert server.catalog_id == "pitchpatterns"
    assert server.slug == "catalog-pitchpatterns"
    assert find_catalog_mcp_server(session, "pitchpatterns") is not None
    assert should_expose_catalog_server(session, server, org_id=org.id)

    resolved = resolve_effective_mcp_servers(
        session, org_id=org.id, user_id=member.id
    )
    assert any(r.config.id == "catalog-pitchpatterns" for r in resolved)
    hit = next(r for r in resolved if r.config.id == "catalog-pitchpatterns")
    assert hit.config.url == "http://host.docker.internal:9020/mcp"
    assert hit.config.headers.get("Authorization") == "Bearer pp-secret"

    # Org URL override applies at resolve time
    upsert_admin_config(
        session,
        target_kind="catalog",
        target_id="pitchpatterns",
        scope="org",
        org_id=org.id,
        is_enabled=True,
        config_updates={"mcp_url": "http://pitchpatterns.internal/mcp"},
    )
    cfg, source, _ = resolve_effective_admin_config(
        session, target_kind="catalog", target_id="pitchpatterns", org_id=org.id
    )
    assert source == "org"
    assert cfg["mcp_url"] == "http://pitchpatterns.internal/mcp"

    resolved2 = resolve_effective_mcp_servers(
        session, org_id=org.id, user_id=member.id
    )
    hit2 = next(r for r in resolved2 if r.config.id == "catalog-pitchpatterns")
    assert hit2.config.url == "http://pitchpatterns.internal/mcp"

    disconnect_catalog(
        session, user_id=member.id, org_id=org.id, catalog_id="pitchpatterns"
    )
    resolved3 = resolve_effective_mcp_servers(
        session, org_id=org.id, user_id=member.id
    )
    assert not any(r.config.id == "catalog-pitchpatterns" for r in resolved3)


def test_embed_session_fingerprint_lookup() -> None:
    from fastapi import HTTPException

    from app.api.integrations import EmbedSessionBody, create_embed_session
    from app.services.integrations.fingerprint import credential_fingerprint
    from app.services.integrations.store import find_install_by_fingerprint

    session = _session()
    org, _admin, member = _seed(session)
    upsert_admin_config(
        session,
        target_kind="catalog",
        target_id="pitchpatterns",
        scope="instance",
        org_id=None,
        is_enabled=True,
        config_updates={
            "mcp_url": "http://host.docker.internal:9020/mcp",
            "allowed_embed_origins": "https://pp.example",
        },
    )
    upsert_enablement(
        session,
        catalog_id="pitchpatterns",
        scope="instance",
        org_id=None,
        is_enabled=True,
    )
    connect_bearer_api_key(
        session,
        user_id=member.id,
        org_id=org.id,
        catalog_id="pitchpatterns",
        api_key="pp-embed-key",
    )
    install = find_install_by_fingerprint(
        session,
        catalog_id="pitchpatterns",
        fingerprint=credential_fingerprint("pp-embed-key"),
        org_id=org.id,
    )
    assert install is not None
    assert install.user_id == member.id
    assert find_install_by_fingerprint(
        session,
        catalog_id="pitchpatterns",
        fingerprint=credential_fingerprint("wrong-key"),
    ) is None

    session_out = create_embed_session(
        "pitchpatterns",
        EmbedSessionBody(api_key="pp-embed-key", org_id=str(org.id)),
        session,
    )
    assert session_out["org_id"] == str(org.id)
    assert session_out["user_id"] == str(member.id)
    assert session_out["access_token"]
    assert "https://pp.example" in session_out["allowed_embed_origins"]

    with pytest.raises(HTTPException) as exc:
        create_embed_session(
            "pitchpatterns",
            EmbedSessionBody(api_key="unknown-key"),
            session,
        )
    assert exc.value.status_code == 401


def test_catalog_not_exposed_when_disabled() -> None:
    session = _session()
    org, _admin, member = _seed(session)
    upsert_admin_config(
        session,
        target_kind="catalog",
        target_id="pitchpatterns",
        scope="instance",
        org_id=None,
        is_enabled=True,
        config_updates={"mcp_url": "http://host.docker.internal:9020/mcp"},
    )
    upsert_enablement(
        session,
        catalog_id="pitchpatterns",
        scope="org",
        org_id=org.id,
        is_enabled=False,
    )
    with pytest.raises(ValueError, match="not enabled"):
        connect_bearer_api_key(
            session,
            user_id=member.id,
            org_id=org.id,
            catalog_id="pitchpatterns",
            api_key="x",
        )


_MS_CATALOG_IDS = {
    "outlook",
    "outlook_calendar",
    "outlook_contacts",
    "onedrive",
    "sharepoint",
    "teams",
    "todo",
}


def test_microsoft_catalog_apps_registered() -> None:
    from app.services.integrations.catalog import (
        graph_mcp_request_headers,
        resolve_catalog_mcp_url,
    )

    ids = {entry.catalog_id for entry in list_catalog()}
    assert _MS_CATALOG_IDS.issubset(ids)
    for catalog_id in _MS_CATALOG_IDS:
        entry = get_catalog(catalog_id)
        assert entry is not None
        assert entry.connect_kind == "oauth"
        assert entry.oauth_provider_id == "microsoft"
        assert "offline_access" in entry.oauth_scopes
        assert entry.mcp.url_from is None
        assert entry.admin_config_fields == ()
        from app.services.integrations.graph_mcp_embed import embedded_graph_mcp_url

        assert resolve_catalog_mcp_url(entry, {}) == embedded_graph_mcp_url()
        headers = graph_mcp_request_headers(catalog_id)
        assert headers.get("X-Write-Scope") == "true"
        assert headers.get("X-Toolsets")


def _configure_microsoft(session: Session, org_id) -> None:
    upsert_admin_config(
        session,
        target_kind="oauth_provider",
        target_id="microsoft",
        scope="instance",
        org_id=None,
        is_enabled=True,
        config_updates={
            "client_id": "ms-client",
            "client_secret": "ms-secret",
            "tenant_id": "common",
            "redirect_uri": "https://chat.example/api/integrations/oauth/callback",
        },
        secret_keys={"client_secret"},
    )
    for catalog_id in _MS_CATALOG_IDS:
        upsert_enablement(
            session,
            catalog_id=catalog_id,
            scope="instance",
            org_id=None,
            is_enabled=True,
        )


def test_microsoft_connect_all_scopes_and_state() -> None:
    session = _session()
    org, _admin, member = _seed(session)
    _configure_microsoft(session, org.id)

    entries = [
        e
        for e in list_catalog()
        if e.oauth_provider_id == "microsoft" and e.connect_kind == "oauth"
    ]
    scopes = union_oauth_scopes(entries)
    assert any(s.endswith("/Mail.Read") for s in scopes)
    assert any(s.endswith("/Tasks.ReadWrite") for s in scopes)
    assert any(s.endswith("/User.Read") for s in scopes)
    assert len(scopes) == len(set(scopes))

    catalog_ids = [e.catalog_id for e in entries]
    state = encode_oauth_state(
        user_id=member.id,
        org_id=org.id,
        catalog_id=catalog_ids[0],
        catalog_ids=catalog_ids,
        redirect_base="https://chat.example",
    )
    decoded = decode_oauth_state(state)
    assert decoded["catalog_ids"] == catalog_ids

    authorize_url = build_authorize_url(
        session,
        entry=entries[0],
        org_id=org.id,
        state=state,
        scopes=scopes,
    )
    assert "login.microsoftonline.com" in authorize_url
    assert "Tasks.ReadWrite" in authorize_url or "Tasks.ReadWrite" in authorize_url.replace(
        "%2F", "/"
    )

    token_payload = {
        "access_token": "ms-all-access",
        "refresh_token": "ms-all-refresh",
        "expires_in": 3600,
        "scope": " ".join(scopes),
    }
    for catalog_id in catalog_ids:
        connect_oauth(
            session,
            user_id=member.id,
            org_id=org.id,
            catalog_id=catalog_id,
            token_payload=token_payload,
        )
    status = build_catalog_status(session, org_id=org.id, user_id=member.id)
    for catalog_id in catalog_ids:
        row = next(item for item in status if item["catalog_id"] == catalog_id)
        assert row["connected"] is True


def test_outlook_oauth_connect_resolve_disconnect() -> None:
    session = _session()
    org, _admin, member = _seed(session)
    _configure_microsoft(session, org.id)

    status = build_catalog_status(session, org_id=org.id, user_id=member.id)
    outlook = next(item for item in status if item["catalog_id"] == "outlook")
    assert outlook["configured"] is True
    assert outlook["enabled"] is True
    assert outlook["installable"] is True
    assert outlook["connected"] is False

    entry = get_catalog("outlook")
    assert entry is not None
    state = encode_oauth_state(
        user_id=member.id,
        org_id=org.id,
        catalog_id="outlook",
        redirect_base="https://chat.example",
    )
    decoded = decode_oauth_state(state)
    assert decoded["catalog_id"] == "outlook"
    assert decoded["catalog_ids"] == ["outlook"]
    assert decoded["user_id"] == member.id

    authorize_url = build_authorize_url(
        session, entry=entry, org_id=org.id, state=state
    )
    assert "login.microsoftonline.com" in authorize_url
    assert "Mail.Read" in authorize_url or "Mail.Read" in authorize_url.replace("%2F", "/")

    server, _conn = connect_oauth(
        session,
        user_id=member.id,
        org_id=org.id,
        catalog_id="outlook",
        token_payload={
            "access_token": "ms-access",
            "refresh_token": "ms-refresh",
            "expires_in": 3600,
            "scope": "Mail.Read",
        },
    )
    assert server.catalog_id == "outlook"
    assert find_catalog_mcp_server(session, "outlook") is not None
    assert should_expose_catalog_server(session, server, org_id=org.id)

    resolved = resolve_effective_mcp_servers(
        session, org_id=org.id, user_id=member.id
    )
    from app.services.integrations.graph_mcp_embed import embedded_graph_mcp_url

    hit = next(r for r in resolved if r.config.id == "catalog-outlook")
    assert hit.config.url == embedded_graph_mcp_url()
    assert hit.config.headers.get("Authorization") == "Bearer ms-access"
    assert hit.config.headers.get("X-Toolsets") == "mail"
    assert hit.config.headers.get("X-Write-Scope") == "true"

    status2 = build_catalog_status(session, org_id=org.id, user_id=member.id)
    outlook2 = next(item for item in status2 if item["catalog_id"] == "outlook")
    assert outlook2["connected"] is True

    disconnect_catalog(session, user_id=member.id, org_id=org.id, catalog_id="outlook")
    resolved2 = resolve_effective_mcp_servers(
        session, org_id=org.id, user_id=member.id
    )
    assert not any(r.config.id == "catalog-outlook" for r in resolved2)


def test_oauth_token_exchange_and_refresh() -> None:
    session = _session()
    org, _admin, member = _seed(session)
    _configure_microsoft(session, org.id)

    provider = get_oauth_provider("microsoft")
    assert provider is not None
    cfg = {
        "client_id": "ms-client",
        "client_secret": "ms-secret",
        "tenant_id": "common",
        "redirect_uri": "https://chat.example/api/integrations/oauth/callback",
    }

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "access_token": "exchanged-access",
        "refresh_token": "exchanged-refresh",
        "expires_in": 3600,
    }
    mock_response.raise_for_status = MagicMock()

    with patch("app.services.integrations.oauth.httpx.Client") as client_cls:
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        client.post.return_value = mock_response
        client_cls.return_value = client
        payload = exchange_authorization_code(provider, cfg, code="auth-code")
        assert payload["access_token"] == "exchanged-access"

    server, connection = connect_oauth(
        session,
        user_id=member.id,
        org_id=org.id,
        catalog_id="onedrive",
        token_payload={
            "access_token": "old-access",
            "refresh_token": "old-refresh",
            "expires_in": 1,
        },
    )
    # Force expiry in the past so refresh triggers.
    expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    stale = encrypt_auth_config(
        {
            "method": "bearer",
            "token": "old-access",
            "refresh_token": "old-refresh",
            "expires_at": expired,
            "oauth_provider_id": "microsoft",
            "catalog_id": "onedrive",
            "org_id": str(org.id),
            "user_id": str(member.id),
        }
    )
    connection.auth_config = stale
    session.add(connection)
    session.commit()
    session.refresh(connection)
    assert connection_needs_refresh(connection.auth_config)

    refresh_response = MagicMock()
    refresh_response.status_code = 200
    refresh_response.json.return_value = {
        "access_token": "new-access",
        "refresh_token": "new-refresh",
        "expires_in": 3600,
    }

    with patch("app.services.integrations.oauth.httpx.Client") as client_cls:
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        client.post.return_value = refresh_response
        client_cls.return_value = client
        refreshed = refresh_user_connection_tokens(
            session, auth_config=connection.auth_config
        )

    assert refreshed is not None
    headers = headers_from_user_connection(refreshed)
    assert headers["Authorization"] == "Bearer new-access"

    connection.auth_config = refreshed
    session.add(connection)
    session.commit()

    from app.services.mcp.registry import ResolvedMcpServer
    from app.services.mcp.types import McpServerConfig

    resolved_stub = ResolvedMcpServer(
        db_id=server.id,
        config=McpServerConfig(
            id=server.slug,
            name=server.name,
            transport=server.transport,  # type: ignore[arg-type]
            enabled=True,
            url=server.url,
        ),
        auth_type="user_provided",
        auth_fingerprint="x",
    )
    # Make tokens look expired again so resolve_auth_headers refreshes.
    connection.auth_config = encrypt_auth_config(
        {
            "method": "bearer",
            "token": "stale",
            "refresh_token": "new-refresh",
            "expires_at": expired,
            "oauth_provider_id": "microsoft",
            "catalog_id": "onedrive",
            "org_id": str(org.id),
            "user_id": str(member.id),
        }
    )
    session.add(connection)
    session.commit()

    with patch("app.services.integrations.oauth.httpx.Client") as client_cls:
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        client.post.return_value = refresh_response
        client_cls.return_value = client
        headers2 = resolve_auth_headers(
            session, resolved_stub, org_id=org.id, user_id=member.id
        )
    assert headers2["Authorization"] == "Bearer new-access"


def test_oauth_app_not_installable_without_provider() -> None:
    session = _session()
    org, _admin, member = _seed(session)
    upsert_enablement(
        session,
        catalog_id="teams",
        scope="instance",
        org_id=None,
        is_enabled=True,
    )
    status = build_catalog_status(session, org_id=org.id, user_id=member.id)
    teams = next(item for item in status if item["catalog_id"] == "teams")
    assert teams["configured"] is False
    assert teams["installable"] is False
