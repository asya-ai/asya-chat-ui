from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.api.deps import get_current_user, get_db
from app.models import McpOrgBinding, McpOrgSettings, McpServer, McpUserConnection, User
from app.services.mcp.auth_config import (
    decrypt_auth_config,
)
from app.services.mcp.registry import (
    get_mcp_org_settings,
    get_mcp_settings,
    resolve_test_auth,
    store_user_token_connection,
)
from app.services.mcp.store import (
    build_auth_config_payload,
    create_server,
    delete_server,
    ensure_org_servers_allowed,
    ensure_user_servers_allowed,
    server_to_read,
    test_server_connection,
    update_server,
)
from app.services.org_service import require_org_admin, require_org_member, require_super_admin

router = APIRouter(tags=["mcp"])


class McpSettingsRead(BaseModel):
    allow_org_servers: bool
    allow_user_servers: bool


class McpSettingsUpdate(BaseModel):
    allow_org_servers: bool | None = None
    allow_user_servers: bool | None = None


class McpOrgSettingsRead(BaseModel):
    allow_user_servers: bool


class McpOrgSettingsUpdate(BaseModel):
    allow_user_servers: bool | None = None


class McpServerWrite(BaseModel):
    slug: str
    name: str
    description: str | None = None
    transport: Literal["http", "sse", "stdio"] = "http"
    url: str | None = None
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    stdio_env: dict[str, str] = Field(default_factory=dict)
    include_tools: bool = True
    include_resources: bool = True
    include_prompts: bool = True
    tool_allowlist: list[str] | None = None
    tool_blocklist: list[str] | None = None
    auth_type: Literal["none", "bearer", "api_token", "user_provided"] = "none"
    token: str | None = None
    header_name: str | None = None
    header_format: str | None = None
    user_auth_method: Literal["bearer", "api_token"] | None = None
    user_instructions: str | None = None
    is_enabled: bool = True


class McpServerUpdate(BaseModel):
    slug: str | None = None
    name: str | None = None
    description: str | None = None
    transport: Literal["http", "sse", "stdio"] | None = None
    url: str | None = None
    command: str | None = None
    args: list[str] | None = None
    stdio_env: dict[str, str] | None = None
    include_tools: bool | None = None
    include_resources: bool | None = None
    include_prompts: bool | None = None
    tool_allowlist: list[str] | None = None
    tool_blocklist: list[str] | None = None
    auth_type: Literal["none", "bearer", "api_token", "user_provided"] | None = None
    token: str | None = None
    header_name: str | None = None
    header_format: str | None = None
    user_auth_method: Literal["bearer", "api_token"] | None = None
    user_instructions: str | None = None
    is_enabled: bool | None = None


class McpServerRead(BaseModel):
    id: str
    slug: str
    name: str
    description: str | None = None
    scope: str
    org_id: str | None = None
    user_id: str | None = None
    transport: str
    url: str | None = None
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    stdio_env: dict[str, str] = Field(default_factory=dict)
    include_tools: bool
    include_resources: bool
    include_prompts: bool
    tool_allowlist: list[str] | None = None
    tool_blocklist: list[str] | None = None
    auth_type: str
    token_set: bool
    user_auth_method: str | None = None
    user_instructions: str | None = None
    api_token_header_name: str | None = None
    api_token_header_format: str | None = None
    is_enabled: bool
    connection_status: str | None = None
    created_at: str
    updated_at: str


class McpBindingRead(BaseModel):
    id: str
    org_id: str
    instance_server_id: str
    instance_server_slug: str
    instance_server_name: str
    mode: str
    auth_type: str | None = None
    token_set: bool = False
    user_auth_method: str | None = None


class McpBindingWrite(BaseModel):
    mode: Literal["inherit", "override", "disabled"]
    auth_type: Literal["none", "bearer", "api_token", "user_provided"] | None = None
    token: str | None = None
    header_name: str | None = None
    header_format: str | None = None
    user_auth_method: Literal["bearer", "api_token"] | None = None
    user_instructions: str | None = None


