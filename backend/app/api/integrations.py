"""Catalog integrations + OAuth provider admin config APIs."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.api.deps import get_current_user, get_db
from app.models import User
from app.services.integrations.catalog import get_catalog, list_catalog
from app.services.integrations.materialize import (
    connect_bearer_api_key,
    connect_oauth,
    disconnect_catalog,
    find_catalog_mcp_server,
    sync_catalog_mcp_from_admin,
)
from app.services.integrations.oauth import (
    build_authorize_url,
    decode_oauth_state,
    encode_oauth_state,
    entry_for_oauth,
    exchange_authorization_code,
    resolve_provider_config,
    union_oauth_scopes,
)
from app.services.integrations.providers import get_oauth_provider, list_oauth_providers, provider_schema
from app.services.integrations.store import (
    admin_config_public,
    build_catalog_status,
    catalog_admin_complete,
    get_admin_config_row,
    is_catalog_enabled,
    list_provider_public,
    resolve_effective_admin_config,
    upsert_admin_config,
    upsert_enablement,
)
from app.services.mcp.registry import resolve_test_auth
from app.services.mcp.store import test_server_connection
from app.services.org_service import (
    require_org_admin,
    require_org_member,
    require_super_admin,
)

router = APIRouter(tags=["integrations"])


def _parse_uuid(value: str, *, field: str = "id") -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid {field}"
        ) from exc


def _http_error(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


def _ensure_org_admin_or_super(session: Session, org_id: UUID, user: User) -> None:
    if user.is_super_admin:
        return
    require_org_admin(session, org_id, user.id)


def _build_frontend_base(request: Request) -> str:
    origin = request.headers.get("origin")
    if origin:
        return origin.rstrip("/")
    forwarded_proto = request.headers.get("x-forwarded-proto")
    forwarded_host = request.headers.get("x-forwarded-host") or request.headers.get(
        "host"
    )
    if forwarded_host:
        scheme = forwarded_proto or request.url.scheme
        base = f"{scheme}://{forwarded_host}"
        forwarded_prefix = request.headers.get("x-forwarded-prefix")
        if forwarded_prefix:
            base = base.rstrip("/") + "/" + forwarded_prefix.strip("/")
        return base.rstrip("/")
    base = str(request.base_url).rstrip("/")
    if base.endswith("/api"):
        base = base[: -len("/api")]
    return base.rstrip("/")


def _integrations_redirect(
    redirect_base: str, *, status_value: str, catalog_id: str | None = None, detail: str | None = None
) -> RedirectResponse:
    params: dict[str, str] = {"oauth": status_value}
    if catalog_id:
        params["catalog_id"] = catalog_id
    if detail:
        params["detail"] = detail[:200]
    url = f"{redirect_base.rstrip('/')}/settings/integrations?{urlencode(params)}"
    return RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)


class AdminConfigUpdate(BaseModel):
    is_enabled: bool | None = None
    values: dict[str, Any] = Field(default_factory=dict)


class EnablementUpdate(BaseModel):
    is_enabled: bool
    scope: Literal["instance", "org"] = "org"


class ConnectApiKeyBody(BaseModel):
    api_key: str
    org_id: str


class EmbedSessionBody(BaseModel):
    api_key: str
    org_id: str | None = None


class IntegrationTestResult(BaseModel):
    status: str
    detail: str | None = None
    latency_ms: float | None = None
    tools: int | None = None
    resources: int | None = None
    prompts: int | None = None


@router.get("/integrations/catalog")
def list_catalog_status(
    org_id: str | None = Query(default=None),
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    org_uuid: UUID | None = None
    if org_id:
        org_uuid = _parse_uuid(org_id, field="org_id")
        require_org_member(
            session,
            org_uuid,
            current_user.id,
            is_super_admin=current_user.is_super_admin,
        )
    return build_catalog_status(
        session, org_id=org_uuid, user_id=current_user.id
    )


@router.get("/integrations/providers")
def list_provider_schemas(
    _: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    return [provider_schema(p) for p in list_oauth_providers()]


@router.get("/admin/integrations/providers")
def list_instance_provider_configs(
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    require_super_admin(current_user)
    return list_provider_public(session, scope="instance", org_id=None)


@router.put("/admin/integrations/providers/{provider_id}")
def upsert_instance_provider_config(
    provider_id: str,
    payload: AdminConfigUpdate,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    require_super_admin(current_user)
    provider = get_oauth_provider(provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Unknown provider")
    secret_keys = {f.key for f in provider.fields if f.secret}
    try:
        row = upsert_admin_config(
            session,
            target_kind="oauth_provider",
            target_id=provider_id,
            scope="instance",
            org_id=None,
            is_enabled=payload.is_enabled,
            config_updates=payload.values,
            secret_keys=secret_keys,
        )
    except ValueError as exc:
        raise _http_error(exc) from exc
    if provider_id == "microsoft":
        from app.services.integrations.graph_mcp_embed import (
            invalidate_graph_mcp_embed,
            sync_microsoft_catalog_servers,
        )

        invalidate_graph_mcp_embed()
        sync_microsoft_catalog_servers(session)
    elif provider_id == "google":
        from app.services.integrations.google_mcp_embed import (
            sync_google_catalog_servers,
        )

        sync_google_catalog_servers(session)
    return admin_config_public(
        target_kind="oauth_provider",
        target_id=provider_id,
        scope="instance",
        row=row,
        field_defs=provider_schema(provider)["fields"],
    )


@router.get("/orgs/{org_id}/integrations/providers")
def list_org_provider_configs(
    org_id: str,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    org_uuid = _parse_uuid(org_id, field="org_id")
    _ensure_org_admin_or_super(session, org_uuid, current_user)
    return list_provider_public(session, scope="org", org_id=org_uuid)


@router.put("/orgs/{org_id}/integrations/providers/{provider_id}")
def upsert_org_provider_config(
    org_id: str,
    provider_id: str,
    payload: AdminConfigUpdate,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    org_uuid = _parse_uuid(org_id, field="org_id")
    _ensure_org_admin_or_super(session, org_uuid, current_user)
    provider = get_oauth_provider(provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Unknown provider")
    secret_keys = {f.key for f in provider.fields if f.secret}
    try:
        row = upsert_admin_config(
            session,
            target_kind="oauth_provider",
            target_id=provider_id,
            scope="org",
            org_id=org_uuid,
            is_enabled=payload.is_enabled,
            config_updates=payload.values,
            secret_keys=secret_keys,
        )
    except ValueError as exc:
        raise _http_error(exc) from exc
    return admin_config_public(
        target_kind="oauth_provider",
        target_id=provider_id,
        scope="org",
        row=row,
        field_defs=provider_schema(provider)["fields"],
    )


def _catalog_field_defs(catalog_id: str) -> list[dict[str, Any]]:
    entry = get_catalog(catalog_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Unknown catalog integration")
    return [
        {
            "key": f.key,
            "label": f.label,
            "required": f.required,
            "secret": f.secret,
            "placeholder": f.placeholder,
            "help_text": f.help_text,
            "default": f.default,
        }
        for f in entry.admin_config_fields
    ]


@router.get("/admin/integrations/catalog/{catalog_id}/config")
def get_instance_catalog_config(
    catalog_id: str,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    require_super_admin(current_user)
    if not get_catalog(catalog_id):
        raise HTTPException(status_code=404, detail="Unknown catalog integration")
    row = get_admin_config_row(
        session,
        target_kind="catalog",
        target_id=catalog_id,
        scope="instance",
        org_id=None,
    )
    return admin_config_public(
        target_kind="catalog",
        target_id=catalog_id,
        scope="instance",
        row=row,
        field_defs=_catalog_field_defs(catalog_id),
    )


@router.put("/admin/integrations/catalog/{catalog_id}/config")
def upsert_instance_catalog_config(
    catalog_id: str,
    payload: AdminConfigUpdate,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    require_super_admin(current_user)
    entry = get_catalog(catalog_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Unknown catalog integration")
    secret_keys = {f.key for f in entry.admin_config_fields if f.secret}
    # Fill defaults for missing required fields on first save
    values = dict(payload.values)
    for field in entry.admin_config_fields:
        if field.default and field.key not in values:
            values.setdefault(field.key, field.default)
    try:
        row = upsert_admin_config(
            session,
            target_kind="catalog",
            target_id=catalog_id,
            scope="instance",
            org_id=None,
            is_enabled=payload.is_enabled if payload.is_enabled is not None else True,
            config_updates=values,
            secret_keys=secret_keys,
        )
        sync_catalog_mcp_from_admin(session, catalog_id=catalog_id, org_id=None)
    except ValueError as exc:
        raise _http_error(exc) from exc
    return admin_config_public(
        target_kind="catalog",
        target_id=catalog_id,
        scope="instance",
        row=row,
        field_defs=_catalog_field_defs(catalog_id),
    )


@router.get("/orgs/{org_id}/integrations/catalog/{catalog_id}/config")
def get_org_catalog_config(
    org_id: str,
    catalog_id: str,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    org_uuid = _parse_uuid(org_id, field="org_id")
    _ensure_org_admin_or_super(session, org_uuid, current_user)
    if not get_catalog(catalog_id):
        raise HTTPException(status_code=404, detail="Unknown catalog integration")
    row = get_admin_config_row(
        session,
        target_kind="catalog",
        target_id=catalog_id,
        scope="org",
        org_id=org_uuid,
    )
    return admin_config_public(
        target_kind="catalog",
        target_id=catalog_id,
        scope="org",
        row=row,
        field_defs=_catalog_field_defs(catalog_id),
    )


@router.put("/orgs/{org_id}/integrations/catalog/{catalog_id}/config")
def upsert_org_catalog_config(
    org_id: str,
    catalog_id: str,
    payload: AdminConfigUpdate,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    org_uuid = _parse_uuid(org_id, field="org_id")
    _ensure_org_admin_or_super(session, org_uuid, current_user)
    entry = get_catalog(catalog_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Unknown catalog integration")
    secret_keys = {f.key for f in entry.admin_config_fields if f.secret}
    try:
        row = upsert_admin_config(
            session,
            target_kind="catalog",
            target_id=catalog_id,
            scope="org",
            org_id=org_uuid,
            is_enabled=payload.is_enabled if payload.is_enabled is not None else True,
            config_updates=payload.values,
            secret_keys=secret_keys,
        )
        sync_catalog_mcp_from_admin(session, catalog_id=catalog_id, org_id=org_uuid)
    except ValueError as exc:
        raise _http_error(exc) from exc
    return admin_config_public(
        target_kind="catalog",
        target_id=catalog_id,
        scope="org",
        row=row,
        field_defs=_catalog_field_defs(catalog_id),
    )


@router.put("/admin/integrations/catalog/{catalog_id}/enablement")
def set_instance_enablement(
    catalog_id: str,
    payload: EnablementUpdate,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    require_super_admin(current_user)
    if not get_catalog(catalog_id):
        raise HTTPException(status_code=404, detail="Unknown catalog integration")
    try:
        row = upsert_enablement(
            session,
            catalog_id=catalog_id,
            scope="instance",
            org_id=None,
            is_enabled=payload.is_enabled,
        )
        if payload.is_enabled:
            sync_catalog_mcp_from_admin(session, catalog_id=catalog_id, org_id=None)
    except ValueError as exc:
        raise _http_error(exc) from exc
    return {
        "catalog_id": catalog_id,
        "scope": "instance",
        "is_enabled": row.is_enabled,
    }


@router.put("/orgs/{org_id}/integrations/catalog/{catalog_id}/enablement")
def set_org_enablement(
    org_id: str,
    catalog_id: str,
    payload: EnablementUpdate,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    org_uuid = _parse_uuid(org_id, field="org_id")
    _ensure_org_admin_or_super(session, org_uuid, current_user)
    if not get_catalog(catalog_id):
        raise HTTPException(status_code=404, detail="Unknown catalog integration")
    try:
        row = upsert_enablement(
            session,
            catalog_id=catalog_id,
            scope="org",
            org_id=org_uuid,
            is_enabled=payload.is_enabled,
        )
    except ValueError as exc:
        raise _http_error(exc) from exc
    return {
        "catalog_id": catalog_id,
        "scope": "org",
        "org_id": org_id,
        "is_enabled": row.is_enabled,
    }


@router.post("/integrations/catalog/{catalog_id}/connect")
def connect_catalog(
    catalog_id: str,
    payload: ConnectApiKeyBody,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    org_uuid = _parse_uuid(payload.org_id, field="org_id")
    require_org_member(
        session,
        org_uuid,
        current_user.id,
        is_super_admin=current_user.is_super_admin,
    )
    entry = get_catalog(catalog_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Unknown catalog integration")
    if entry.connect_kind != "bearer_api_key":
        raise HTTPException(
            status_code=400,
            detail="This integration does not support API key connect",
        )
    try:
        server, _conn = connect_bearer_api_key(
            session,
            user_id=current_user.id,
            org_id=org_uuid,
            catalog_id=catalog_id,
            api_key=payload.api_key,
        )
    except ValueError as exc:
        raise _http_error(exc) from exc
    return {
        "catalog_id": catalog_id,
        "status": "connected",
        "server_id": str(server.id),
        "server_slug": server.slug,
    }


def _oauth_entries_for_provider(
    session: Session, *, provider_id: str, org_id: UUID
) -> list:
    """Enabled + admin-complete OAuth catalog cards for one provider."""
    out = []
    for entry in list_catalog():
        if entry.connect_kind != "oauth":
            continue
        if entry.oauth_provider_id != provider_id:
            continue
        if not is_catalog_enabled(
            session, catalog_id=entry.catalog_id, org_id=org_id
        ):
            continue
        cfg, _, _ = resolve_effective_admin_config(
            session,
            target_kind="catalog",
            target_id=entry.catalog_id,
            org_id=org_id,
        )
        if not catalog_admin_complete(entry, cfg):
            continue
        out.append(entry)
    return out


@router.get("/integrations/catalog/{catalog_id}/oauth/start")
def start_catalog_oauth(
    catalog_id: str,
    request: Request,
    org_id: str = Query(...),
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    org_uuid = _parse_uuid(org_id, field="org_id")
    require_org_member(
        session,
        org_uuid,
        current_user.id,
        is_super_admin=current_user.is_super_admin,
    )
    try:
        entry = entry_for_oauth(catalog_id)
    except ValueError as exc:
        raise _http_error(exc) from exc
    if not is_catalog_enabled(session, catalog_id=catalog_id, org_id=org_uuid):
        raise HTTPException(
            status_code=400, detail="Integration is not enabled for this organisation"
        )
    redirect_base = _build_frontend_base(request)
    state = encode_oauth_state(
        user_id=current_user.id,
        org_id=org_uuid,
        catalog_id=catalog_id,
        redirect_base=redirect_base,
    )
    try:
        authorize_url = build_authorize_url(
            session, entry=entry, org_id=org_uuid, state=state
        )
    except ValueError as exc:
        raise _http_error(exc) from exc
    return {"authorize_url": authorize_url}


@router.get("/integrations/oauth/providers/{provider_id}/start")
def start_provider_oauth(
    provider_id: str,
    request: Request,
    org_id: str = Query(...),
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    """One consent for every offered catalog card on this OAuth provider."""
    org_uuid = _parse_uuid(org_id, field="org_id")
    require_org_member(
        session,
        org_uuid,
        current_user.id,
        is_super_admin=current_user.is_super_admin,
    )
    if not get_oauth_provider(provider_id):
        raise HTTPException(status_code=404, detail="Unknown OAuth provider")
    entries = _oauth_entries_for_provider(
        session, provider_id=provider_id, org_id=org_uuid
    )
    if not entries:
        raise HTTPException(
            status_code=400,
            detail="No enabled apps for this provider",
        )
    catalog_ids = [e.catalog_id for e in entries]
    scopes = union_oauth_scopes(entries)
    redirect_base = _build_frontend_base(request)
    state = encode_oauth_state(
        user_id=current_user.id,
        org_id=org_uuid,
        catalog_id=catalog_ids[0],
        catalog_ids=catalog_ids,
        redirect_base=redirect_base,
    )
    try:
        authorize_url = build_authorize_url(
            session,
            entry=entries[0],
            org_id=org_uuid,
            state=state,
            scopes=scopes,
        )
    except ValueError as exc:
        raise _http_error(exc) from exc
    return {"authorize_url": authorize_url}


@router.get("/integrations/oauth/callback")
def catalog_oauth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    session: Session = Depends(get_db),
) -> RedirectResponse:
    fallback_base = _build_frontend_base(request)
    if error:
        return _integrations_redirect(
            fallback_base,
            status_value="error",
            detail=error_description or error,
        )
    if not code or not state:
        return _integrations_redirect(
            fallback_base, status_value="error", detail="Invalid OAuth response"
        )
    try:
        state_payload = decode_oauth_state(state)
    except Exception:
        return _integrations_redirect(
            fallback_base, status_value="error", detail="Invalid OAuth state"
        )

    redirect_base = state_payload["redirect_base"] or fallback_base
    catalog_id = state_payload["catalog_id"]
    catalog_ids = state_payload["catalog_ids"]
    org_id = state_payload["org_id"]
    user_id = state_payload["user_id"]

    user = session.get(User, user_id)
    if not user or not user.is_active:
        return _integrations_redirect(
            redirect_base,
            status_value="error",
            catalog_id=catalog_id,
            detail="User not found",
        )

    try:
        entries = [entry_for_oauth(cid) for cid in catalog_ids]
        provider_id = entries[0].oauth_provider_id
        assert provider_id
        provider, cfg = resolve_provider_config(
            session, provider_id=provider_id, org_id=org_id
        )
        token_payload = exchange_authorization_code(
            provider,
            cfg,
            code=code,
            scopes=union_oauth_scopes(entries),
        )
        connected: list[str] = []
        last_error: str | None = None
        for entry in entries:
            try:
                connect_oauth(
                    session,
                    user_id=user_id,
                    org_id=org_id,
                    catalog_id=entry.catalog_id,
                    token_payload=token_payload,
                )
                connected.append(entry.catalog_id)
            except ValueError as exc:
                last_error = str(exc)
        if not connected:
            raise ValueError(last_error or "OAuth connect failed")
        catalog_id = connected[0]
    except ValueError as exc:
        return _integrations_redirect(
            redirect_base,
            status_value="error",
            catalog_id=catalog_id,
            detail=str(exc),
        )
    except Exception:
        return _integrations_redirect(
            redirect_base,
            status_value="error",
            catalog_id=catalog_id,
            detail="OAuth connect failed",
        )

    return _integrations_redirect(
        redirect_base, status_value="connected", catalog_id=catalog_id
    )


@router.post("/integrations/catalog/{catalog_id}/embed/session")
def create_embed_session(
    catalog_id: str,
    payload: EmbedSessionBody,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Exchange a previously Connected API key for a ChatUI access token."""
    from app.core.security import create_access_token
    from app.models import User
    from app.services.integrations.fingerprint import credential_fingerprint
    from app.services.integrations.store import (
        find_install_by_fingerprint,
        resolve_effective_admin_config,
    )

    entry = get_catalog(catalog_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Unknown catalog integration")
    if entry.connect_kind != "bearer_api_key":
        raise HTTPException(
            status_code=400, detail="Embed session is not supported for this integration"
        )

    key = (payload.api_key or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="api_key is required")

    org_uuid: UUID | None = None
    if payload.org_id:
        org_uuid = _parse_uuid(payload.org_id, field="org_id")

    # Optional origin allowlist is enforced on the embed client; if configured empty,
    # still allow session minting so Connect-first auth works.
    cfg, _, cfg_enabled = resolve_effective_admin_config(
        session, target_kind="catalog", target_id=catalog_id, org_id=org_uuid
    )
    if not cfg_enabled:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Connect this integration in ChatUI first",
        )

    install = find_install_by_fingerprint(
        session,
        catalog_id=catalog_id,
        fingerprint=credential_fingerprint(key),
        org_id=org_uuid,
    )
    if not install:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Connect this integration in ChatUI first",
        )

    user = session.get(User, install.user_id)
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Connect this integration in ChatUI first",
        )

    token = create_access_token(str(user.id), token_version=user.token_version)
    return {
        "access_token": token,
        "org_id": str(install.org_id),
        "user_id": str(user.id),
        "allowed_embed_origins": _parse_embed_origins(
            cfg.get("allowed_embed_origins")
        ),
    }


