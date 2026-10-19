"""Graph MCP embed JWT audience / multi-tenant issuer handling."""

from __future__ import annotations

from app.services.integrations.graph_mcp_embed import (
    _CONSUMERS_TID,
    _GRAPH_AUDIENCES,
    _install_graph_jwt_patches,
    _is_teams_graph_path,
    _issuers_for_tid,
    _sanitize_graph_get_params,
    _token_is_personal_msa,
)


def test_issuers_for_tid() -> None:
    tid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    issuers = _issuers_for_tid(tid)
    assert f"https://login.microsoftonline.com/{tid}/v2.0" in issuers
    assert f"https://sts.windows.net/{tid}/" in issuers


def test_to_auth_config_includes_graph_app_id_audience() -> None:
    _install_graph_jwt_patches()
    from ms_graph_mcp.config import GraphMcpConfig

    cfg = GraphMcpConfig(
        client_id="my-client",
        tenant_id="common",
        resource_url="http://127.0.0.1:8000/internal/graph-mcp/mcp",
    )
    auth = cfg.to_auth_config()
    audiences = set(auth.audience_list)
    assert "https://graph.microsoft.com" in audiences
    assert "00000003-0000-0000-c000-000000000000" in audiences
    assert "https://graph.microsoft.com" in _GRAPH_AUDIENCES
    assert "common/discovery" in auth.effective_jwks_url


def test_opaque_access_token_accepted() -> None:
    _install_graph_jwt_patches()
    from ms_graph_mcp.config import GraphMcpConfig
    from ms_graph_mcp.entra import jwt_verify

    cfg = GraphMcpConfig(
        client_id="my-client",
        tenant_id="common",
        resource_url="http://127.0.0.1:8000/internal/graph-mcp/mcp",
    )
    auth = cfg.to_auth_config()
    principal = jwt_verify.verify_token("not-a-jwt", auth)
    assert principal.subject_id == "opaque"
    assert principal.azp == "my-client"


def test_graph_token_accepted_when_signature_unverifiable() -> None:
    """Graph access tokens are opaque to clients; claim checks still apply."""
    import base64
    import json
    import time

    _install_graph_jwt_patches()
    from ms_graph_mcp.config import GraphMcpConfig
    from ms_graph_mcp.entra import jwt_verify

    def _b64(data: dict) -> str:
        raw = json.dumps(data, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    tid = "be3ecb34-5ecb-4785-8e64-e64fdf757fc5"
    token = ".".join(
        [
            _b64({"alg": "RS256", "typ": "JWT", "kid": "missing"}),
            _b64(
                {
                    "aud": "https://graph.microsoft.com",
                    "iss": f"https://sts.windows.net/{tid}/",
                    "tid": tid,
                    "azp": "my-client",
                    "scp": "Mail.Read",
                    "exp": int(time.time()) + 3600,
                    "oid": "user-1",
                }
            ),
            "not-a-real-signature",
        ]
    )
    cfg = GraphMcpConfig(
        client_id="my-client",
        tenant_id="common",
        resource_url="http://127.0.0.1:8000/internal/graph-mcp/mcp",
    )
    auth = cfg.to_auth_config()
    principal = jwt_verify.verify_token(token, auth)
    assert principal.azp == "my-client"
    assert principal.tenant_id == tid


def test_personal_msa_teams_detection() -> None:
    import base64
    import json

    def _b64(data: dict) -> str:
        raw = json.dumps(data, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    sig = base64.urlsafe_b64encode(b"sig").rstrip(b"=").decode()
    personal = ".".join(
        [
            _b64({"alg": "none"}),
            _b64({"tid": _CONSUMERS_TID, "aud": "https://graph.microsoft.com"}),
            sig,
        ]
    )
    work = ".".join(
        [
            _b64({"alg": "none"}),
            _b64(
                {
                    "tid": "be3ecb34-5ecb-4785-8e64-e64fdf757fc5",
                    "aud": "https://graph.microsoft.com",
                }
            ),
            sig,
        ]
    )
    assert _token_is_personal_msa(personal)
    assert not _token_is_personal_msa(work)
    assert _is_teams_graph_path("/me/chats")
    assert _is_teams_graph_path("/me/joinedTeams")
    assert not _is_teams_graph_path("/me/messages")


def test_todo_graph_get_strips_select() -> None:
    """Personal Graph rejects $select on /me/todo — strip it before the request."""
    params = _sanitize_graph_get_params(
        "/me/todo/lists",
        {"$select": "id,displayName", "$top": 10},
    )
    assert "$select" not in params
    assert params.get("$top") == 10


def test_search_graph_get_adds_consistency_level() -> None:
    params = _sanitize_graph_get_params("/me/people", {"$search": "alice"})
    assert (params.get("headers") or {}).get("ConsistencyLevel") == "eventual"


def test_catalog_contacts_scopes_include_people_and_user() -> None:
    from app.services.integrations.catalog import OUTLOOK_CONTACTS, TODO

    assert any(s.endswith("/User.Read") for s in OUTLOOK_CONTACTS.oauth_scopes)
    assert any(s.endswith("/People.Read") for s in OUTLOOK_CONTACTS.oauth_scopes)
    assert any(s.endswith("/Tasks.ReadWrite") for s in TODO.oauth_scopes)