class McpConnectionWrite(BaseModel):
    token: str | None = None
    header_name: str | None = None
    header_format: str | None = None


class McpConnectionRead(BaseModel):
    id: str
    server_id: str
    server_slug: str
    server_name: str
    status: str
    user_auth_method: str | None = None
    user_instructions: str | None = None


class McpTestResult(BaseModel):
    status: str
    detail: str | None = None
    latency_ms: float | None = None
    tools: int | None = None
    resources: int | None = None
    prompts: int | None = None


def _parse_uuid(value: str, *, field: str = "id") -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid {field}") from exc


def _http_error(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


def _ensure_org_admin_or_super(session: Session, org_id: UUID, current_user: User) -> None:
    if current_user.is_super_admin:
        return
    require_org_admin(session, org_id, current_user.id)


async def _test_server_row(
    row: McpServer,
    session: Session,
    current_user: User,
    *,
    org_id: UUID | None = None,
) -> McpTestResult:
    auth_type, headers = resolve_test_auth(
        session, row, user_id=current_user.id, org_id=org_id
    )
    result = await test_server_connection(row, headers=headers, auth_type=auth_type)
    return McpTestResult.model_validate(result)


def _build_auth_from_payload(
    payload: McpServerWrite | McpServerUpdate | McpBindingWrite,
    *,
    auth_type: str,
    existing_auth_config: str | None = None,
) -> str | None:
    return build_auth_config_payload(
        auth_type,
        token=getattr(payload, "token", None),
        header_name=getattr(payload, "header_name", None),
        header_format=getattr(payload, "header_format", None),
        user_auth_method=getattr(payload, "user_auth_method", None),
        user_instructions=getattr(payload, "user_instructions", None),
        existing_auth_config=existing_auth_config,
    )


def _read_server(row: McpServer, session: Session, user_id: UUID | None = None) -> McpServerRead:
    connection_status = None
    if row.auth_type == "user_provided" and user_id is not None:
        connection = session.exec(
            select(McpUserConnection).where(
                McpUserConnection.server_id == row.id,
                McpUserConnection.user_id == user_id,
            )
        ).first()
        connection_status = connection.status if connection else "not_connected"
    return McpServerRead.model_validate(server_to_read(row, connection_status=connection_status))


@router.get("/admin/mcp/settings", response_model=McpSettingsRead)
def get_instance_mcp_settings(
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> McpSettingsRead:
    require_super_admin(current_user)
    settings_row = get_mcp_settings(session)
    return McpSettingsRead(
        allow_org_servers=settings_row.allow_org_servers,
        allow_user_servers=settings_row.allow_user_servers,
    )


@router.patch("/admin/mcp/settings", response_model=McpSettingsRead)
def update_instance_mcp_settings(
    payload: McpSettingsUpdate,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> McpSettingsRead:
    require_super_admin(current_user)
    settings_row = get_mcp_settings(session)
    if payload.allow_org_servers is not None:
        settings_row.allow_org_servers = payload.allow_org_servers
    if payload.allow_user_servers is not None:
        settings_row.allow_user_servers = payload.allow_user_servers
    settings_row.updated_at = datetime.utcnow()
    session.add(settings_row)
    session.commit()
    session.refresh(settings_row)
    return McpSettingsRead(
        allow_org_servers=settings_row.allow_org_servers,
        allow_user_servers=settings_row.allow_user_servers,
    )


@router.get("/admin/mcp/servers", response_model=list[McpServerRead])
def list_instance_mcp_servers(
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[McpServerRead]:
    require_super_admin(current_user)
    rows = session.exec(
        select(McpServer).where(McpServer.scope == "instance").order_by(McpServer.slug)
    ).all()
    return [_read_server(row, session, current_user.id) for row in rows]


@router.post("/admin/mcp/servers", response_model=McpServerRead, status_code=status.HTTP_201_CREATED)
def create_instance_mcp_server(
    payload: McpServerWrite,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> McpServerRead:
    require_super_admin(current_user)
    try:
        auth_config = _build_auth_from_payload(payload, auth_type=payload.auth_type)
        row = create_server(
            session,
            scope="instance",
            slug=payload.slug,
            name=payload.name,
            transport=payload.transport,
            auth_type=payload.auth_type,
            description=payload.description,
            url=payload.url,
            command=payload.command,
            args=payload.args,
            stdio_env=payload.stdio_env,
            include_tools=payload.include_tools,
            include_resources=payload.include_resources,
            include_prompts=payload.include_prompts,
            tool_allowlist=payload.tool_allowlist,
            tool_blocklist=payload.tool_blocklist,
            is_enabled=payload.is_enabled,
            auth_config=auth_config,
        )
    except ValueError as exc:
        raise _http_error(exc) from exc
    return _read_server(row, session)


@router.patch("/admin/mcp/servers/{server_id}", response_model=McpServerRead)
def update_instance_mcp_server(
    server_id: str,
    payload: McpServerUpdate,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> McpServerRead:
    require_super_admin(current_user)
    row = session.get(McpServer, _parse_uuid(server_id))
    if not row or row.scope != "instance":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    updates = payload.model_dump(exclude_unset=True)
    auth_type = updates.pop("auth_type", None) or row.auth_type
    auth_fields = (
        "token",
        "header_name",
        "header_format",
        "user_auth_method",
        "user_instructions",
    )
    auth_updates = any(field in updates for field in auth_fields)
    for field in auth_fields:
        updates.pop(field, None)
    if payload.auth_type is not None:
        updates["auth_type"] = payload.auth_type
    if auth_updates or payload.auth_type is not None:
        try:
            updates["auth_config"] = _build_auth_from_payload(
                payload,
                auth_type=auth_type,
                existing_auth_config=row.auth_config,
            )
        except ValueError as exc:
            raise _http_error(exc) from exc
    try:
        row = update_server(session, row, **updates)
    except ValueError as exc:
        raise _http_error(exc) from exc
    return _read_server(row, session)


@router.delete("/admin/mcp/servers/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_instance_mcp_server(
    server_id: str,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    require_super_admin(current_user)
    row = session.get(McpServer, _parse_uuid(server_id))
    if not row or row.scope != "instance":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    delete_server(session, row)


@router.post("/admin/mcp/servers/{server_id}/test", response_model=McpTestResult)
async def test_instance_mcp_server(
    server_id: str,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> McpTestResult:
    require_super_admin(current_user)
    row = session.get(McpServer, _parse_uuid(server_id))
    if not row or row.scope != "instance":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    return await _test_server_row(row, session, current_user)


@router.get("/orgs/{org_id}/mcp/settings", response_model=McpOrgSettingsRead)
def get_org_mcp_settings(
    org_id: str,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> McpOrgSettingsRead:
    org_uuid = _parse_uuid(org_id, field="org_id")
    _ensure_org_admin_or_super(session, org_uuid, current_user)
    row = get_mcp_org_settings(session, org_uuid)
    return McpOrgSettingsRead(allow_user_servers=row.allow_user_servers)


@router.patch("/orgs/{org_id}/mcp/settings", response_model=McpOrgSettingsRead)
def update_org_mcp_settings(
    org_id: str,
    payload: McpOrgSettingsUpdate,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> McpOrgSettingsRead:
    org_uuid = _parse_uuid(org_id, field="org_id")
    _ensure_org_admin_or_super(session, org_uuid, current_user)
    row = get_mcp_org_settings(session, org_uuid)
    if payload.allow_user_servers is not None:
        row.allow_user_servers = payload.allow_user_servers
    row.updated_at = datetime.utcnow()
    session.add(row)
    session.commit()
    session.refresh(row)
    return McpOrgSettingsRead(allow_user_servers=row.allow_user_servers)


@router.get("/orgs/{org_id}/mcp/servers", response_model=list[McpServerRead])
def list_org_mcp_servers(
    org_id: str,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[McpServerRead]:
    org_uuid = _parse_uuid(org_id, field="org_id")
    _ensure_org_admin_or_super(session, org_uuid, current_user)
    rows = session.exec(
        select(McpServer).where(McpServer.scope == "org", McpServer.org_id == org_uuid)
    ).all()
    return [_read_server(row, session, current_user.id) for row in rows]


@router.post("/orgs/{org_id}/mcp/servers", response_model=McpServerRead, status_code=status.HTTP_201_CREATED)
def create_org_mcp_server(
    org_id: str,
    payload: McpServerWrite,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> McpServerRead:
    org_uuid = _parse_uuid(org_id, field="org_id")
    _ensure_org_admin_or_super(session, org_uuid, current_user)
    try:
        ensure_org_servers_allowed(session)
        if payload.transport == "stdio":
            raise ValueError("stdio transport is only allowed for instance servers")
        auth_config = _build_auth_from_payload(payload, auth_type=payload.auth_type)
        row = create_server(
            session,
            scope="org",
            org_id=org_uuid,
            slug=payload.slug,
            name=payload.name,
            transport=payload.transport,
            auth_type=payload.auth_type,
            description=payload.description,
            url=payload.url,
            command=payload.command,
            args=payload.args,
            include_tools=payload.include_tools,
            include_resources=payload.include_resources,
            include_prompts=payload.include_prompts,
            tool_allowlist=payload.tool_allowlist,
            tool_blocklist=payload.tool_blocklist,
            is_enabled=payload.is_enabled,
            auth_config=auth_config,
        )
    except ValueError as exc:
        raise _http_error(exc) from exc
    return _read_server(row, session, current_user.id)


@router.patch("/orgs/{org_id}/mcp/servers/{server_id}", response_model=McpServerRead)
def update_org_mcp_server(
    org_id: str,
    server_id: str,
    payload: McpServerUpdate,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> McpServerRead:
    org_uuid = _parse_uuid(org_id, field="org_id")
    _ensure_org_admin_or_super(session, org_uuid, current_user)
    row = session.get(McpServer, _parse_uuid(server_id))
    if not row or row.scope != "org" or row.org_id != org_uuid:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    updates = payload.model_dump(exclude_unset=True)
    auth_type = updates.pop("auth_type", None) or row.auth_type
    auth_fields = (
        "token",
        "header_name",
        "header_format",
        "user_auth_method",
        "user_instructions",
    )
    auth_updates = any(field in updates for field in auth_fields)
    for field in auth_fields:
        updates.pop(field, None)
    if payload.auth_type is not None:
        updates["auth_type"] = payload.auth_type
    if auth_updates or payload.auth_type is not None:
        try:
            updates["auth_config"] = _build_auth_from_payload(
                payload,
                auth_type=auth_type,
                existing_auth_config=row.auth_config,
            )
        except ValueError as exc:
            raise _http_error(exc) from exc
    try:
        row = update_server(session, row, **updates)
    except ValueError as exc:
        raise _http_error(exc) from exc
    return _read_server(row, session, current_user.id)


@router.delete("/orgs/{org_id}/mcp/servers/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_org_mcp_server(
    org_id: str,
    server_id: str,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    org_uuid = _parse_uuid(org_id, field="org_id")
    _ensure_org_admin_or_super(session, org_uuid, current_user)
    row = session.get(McpServer, _parse_uuid(server_id))
    if not row or row.scope != "org" or row.org_id != org_uuid:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    delete_server(session, row)


@router.post("/orgs/{org_id}/mcp/servers/{server_id}/test", response_model=McpTestResult)
async def test_org_mcp_server(
    org_id: str,
    server_id: str,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> McpTestResult:
    org_uuid = _parse_uuid(org_id, field="org_id")
    _ensure_org_admin_or_super(session, org_uuid, current_user)
    row = session.get(McpServer, _parse_uuid(server_id))
    if not row or row.scope != "org" or row.org_id != org_uuid:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    return await _test_server_row(row, session, current_user, org_id=org_uuid)


@router.get("/orgs/{org_id}/mcp/bindings", response_model=list[McpBindingRead])
def list_org_mcp_bindings(
    org_id: str,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[McpBindingRead]:
    org_uuid = _parse_uuid(org_id, field="org_id")
    _ensure_org_admin_or_super(session, org_uuid, current_user)
    instance_rows = session.exec(
        select(McpServer).where(McpServer.scope == "instance", McpServer.is_enabled.is_(True))
    ).all()
    bindings = {
        binding.instance_server_id: binding
        for binding in session.exec(
            select(McpOrgBinding).where(McpOrgBinding.org_id == org_uuid)
        ).all()
    }
    reads: list[McpBindingRead] = []
    for server in instance_rows:
        binding = bindings.get(server.id)
        mode = binding.mode if binding else "inherit"
        auth_type = binding.auth_type if binding and binding.mode == "override" else None
        token_set = False
        user_auth_method = None
        if binding and binding.mode == "override" and binding.auth_config:
            from app.services.mcp.auth_config import auth_config_token_set

            token_set = auth_config_token_set(binding.auth_config)
            if binding.auth_type == "user_provided":
                data = decrypt_auth_config(binding.auth_config)
                user_auth_method = data.get("user_auth_method")
        reads.append(
            McpBindingRead(
                id=str(binding.id) if binding else "",
                org_id=str(org_uuid),
                instance_server_id=str(server.id),
                instance_server_slug=server.slug,
                instance_server_name=server.name,
                mode=mode,
                auth_type=auth_type,
                token_set=token_set,
                user_auth_method=str(user_auth_method) if user_auth_method else None,
            )
        )
    return reads


@router.put("/orgs/{org_id}/mcp/bindings/{instance_server_id}", response_model=McpBindingRead)
def upsert_org_mcp_binding(
    org_id: str,
    instance_server_id: str,
    payload: McpBindingWrite,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> McpBindingRead:
    org_uuid = _parse_uuid(org_id, field="org_id")
    _ensure_org_admin_or_super(session, org_uuid, current_user)
    server_uuid = _parse_uuid(instance_server_id, field="instance_server_id")
    server = session.get(McpServer, server_uuid)
    if not server or server.scope != "instance":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Instance server not found")

    binding = session.exec(
        select(McpOrgBinding).where(
            McpOrgBinding.org_id == org_uuid,
            McpOrgBinding.instance_server_id == server_uuid,
        )
    ).first()
    auth_config = None
    auth_type = None
    if payload.mode == "override":
        if not payload.auth_type:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="auth_type is required when mode=override",
            )
        auth_type = payload.auth_type
        try:
            auth_config = _build_auth_from_payload(
                payload,
                auth_type=auth_type,
                existing_auth_config=binding.auth_config if binding else None,
            )
        except ValueError as exc:
            raise _http_error(exc) from exc

    if binding:
        binding.mode = payload.mode
        binding.auth_type = auth_type
        binding.auth_config = auth_config
        binding.updated_at = datetime.utcnow()
    else:
        binding = McpOrgBinding(
            org_id=org_uuid,
            instance_server_id=server_uuid,
            mode=payload.mode,
            auth_type=auth_type,
            auth_config=auth_config,
        )
    session.add(binding)
    session.commit()
    session.refresh(binding)
    token_set = bool(auth_config)
    user_auth_method = None
    if auth_type == "user_provided" and auth_config:
        user_auth_method = decrypt_auth_config(auth_config).get("user_auth_method")
    return McpBindingRead(
        id=str(binding.id),
        org_id=str(org_uuid),
        instance_server_id=str(server_uuid),
        instance_server_slug=server.slug,
        instance_server_name=server.name,
        mode=binding.mode,
        auth_type=auth_type,
        token_set=token_set,
        user_auth_method=str(user_auth_method) if user_auth_method else None,
    )


@router.get("/users/me/mcp/overview")
def get_user_mcp_overview(
    org_id: str | None = Query(default=None),
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    settings_row = get_mcp_settings(session)
    org_settings = None
    if org_id:
        org_uuid = _parse_uuid(org_id, field="org_id")
        require_org_member(
            session,
            org_uuid,
            current_user.id,
            is_super_admin=current_user.is_super_admin,
        )
        org_settings = get_mcp_org_settings(session, org_uuid)

    instance_servers = session.exec(
        select(McpServer).where(McpServer.scope == "instance", McpServer.is_enabled.is_(True))
    ).all()
    user_provided: list[McpServerRead] = []
    for row in instance_servers:
        if row.auth_type != "user_provided":
            continue
        binding = None
        if org_id:
            binding = session.exec(
                select(McpOrgBinding).where(
                    McpOrgBinding.org_id == _parse_uuid(org_id),
                    McpOrgBinding.instance_server_id == row.id,
                )
            ).first()
            if binding and binding.mode == "disabled":
                continue
        user_provided.append(_read_server(row, session, current_user.id))

    org_servers: list[McpServerRead] = []
    if org_id and settings_row.allow_org_servers:
        org_uuid = _parse_uuid(org_id)
        org_rows = session.exec(
            select(McpServer).where(
                McpServer.scope == "org",
                McpServer.org_id == org_uuid,
                McpServer.is_enabled.is_(True),
            )
        ).all()
        for row in org_rows:
            if row.auth_type == "user_provided":
                org_servers.append(_read_server(row, session, current_user.id))

    personal: list[McpServerRead] = []
    if settings_row.allow_user_servers and (org_settings is None or org_settings.allow_user_servers):
        personal_rows = session.exec(
            select(McpServer).where(
                McpServer.scope == "user",
                McpServer.user_id == current_user.id,
            )
        ).all()
        personal = [_read_server(row, session, current_user.id) for row in personal_rows]

    return {
        "policy": {
            "allow_org_servers": settings_row.allow_org_servers,
            "allow_user_servers": settings_row.allow_user_servers,
            "org_allow_user_servers": org_settings.allow_user_servers if org_settings else None,
        },
        "user_provided_servers": user_provided + org_servers,
        "personal_servers": personal,
    }


@router.get("/users/me/mcp/servers", response_model=list[McpServerRead])
def list_user_mcp_servers(
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[McpServerRead]:
    rows = session.exec(
        select(McpServer).where(McpServer.scope == "user", McpServer.user_id == current_user.id)
    ).all()
    return [_read_server(row, session, current_user.id) for row in rows]


@router.post("/users/me/mcp/servers", response_model=McpServerRead, status_code=status.HTTP_201_CREATED)
def create_user_mcp_server(
    payload: McpServerWrite,
    org_id: str | None = Query(default=None),
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> McpServerRead:
    try:
        ensure_user_servers_allowed(
            session, _parse_uuid(org_id) if org_id else None
        )
        if payload.transport == "stdio":
            raise ValueError("stdio transport is only allowed for instance servers")
        auth_config = _build_auth_from_payload(payload, auth_type=payload.auth_type)
        row = create_server(
            session,
            scope="user",
            user_id=current_user.id,
            slug=payload.slug,
            name=payload.name,
            transport=payload.transport,
            auth_type=payload.auth_type,
            description=payload.description,
            url=payload.url,
            command=payload.command,
            args=payload.args,
            include_tools=payload.include_tools,
            include_resources=payload.include_resources,
            include_prompts=payload.include_prompts,
            tool_allowlist=payload.tool_allowlist,
            tool_blocklist=payload.tool_blocklist,
            is_enabled=payload.is_enabled,
            auth_config=auth_config,
        )
    except ValueError as exc:
        raise _http_error(exc) from exc
    return _read_server(row, session, current_user.id)


@router.patch("/users/me/mcp/servers/{server_id}", response_model=McpServerRead)
def update_user_mcp_server(
    server_id: str,
    payload: McpServerUpdate,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> McpServerRead:
    row = session.get(McpServer, _parse_uuid(server_id))
    if not row or row.scope != "user" or row.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    updates = payload.model_dump(exclude_unset=True)
    auth_type = updates.pop("auth_type", None) or row.auth_type
    auth_fields = (
        "token",
        "header_name",
        "header_format",
        "user_auth_method",
        "user_instructions",
    )
    auth_updates = any(field in updates for field in auth_fields)
    for field in auth_fields:
        updates.pop(field, None)
    if payload.auth_type is not None:
        updates["auth_type"] = payload.auth_type
    if auth_updates or payload.auth_type is not None:
        try:
            updates["auth_config"] = _build_auth_from_payload(
                payload,
                auth_type=auth_type,
                existing_auth_config=row.auth_config,
            )
        except ValueError as exc:
            raise _http_error(exc) from exc
    try:
        row = update_server(session, row, **updates)
    except ValueError as exc:
        raise _http_error(exc) from exc
    return _read_server(row, session, current_user.id)


@router.delete("/users/me/mcp/servers/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user_mcp_server(
    server_id: str,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    row = session.get(McpServer, _parse_uuid(server_id))
    if not row or row.scope != "user" or row.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    delete_server(session, row)


@router.post("/users/me/mcp/servers/{server_id}/test", response_model=McpTestResult)
async def test_user_mcp_server(
    server_id: str,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> McpTestResult:
    row = session.get(McpServer, _parse_uuid(server_id))
    if not row or row.scope != "user" or row.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    return await _test_server_row(row, session, current_user)


@router.put("/users/me/mcp/connections/{server_id}", response_model=McpConnectionRead)
def upsert_user_mcp_connection(
    server_id: str,
    payload: McpConnectionWrite,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> McpConnectionRead:
    server_uuid = _parse_uuid(server_id, field="server_id")
    row = session.get(McpServer, server_uuid)
    if not row or row.auth_type != "user_provided":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    data = decrypt_auth_config(row.auth_config)
    method = str(data.get("user_auth_method") or "")
    if method not in {"bearer", "api_token"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Server does not accept token connections",
        )
    if not payload.token or not payload.token.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="token is required")
    defaults = data.get("api_token_defaults") if isinstance(data.get("api_token_defaults"), dict) else {}
    connection = store_user_token_connection(
        session,
        user_id=current_user.id,
        server_id=server_uuid,
        method=method,
        token=payload.token,
        header_name=payload.header_name or defaults.get("header_name"),
        header_format=payload.header_format or defaults.get("header_format"),
    )
    return McpConnectionRead(
        id=str(connection.id),
        server_id=str(row.id),
        server_slug=row.slug,
        server_name=row.name,
        status=connection.status,
        user_auth_method=method,
        user_instructions=data.get("user_instructions"),
    )


@router.delete("/users/me/mcp/connections/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user_mcp_connection(
    server_id: str,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    server_uuid = _parse_uuid(server_id, field="server_id")
    connection = session.exec(
        select(McpUserConnection).where(
            McpUserConnection.user_id == current_user.id,
            McpUserConnection.server_id == server_uuid,
        )
    ).first()
    if not connection:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found")
    session.delete(connection)
    session.commit()


@router.post("/users/me/mcp/connections/{server_id}/test", response_model=McpTestResult)
async def test_user_mcp_connection(
    server_id: str,
    org_id: str | None = Query(default=None),
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> McpTestResult:
    server_uuid = _parse_uuid(server_id, field="server_id")
    row = session.get(McpServer, server_uuid)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    org_uuid: UUID | None = None
    if org_id:
        org_uuid = _parse_uuid(org_id, field="org_id")
        require_org_member(
            session,
            org_uuid,
            current_user.id,
            is_super_admin=current_user.is_super_admin,
        )
    elif row.scope == "org" and row.org_id is not None:
        org_uuid = row.org_id
        require_org_member(
            session,
            org_uuid,
            current_user.id,
            is_super_admin=current_user.is_super_admin,
        )
    return await _test_server_row(row, session, current_user, org_id=org_uuid)