def _parse_embed_origins(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    text = str(raw).strip()
    if not text:
        return []
    parts: list[str] = []
    for chunk in text.replace(";", ",").replace("\n", ",").split(","):
        origin = chunk.strip()
        if origin:
            parts.append(origin)
    return parts


@router.delete("/integrations/catalog/{catalog_id}/install")
def disconnect_catalog_install(
    catalog_id: str,
    org_id: str = Query(...),
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    org_uuid = _parse_uuid(org_id, field="org_id")
    require_org_member(
        session,
        org_uuid,
        current_user.id,
        is_super_admin=current_user.is_super_admin,
    )
    if not get_catalog(catalog_id):
        raise HTTPException(status_code=404, detail="Unknown catalog integration")
    disconnect_catalog(
        session,
        user_id=current_user.id,
        org_id=org_uuid,
        catalog_id=catalog_id,
    )


@router.post(
    "/integrations/catalog/{catalog_id}/test",
    response_model=IntegrationTestResult,
)
async def test_catalog_connection(
    catalog_id: str,
    org_id: str = Query(...),
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> IntegrationTestResult:
    org_uuid = _parse_uuid(org_id, field="org_id")
    require_org_member(
        session,
        org_uuid,
        current_user.id,
        is_super_admin=current_user.is_super_admin,
    )
    entry = get_catalog(catalog_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Unknown catalog integration")
    server = find_catalog_mcp_server(session, catalog_id)
    if not server:
        sync_catalog_mcp_from_admin(session, catalog_id=catalog_id, org_id=org_uuid)
        server = find_catalog_mcp_server(session, catalog_id)
    if not server:
        raise HTTPException(
            status_code=400, detail="Integration MCP server is not configured"
        )
    # Apply org-effective URL for the probe
    from app.services.integrations.catalog import resolve_catalog_mcp_url
    from app.services.integrations.store import resolve_effective_admin_config

    cfg, _, _ = resolve_effective_admin_config(
        session, target_kind="catalog", target_id=catalog_id, org_id=org_uuid
    )
    url = resolve_catalog_mcp_url(entry, cfg)
    if url:
        server.url = url
    auth_type, headers = resolve_test_auth(
        session, server, user_id=current_user.id, org_id=org_uuid
    )
    result = await test_server_connection(server, headers=headers, auth_type=auth_type)
    return IntegrationTestResult(**result)


class WritePreferenceRead(BaseModel):
    provider_id: str
    always_allow: bool


class WritePreferencesStatus(BaseModel):
    always_allow: list[WritePreferenceRead]
    session_chat_ids: list[str]


@router.get(
    "/integrations/orgs/{org_id}/write-preferences",
    response_model=WritePreferencesStatus,
)
def get_write_preferences(
    org_id: str,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> WritePreferencesStatus:
    from app.services.integrations.write_approval import (
        list_always_allow,
        list_session_allows,
    )

    org_uuid = _parse_uuid(org_id, field="org_id")
    require_org_member(
        session,
        org_uuid,
        current_user.id,
        is_super_admin=current_user.is_super_admin,
    )
    rows = list_always_allow(session, user_id=current_user.id, org_id=org_uuid)
    return WritePreferencesStatus(
        always_allow=[
            WritePreferenceRead(provider_id=r.provider_id, always_allow=r.always_allow)
            for r in rows
        ],
        session_chat_ids=list_session_allows(
            org_id=org_uuid, user_id=current_user.id
        ),
    )


@router.delete(
    "/integrations/orgs/{org_id}/write-preferences/{provider_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def revoke_write_preference(
    org_id: str,
    provider_id: str,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    from app.services.integrations.write_approval import revoke_always_allow

    org_uuid = _parse_uuid(org_id, field="org_id")
    require_org_member(
        session,
        org_uuid,
        current_user.id,
        is_super_admin=current_user.is_super_admin,
    )
    if provider_id not in {"google", "microsoft"}:
        raise HTTPException(status_code=400, detail="Unknown provider")
    revoke_always_allow(
        session,
        user_id=current_user.id,
        org_id=org_uuid,
        provider_id=provider_id,
    )


@router.delete(
    "/integrations/orgs/{org_id}/write-session/{chat_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def clear_write_session(
    org_id: str,
    chat_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> None:
    from app.services.integrations.write_approval import clear_session_allow

    org_uuid = _parse_uuid(org_id, field="org_id")
    chat_uuid = _parse_uuid(chat_id, field="chat_id")
    require_org_member(
        session,
        org_uuid,
        current_user.id,
        is_super_admin=current_user.is_super_admin,
    )
    clear_session_allow(
        org_id=org_uuid, user_id=current_user.id, chat_id=chat_uuid
    )
