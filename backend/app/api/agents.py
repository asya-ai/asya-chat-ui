from __future__ import annotations

import base64
import binascii
import re
from datetime import datetime, timedelta, timezone
from html import unescape
from urllib.parse import urlparse
from uuid import UUID

import httpx
import jwt
from fastapi import APIRouter, Depends, HTTPException, Response, status
from jwt.exceptions import InvalidTokenError
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func
from sqlmodel import Session, select

from app.api.deps import AuthContext, get_auth_context, get_db
from app.core.config import settings
from app.core.url_safety import is_blocked_hostname
from app.models import (
    Agent,
    AgentAccess,
    AgentAccessRole,
    AgentChunk,
    AgentEmbedding,
    AgentSource,
    AgentSourceKind,
    AgentSourceStatus,
    AgentTeamAccess,
    AgentUserOrder,
    AgentVisibility,
    Chat,
    OrgMembership,
    Prompt,
    PromptTeamShare,
    PromptUserShare,
    PromptVisibility,
    Team,
    User,
)
from app.services.agent_access import (
    ROLE_ORDER,
    accessible_agent_ids,
    get_agent_role,
    list_accessible_agents,
)
from app.services.agents.source_media import is_archive_file, is_image_file
from app.services.agents.source_parsing import extract_text_from_file
from app.services.agents.source_prepare import delete_source_tree
from app.services.file_storage import delete_file, maybe_read_file_bytes, write_agent_source_file
from app.services.image_normalize import normalize_image_attachment
from app.workers.celery_app import celery_app

router = APIRouter(prefix="/agents", tags=["agents"])


def _enqueue_source_reindex_task(source_id: UUID) -> None:
    celery_app.send_task(
        "chatui.reindex_agent_source",
        args=[str(source_id)],
    )


def _decode_base64_bytes(data_base64: str) -> bytes:
    try:
        return base64.b64decode(data_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid base64 source content",
        ) from exc


def _persist_source_bytes(
    source: AgentSource,
    *,
    data: bytes,
    file_name: str | None,
) -> None:
    name = file_name or source.file_name or source.title or "source"
    if source.file_path:
        delete_file(source.file_path)
    relative_path, _size = write_agent_source_file(
        agent_id=source.agent_id,
        source_id=source.id,
        file_name=name,
        data=data,
    )
    source.file_path = relative_path


def _store_file_source_bytes(
    source: AgentSource,
    *,
    data_base64: str,
    file_name: str | None = None,
) -> None:
    if source.kind != AgentSourceKind.file:
        return
    _persist_source_bytes(
        source,
        data=_decode_base64_bytes(data_base64),
        file_name=file_name or source.file_name,
    )


class AgentCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    preferred_model_id: str | None = None
    master_prompt: str | None = None


class AgentUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    preferred_model_id: str | None = None
    master_prompt: str | None = None
    visibility: AgentVisibility | None = None


class AgentRead(BaseModel):
    id: str
    name: str
    description: str | None
    preferred_model_id: str | None
    master_prompt: str
    visibility: AgentVisibility
    is_owner: bool
    role: AgentAccessRole
    display_order: int | None = None
    created_at: datetime
    updated_at: datetime


class AgentOrderUpdateRequest(BaseModel):
    agent_id: str
    display_order: int


class AgentSourceCreateRequest(BaseModel):
    kind: AgentSourceKind
    title: str | None = Field(default=None, min_length=1, max_length=200)
    url: str | None = None
    file_name: str | None = None
    content_type: str | None = None
    data_base64: str | None = None
    content_text: str | None = None


class AgentSourceUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    url: str | None = None
    file_name: str | None = None
    content_type: str | None = None
    data_base64: str | None = None
    content_text: str | None = None


class AgentSourceRead(BaseModel):
    id: str
    kind: AgentSourceKind
    title: str
    summary: str | None
    url: str | None
    file_name: str | None
    content_type: str | None
    status: AgentSourceStatus
    error_message: str | None
    parent_source_id: str | None = None
    is_archive: bool = False
    is_image: bool = False
    content_url: str | None = None
    cloud_provider: str | None = None
    cloud_remote_kind: str | None = None
    cloud_checked_at: str | None = None
    created_at: datetime
    updated_at: datetime


class AgentShareRequest(BaseModel):
    user_id: str | None = None
    team_id: str | None = None
    org: bool = False
    role: AgentAccessRole = AgentAccessRole.viewer

    @model_validator(mode="after")
    def exactly_one_target(self) -> "AgentShareRequest":
        targets = sum((bool(self.user_id), bool(self.team_id), self.org))
        if targets != 1:
            raise ValueError("Provide exactly one of user_id, team_id, or org=true")
        return self


class AgentShareRead(BaseModel):
    kind: str
    user_id: str | None = None
    email: str | None = None
    display_name: str | None = None
    team_id: str | None = None
    team_name: str | None = None
    role: AgentAccessRole
    created_at: datetime
    updated_at: datetime


class AgentShareSuggestion(BaseModel):
    kind: str
    user_id: str | None = None
    email: str | None = None
    display_name: str | None = None
    team_id: str | None = None
    team_name: str | None = None


def _to_agent_read(
    agent: Agent,
    user_id: UUID,
    role: AgentAccessRole,
    display_order: int | None = None,
) -> AgentRead:
    return AgentRead(
        id=str(agent.id),
        name=agent.name,
        description=agent.description,
        preferred_model_id=str(agent.preferred_model_id) if agent.preferred_model_id else None,
        master_prompt=agent.master_prompt,
        visibility=agent.visibility,
        is_owner=agent.owner_user_id == user_id,
        role=role,
        display_order=display_order,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
    )


def _parse_uuid(value: str, detail: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail) from exc


def _require_agent_access(
    session: Session,
    auth: AuthContext,
    agent_id: UUID,
    *,
    minimum_role: AgentAccessRole = AgentAccessRole.viewer,
) -> tuple[Agent, AgentAccessRole]:
    agent = session.exec(
        select(Agent).where(Agent.id == agent_id, Agent.org_id == auth.org_id)
    ).first()
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    role = get_agent_role(session, agent, auth.user.id)
    if role is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Agent access required")

    if ROLE_ORDER[role] < ROLE_ORDER[minimum_role]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient agent permissions",
        )
    return agent, role


def _refresh_agent_visibility(session: Session, agent: Agent) -> None:
    has_org = agent.org_access_role is not None
    has_team = (
        session.exec(
            select(AgentTeamAccess.id).where(AgentTeamAccess.agent_id == agent.id).limit(1)
        ).first()
        is not None
    )
    has_extra_user = (
        session.exec(
            select(AgentAccess.id)
            .where(
                AgentAccess.agent_id == agent.id,
                AgentAccess.user_id != agent.owner_user_id,
            )
            .limit(1)
        ).first()
        is not None
    )
    agent.visibility = (
        AgentVisibility.shared if (has_org or has_team or has_extra_user) else AgentVisibility.private
    )
    agent.updated_at = datetime.utcnow()
    session.add(agent)


def _validate_org_team(session: Session, auth: AuthContext, team_id: UUID) -> Team:
    team = session.exec(
        select(Team).where(Team.id == team_id, Team.org_id == auth.org_id)
    ).first()
    if not team:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    if team.is_default:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Share with the organization instead of the default team",
        )
    return team


def _decode_source_text(payload: AgentSourceCreateRequest) -> str:
    if payload.kind == AgentSourceKind.cloud:
        return ""

    if payload.kind == AgentSourceKind.url:
        target = (payload.url or "").strip()
        if not target:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="url is required for URL sources",
            )
        return _fetch_url_text(target)

    if payload.kind == AgentSourceKind.file:
        if not payload.data_base64:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="data_base64 is required for file sources",
            )
        if is_archive_file(
            file_name=payload.file_name, content_type=payload.content_type
        ) or is_image_file(
            file_name=payload.file_name, content_type=payload.content_type
        ):
            # Text / description is produced asynchronously during reindex.
            return ""
        raw = _decode_base64_bytes(payload.data_base64)
        try:
            return extract_text_from_file(
                file_name=payload.file_name,
                content_type=payload.content_type,
                data=raw,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc

    if payload.content_text:
        return payload.content_text
    if payload.data_base64:
        raw = _decode_base64_bytes(payload.data_base64)
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only UTF-8 text sources are currently supported",
            ) from exc
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Either content_text or data_base64 is required",
    )


def _strip_html(raw_html: str) -> str:
    without_scripts = re.sub(
        r"<(script|style)[^>]*>.*?</\1>",
        " ",
        raw_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(r"<[^>]+>", " ", without_scripts)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _fetch_url_text(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only http/https URLs are supported",
        )
    if is_blocked_hostname(parsed.hostname):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="URL host is blocked",
        )
    try:
        with httpx.Client(timeout=20.0, follow_redirects=True) as client:
            response = client.get(url)
        response.raise_for_status()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to fetch URL source",
        ) from exc
    if is_blocked_hostname(urlparse(str(response.url)).hostname):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="URL host is blocked",
        )
    content_type = (response.headers.get("content-type") or "").lower()
    body = response.text or ""
    if "html" in content_type:
        body = _strip_html(body)
    body = body.strip()
    if not body:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No extractable text found at URL",
        )
    return body


def _source_access_token(source_id: UUID) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.attachment_url_expire_minutes
    )
    payload = {"src": str(source_id), "exp": expire}
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def _decode_source_access_token(token: str) -> UUID:
    try:
        payload = jwt.decode(
            token, settings.secret_key, algorithms=[settings.jwt_algorithm]
        )
        source_id = payload.get("src")
        if not source_id:
            raise ValueError("Missing source id")
        return UUID(str(source_id))
    except (InvalidTokenError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid source token",
        ) from exc


def _public_api_base_url() -> str | None:
    configured = (settings.public_api_base_url or "").strip()
    if configured:
        return configured.rstrip("/")
    return None


def _source_content_url(source: AgentSource) -> str | None:
    if not is_image_file(file_name=source.file_name, content_type=source.content_type):
        meta = source.metadata_json if isinstance(source.metadata_json, dict) else {}
        if not meta.get("is_image"):
            return None
    token = _source_access_token(source.id)
    base_url = _public_api_base_url()
    if base_url:
        return f"{base_url}/agents/sources/{source.id}/content?token={token}"
    return f"/api/agents/sources/{source.id}/content?token={token}"


def _to_source_read(source: AgentSource) -> AgentSourceRead:
    metadata = source.metadata_json if isinstance(source.metadata_json, dict) else {}
    is_archive = bool(metadata.get("is_archive")) or is_archive_file(
        file_name=source.file_name, content_type=source.content_type
    )
    is_image = bool(metadata.get("is_image")) or is_image_file(
        file_name=source.file_name, content_type=source.content_type
    )
    cloud_provider = metadata.get("provider") if source.kind == AgentSourceKind.cloud else None
    cloud_remote_kind = (
        metadata.get("remote_kind") if source.kind == AgentSourceKind.cloud else None
    )
    cloud_checked_at = (
        metadata.get("checked_at") if source.kind == AgentSourceKind.cloud else None
    )
    return AgentSourceRead(
        id=str(source.id),
        kind=source.kind,
        title=source.title,
        summary=metadata.get("summary"),
        url=source.url,
        file_name=source.file_name,
        content_type=source.content_type,
        status=source.status,
        error_message=source.error_message,
        parent_source_id=str(source.parent_source_id) if source.parent_source_id else None,
        is_archive=is_archive,
        is_image=is_image,
        content_url=_source_content_url(source) if is_image else None,
        cloud_provider=str(cloud_provider) if cloud_provider else None,
        cloud_remote_kind=str(cloud_remote_kind) if cloud_remote_kind else None,
        cloud_checked_at=str(cloud_checked_at) if cloud_checked_at else None,
        created_at=source.created_at,
        updated_at=source.updated_at,
    )


@router.get("", response_model=list[AgentRead])
def list_agents(
    session: Session = Depends(get_db),
    auth: AuthContext = Depends(get_auth_context),
) -> list[AgentRead]:
    rows = list_accessible_agents(session, auth.org_id, auth.user.id)
    return [
        _to_agent_read(agent, auth.user.id, role, display_order)
        for agent, role, display_order in rows
    ]


@router.put("/order", response_model=list[AgentRead])
def update_agent_order(
    payload: list[AgentOrderUpdateRequest],
    session: Session = Depends(get_db),
    auth: AuthContext = Depends(get_auth_context),
) -> list[AgentRead]:
    if not payload:
        rows = list_accessible_agents(session, auth.org_id, auth.user.id)
        return [
            _to_agent_read(agent, auth.user.id, role, display_order)
            for agent, role, display_order in rows
        ]

    updates: dict[UUID, int] = {}
    for item in payload:
        agent_uuid = _parse_uuid(item.agent_id, "Invalid agent id")
        if item.display_order < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid display order"
            )
        updates[agent_uuid] = item.display_order

    allowed = accessible_agent_ids(session, auth.org_id, auth.user.id)
    unknown = set(updates.keys()) - allowed
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Agent access required"
        )

    now = datetime.utcnow()
    existing = session.exec(
        select(AgentUserOrder).where(
            AgentUserOrder.org_id == auth.org_id,
            AgentUserOrder.user_id == auth.user.id,
            AgentUserOrder.agent_id.in_(updates.keys()),
        )
    ).all()
    by_agent = {row.agent_id: row for row in existing}
    for agent_id, display_order in updates.items():
        row = by_agent.get(agent_id)
        if row is None:
            session.add(
                AgentUserOrder(
                    user_id=auth.user.id,
                    org_id=auth.org_id,
                    agent_id=agent_id,
                    display_order=display_order,
                    updated_at=now,
                )
            )
        else:
            row.display_order = display_order
            row.updated_at = now
            session.add(row)
    session.commit()

    rows = list_accessible_agents(session, auth.org_id, auth.user.id)
    return [
        _to_agent_read(agent, auth.user.id, role, display_order)
        for agent, role, display_order in rows
    ]


@router.post("", response_model=AgentRead)
def create_agent(
    payload: AgentCreateRequest,
    session: Session = Depends(get_db),
    auth: AuthContext = Depends(get_auth_context),
) -> AgentRead:
    preferred_model_id = (
        _parse_uuid(payload.preferred_model_id, "Invalid preferred model id")
        if payload.preferred_model_id
        else None
    )
    now = datetime.utcnow()
    agent = Agent(
        org_id=auth.org_id,
        owner_user_id=auth.user.id,
        name=payload.name.strip(),
        description=payload.description,
        preferred_model_id=preferred_model_id,
        master_prompt=(payload.master_prompt or "").strip(),
        visibility=AgentVisibility.private,
        created_at=now,
        updated_at=now,
    )
    session.add(agent)
    session.flush()
    session.add(
        AgentAccess(
            agent_id=agent.id,
            user_id=auth.user.id,
            role=AgentAccessRole.owner,
            granted_by_user_id=auth.user.id,
            created_at=now,
            updated_at=now,
        )
    )
    session.commit()
    session.refresh(agent)
    return _to_agent_read(agent, auth.user.id, AgentAccessRole.owner)


@router.patch("/{agent_id}", response_model=AgentRead)
def update_agent(
    agent_id: UUID,
    payload: AgentUpdateRequest,
    session: Session = Depends(get_db),
    auth: AuthContext = Depends(get_auth_context),
) -> AgentRead:
    agent, role = _require_agent_access(
        session, auth, agent_id, minimum_role=AgentAccessRole.editor
    )
    if payload.name is not None:
        agent.name = payload.name.strip()
    if payload.description is not None:
        agent.description = payload.description
    if payload.master_prompt is not None:
        agent.master_prompt = payload.master_prompt.strip()
    if payload.preferred_model_id is not None:
        agent.preferred_model_id = _parse_uuid(payload.preferred_model_id, "Invalid preferred model id")
    if payload.visibility is not None:
        agent.visibility = payload.visibility
    agent.updated_at = datetime.utcnow()
    session.add(agent)
    session.commit()
    session.refresh(agent)
    return _to_agent_read(agent, auth.user.id, role)


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_agent(
    agent_id: UUID,
    session: Session = Depends(get_db),
    auth: AuthContext = Depends(get_auth_context),
) -> None:
    agent, _ = _require_agent_access(
        session, auth, agent_id, minimum_role=AgentAccessRole.owner
    )

    chats = session.exec(select(Chat).where(Chat.agent_id == agent.id)).all()
    for chat in chats:
        chat.agent_id = None
        session.add(chat)

    prompts = session.exec(select(Prompt).where(Prompt.agent_id == agent.id)).all()
    for prompt in prompts:
        # Project-only prompts die with the project; others just lose the link.
        if prompt.visibility == PromptVisibility.project:
            for share in session.exec(
                select(PromptTeamShare).where(PromptTeamShare.prompt_id == prompt.id)
            ).all():
                session.delete(share)
            for share in session.exec(
                select(PromptUserShare).where(PromptUserShare.prompt_id == prompt.id)
            ).all():
                session.delete(share)
            session.delete(prompt)
        else:
            prompt.agent_id = None
            session.add(prompt)
    session.flush()

    access_rows = session.exec(
        select(AgentAccess).where(AgentAccess.agent_id == agent.id)
    ).all()
    for row in access_rows:
        session.delete(row)
    team_access_rows = session.exec(
        select(AgentTeamAccess).where(AgentTeamAccess.agent_id == agent.id)
    ).all()
    for row in team_access_rows:
        session.delete(row)

    sources = session.exec(select(AgentSource).where(AgentSource.agent_id == agent.id)).all()
    roots = [source for source in sources if source.parent_source_id is None]
    for source in roots:
        delete_source_tree(session, source)
    # Detach any leftover chunks keyed only by agent_id.
    orphan_chunks = session.exec(
        select(AgentChunk).where(AgentChunk.agent_id == agent.id)
    ).all()
    if orphan_chunks:
        orphan_ids = [chunk.id for chunk in orphan_chunks]
        embeddings = session.exec(
            select(AgentEmbedding).where(AgentEmbedding.chunk_id.in_(orphan_ids))
        ).all()
        for embedding in embeddings:
            session.delete(embedding)
        session.flush()
        for chunk in orphan_chunks:
            session.delete(chunk)
        session.flush()
    leftover_sources = session.exec(
        select(AgentSource).where(AgentSource.agent_id == agent.id)
    ).all()
    for source in leftover_sources:
        delete_file(source.file_path)
        session.delete(source)
    session.flush()

    session.delete(agent)
    session.commit()


@router.get("/{agent_id}/sources", response_model=list[AgentSourceRead])
def list_sources(
    agent_id: UUID,
    session: Session = Depends(get_db),
    auth: AuthContext = Depends(get_auth_context),
) -> list[AgentSourceRead]:
    _require_agent_access(session, auth, agent_id, minimum_role=AgentAccessRole.viewer)
    from app.services.agents.cloud_sources import maybe_sync_agent_cloud_roots

    maybe_sync_agent_cloud_roots(session, agent_id, force=False)
    items = session.exec(
        select(AgentSource)
        .where(
            AgentSource.agent_id == agent_id,
            AgentSource.kind != AgentSourceKind.chat,
        )
        .order_by(AgentSource.updated_at.desc())
    ).all()
    return [_to_source_read(item) for item in items]


@router.post("/{agent_id}/sources", response_model=AgentSourceRead)
def create_source(
    agent_id: UUID,
    payload: AgentSourceCreateRequest,
    session: Session = Depends(get_db),
    auth: AuthContext = Depends(get_auth_context),
) -> AgentSourceRead:
    _require_agent_access(session, auth, agent_id, minimum_role=AgentAccessRole.editor)
    if payload.kind == AgentSourceKind.chat:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Chat sources are managed automatically",
        )

    # Auto-promote Drive/OneDrive paste links to cloud sources.
    effective_kind = payload.kind
    if payload.kind == AgentSourceKind.url and payload.url:
        from app.services.agents.cloud_sources import parse_cloud_url

        if parse_cloud_url(payload.url):
            effective_kind = AgentSourceKind.cloud

    if effective_kind == AgentSourceKind.cloud:
        from app.services.agents.cloud_sources import (
            CloudSourceError,
            create_cloud_source_tree,
        )

        url = (payload.url or "").strip()
        if not url:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="url is required for cloud sources",
            )
        try:
            root, enqueue_ids = create_cloud_source_tree(
                session,
                agent_id=agent_id,
                url=url,
                title=payload.title,
                user_id=auth.user.id,
                org_id=auth.org_id,
            )
        except CloudSourceError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        session.commit()
        session.refresh(root)
        for child_id in enqueue_ids:
            _enqueue_source_reindex_task(child_id)
        return _to_source_read(root)

    now = datetime.utcnow()
    file_name = payload.file_name
    content_type = payload.content_type
    raw_bytes: bytes | None = None
    metadata: dict | None = None

    if payload.kind == AgentSourceKind.file and payload.data_base64:
        raw_bytes = _decode_base64_bytes(payload.data_base64)
        if len(raw_bytes) > settings.attachments_max_file_bytes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Attachment exceeds maximum size.",
            )
        if is_image_file(file_name=file_name, content_type=content_type):
            file_name, content_type, raw_bytes = normalize_image_attachment(
                file_name or "image",
                content_type or "application/octet-stream",
                raw_bytes,
            )
            metadata = {"is_image": True}
        elif is_archive_file(file_name=file_name, content_type=content_type):
            metadata = {"is_archive": True}

    # Rebuild payload fields that may have been normalized for images.
    decode_payload = AgentSourceCreateRequest(
        kind=payload.kind,
        title=payload.title,
        url=payload.url,
        file_name=file_name,
        content_type=content_type,
        data_base64=payload.data_base64,
        content_text=payload.content_text,
    )
    source = AgentSource(
        agent_id=agent_id,
        kind=payload.kind,
        title=(payload.title.strip() if payload.title else (file_name or payload.url or "Source")),
        url=payload.url,
        file_name=file_name,
        content_type=content_type,
        content_text=_decode_source_text(decode_payload),
        status=AgentSourceStatus.queued,
        metadata_json=metadata,
        created_at=now,
        updated_at=now,
    )
    session.add(source)
    session.flush()
    if payload.kind == AgentSourceKind.file and raw_bytes is not None:
        _persist_source_bytes(
            source,
            data=raw_bytes,
            file_name=file_name,
        )
    source.status = AgentSourceStatus.queued
    source.error_message = None
    session.add(source)
    session.commit()
    session.refresh(source)
    _enqueue_source_reindex_task(source.id)
    return _to_source_read(source)


@router.patch("/{agent_id}/sources/{source_id}", response_model=AgentSourceRead)
def update_source(
    agent_id: UUID,
    source_id: UUID,
    payload: AgentSourceUpdateRequest,
    session: Session = Depends(get_db),
    auth: AuthContext = Depends(get_auth_context),
) -> AgentSourceRead:
    _require_agent_access(session, auth, agent_id, minimum_role=AgentAccessRole.editor)
    source = session.exec(
        select(AgentSource).where(AgentSource.id == source_id, AgentSource.agent_id == agent_id)
    ).first()
    if not source:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")

    should_reindex = False
    if payload.title is not None:
        source.title = payload.title.strip()
    if payload.url is not None:
        source.url = payload.url.strip() or None
        if source.kind == AgentSourceKind.url and source.url:
            source.content_text = _decode_source_text(
                AgentSourceCreateRequest(
                    kind=source.kind,
                    title=source.title,
                    url=source.url,
                    file_name=source.file_name,
                    content_type=source.content_type,
                )
            )
            should_reindex = True
    if payload.file_name is not None:
        source.file_name = payload.file_name
    if payload.content_type is not None:
        source.content_type = payload.content_type

    if payload.content_text is not None:
        source.content_text = payload.content_text
        should_reindex = True
    elif payload.data_base64 is not None:
        source.content_text = _decode_source_text(
            AgentSourceCreateRequest(
                kind=source.kind,
                title=source.title,
                url=payload.url if payload.url is not None else source.url,
                file_name=payload.file_name if payload.file_name is not None else source.file_name,
                content_type=payload.content_type if payload.content_type is not None else source.content_type,
                data_base64=payload.data_base64,
                content_text=None,
            )
        )
        if source.kind == AgentSourceKind.file:
            _store_file_source_bytes(
                source,
                data_base64=payload.data_base64,
                file_name=payload.file_name if payload.file_name is not None else source.file_name,
            )
        should_reindex = True

    source.updated_at = datetime.utcnow()
    if should_reindex:
        source.status = AgentSourceStatus.queued
        source.error_message = None
    session.add(source)
    session.commit()
    session.refresh(source)
    if should_reindex:
        _enqueue_source_reindex_task(source.id)
    return _to_source_read(source)


@router.delete("/{agent_id}/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_source(
    agent_id: UUID,
    source_id: UUID,
    session: Session = Depends(get_db),
    auth: AuthContext = Depends(get_auth_context),
) -> None:
    _require_agent_access(session, auth, agent_id, minimum_role=AgentAccessRole.editor)
    source = session.exec(
        select(AgentSource).where(AgentSource.id == source_id, AgentSource.agent_id == agent_id)
    ).first()
    if not source:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")

    delete_source_tree(session, source)
    session.commit()


@router.get("/sources/{source_id}/content")
def get_source_content(
    source_id: UUID,
    token: str,
    session: Session = Depends(get_db),
) -> Response:
    token_source_id = _decode_source_access_token(token)
    if token_source_id != source_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid source token"
        )
    source = session.get(AgentSource, source_id)
    if not source:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    data = maybe_read_file_bytes(source.file_path)
    if data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Source content missing"
        )
    return Response(
        content=data,
        media_type=source.content_type or "application/octet-stream",
        headers={
            "Cache-Control": "private, max-age=300",
            "Content-Disposition": f'inline; filename="{source.file_name or "source"}"',
        },
    )


@router.post("/{agent_id}/sources/{source_id}/reindex", response_model=AgentSourceRead)
def reindex_agent_source(
    agent_id: UUID,
    source_id: UUID,
    session: Session = Depends(get_db),
    auth: AuthContext = Depends(get_auth_context),
) -> AgentSourceRead:
    _require_agent_access(session, auth, agent_id, minimum_role=AgentAccessRole.editor)
    source = session.exec(
        select(AgentSource).where(AgentSource.id == source_id, AgentSource.agent_id == agent_id)
    ).first()
    if not source:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    if source.kind == AgentSourceKind.cloud:
        from app.services.agents.cloud_sources import maybe_sync_cloud_source

        maybe_sync_cloud_source(session, source, force=True)
        session.refresh(source)
        return _to_source_read(source)
    source.status = AgentSourceStatus.queued
    source.error_message = None
    source.updated_at = datetime.utcnow()
    session.add(source)
    session.commit()
    session.refresh(source)
    _enqueue_source_reindex_task(source.id)
    return _to_source_read(source)


@router.post("/{agent_id}/sources/{source_id}/sync", response_model=AgentSourceRead)
def sync_cloud_agent_source(
    agent_id: UUID,
    source_id: UUID,
    session: Session = Depends(get_db),
    auth: AuthContext = Depends(get_auth_context),
) -> AgentSourceRead:
    """Force cloud change-detection sync (past cooldown)."""
    _require_agent_access(session, auth, agent_id, minimum_role=AgentAccessRole.editor)
    source = session.exec(
        select(AgentSource).where(AgentSource.id == source_id, AgentSource.agent_id == agent_id)
    ).first()
    if not source:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    if source.kind != AgentSourceKind.cloud:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only cloud sources can be synced",
        )
    from app.services.agents.cloud_sources import maybe_sync_cloud_source

    maybe_sync_cloud_source(session, source, force=True)
    session.refresh(source)
    return _to_source_read(source)


@router.get("/{agent_id}/shares", response_model=list[AgentShareRead])
def list_shares(
    agent_id: UUID,
    session: Session = Depends(get_db),
    auth: AuthContext = Depends(get_auth_context),
) -> list[AgentShareRead]:
    agent, _ = _require_agent_access(session, auth, agent_id, minimum_role=AgentAccessRole.owner)
    shares: list[AgentShareRead] = []
    if agent.org_access_role is not None:
        shares.append(
            AgentShareRead(
                kind="org",
                role=agent.org_access_role,
                created_at=agent.updated_at,
                updated_at=agent.updated_at,
            )
        )
    team_rows = session.exec(
        select(AgentTeamAccess, Team)
        .join(Team, Team.id == AgentTeamAccess.team_id)
        .where(AgentTeamAccess.agent_id == agent_id)
        .order_by(Team.name.asc())
    ).all()
    for access, team in team_rows:
        shares.append(
            AgentShareRead(
                kind="team",
                team_id=str(team.id),
                team_name=team.name,
                role=access.role,
                created_at=access.created_at,
                updated_at=access.updated_at,
            )
        )
    user_rows = session.exec(
        select(AgentAccess, User)
        .join(User, User.id == AgentAccess.user_id)
        .where(AgentAccess.agent_id == agent_id)
        .order_by(AgentAccess.created_at.asc())
    ).all()
    for access, user in user_rows:
        shares.append(
            AgentShareRead(
                kind="user",
                user_id=str(user.id),
                email=user.email,
                display_name=user.display_name,
                role=access.role,
                created_at=access.created_at,
                updated_at=access.updated_at,
            )
        )
    return shares


@router.get("/{agent_id}/share-suggestions", response_model=list[AgentShareSuggestion])
def share_suggestions(
    agent_id: UUID,
    q: str | None = None,
    limit: int = 10,
    session: Session = Depends(get_db),
    auth: AuthContext = Depends(get_auth_context),
) -> list[AgentShareSuggestion]:
    agent, _ = _require_agent_access(session, auth, agent_id, minimum_role=AgentAccessRole.owner)
    capped_limit = max(1, min(limit, 25))
    needle = (q or "").strip().lower()
    suggestions: list[AgentShareSuggestion] = []

    org_labels = ("organization", "organisation", "everyone", "org")
    if agent.org_access_role is None and (not needle or any(label.startswith(needle) or needle in label for label in org_labels)):
        suggestions.append(AgentShareSuggestion(kind="org"))

    already_teams = set(
        session.exec(
            select(AgentTeamAccess.team_id).where(AgentTeamAccess.agent_id == agent_id)
        ).all()
    )
    team_statement = select(Team).where(
        Team.org_id == auth.org_id,
        Team.is_default.is_(False),
    )
    if needle:
        team_statement = team_statement.where(func.lower(Team.name).like(f"%{needle}%"))
    for team in session.exec(team_statement.order_by(Team.name.asc())).all():
        if team.id in already_teams:
            continue
        suggestions.append(
            AgentShareSuggestion(kind="team", team_id=str(team.id), team_name=team.name)
        )
        if len(suggestions) >= capped_limit:
            return suggestions

    already_shared = set(
        session.exec(
            select(AgentAccess.user_id).where(AgentAccess.agent_id == agent_id)
        ).all()
    )
    statement = (
        select(User)
        .join(OrgMembership, OrgMembership.user_id == User.id)
        .where(OrgMembership.org_id == auth.org_id)
    )
    if needle:
        pattern = f"%{needle}%"
        statement = statement.where(
            func.lower(User.email).like(pattern)
            | func.lower(func.coalesce(User.display_name, "")).like(pattern)
            | func.lower(func.coalesce(User.username, "")).like(pattern)
        )
    statement = statement.order_by(User.email.asc()).limit(capped_limit + len(already_shared) + 1)
    for user in session.exec(statement).all():
        if user.id in already_shared:
            continue
        suggestions.append(
            AgentShareSuggestion(
                kind="user",
                user_id=str(user.id),
                email=user.email,
                display_name=user.display_name,
            )
        )
        if len(suggestions) >= capped_limit:
            break
    return suggestions


@router.post("/{agent_id}/shares", response_model=AgentShareRead)
def share_agent(
    agent_id: UUID,
    payload: AgentShareRequest,
    session: Session = Depends(get_db),
    auth: AuthContext = Depends(get_auth_context),
) -> AgentShareRead:
    agent, _ = _require_agent_access(session, auth, agent_id, minimum_role=AgentAccessRole.owner)
    now = datetime.utcnow()

    if payload.org:
        agent.org_access_role = payload.role
        _refresh_agent_visibility(session, agent)
        session.commit()
        session.refresh(agent)
        return AgentShareRead(
            kind="org",
            role=agent.org_access_role,
            created_at=agent.updated_at,
            updated_at=agent.updated_at,
        )

    if payload.team_id:
        team = _validate_org_team(session, auth, _parse_uuid(payload.team_id, "Invalid team id"))
        access = session.exec(
            select(AgentTeamAccess).where(
                AgentTeamAccess.agent_id == agent.id,
                AgentTeamAccess.team_id == team.id,
            )
        ).first()
        if access:
            access.role = payload.role
            access.updated_at = now
        else:
            access = AgentTeamAccess(
                agent_id=agent.id,
                team_id=team.id,
                role=payload.role,
                granted_by_user_id=auth.user.id,
                created_at=now,
                updated_at=now,
            )
        session.add(access)
        _refresh_agent_visibility(session, agent)
        session.commit()
        session.refresh(access)
        return AgentShareRead(
            kind="team",
            team_id=str(team.id),
            team_name=team.name,
            role=access.role,
            created_at=access.created_at,
            updated_at=access.updated_at,
        )

    target_user_id = _parse_uuid(payload.user_id or "", "Invalid user id")
    target_user = session.exec(select(User).where(User.id == target_user_id)).first()
    if not target_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    membership = session.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == auth.org_id,
            OrgMembership.user_id == target_user.id,
        )
    ).first()
    if not membership:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User must belong to the same organization",
        )

    access = session.exec(
        select(AgentAccess).where(
            AgentAccess.agent_id == agent.id,
            AgentAccess.user_id == target_user.id,
        )
    ).first()
    if access:
        access.role = payload.role
        access.updated_at = now
    else:
        access = AgentAccess(
            agent_id=agent.id,
            user_id=target_user.id,
            role=payload.role,
            granted_by_user_id=auth.user.id,
            created_at=now,
            updated_at=now,
        )
    session.add(access)
    _refresh_agent_visibility(session, agent)
    session.commit()
    session.refresh(access)
    return AgentShareRead(
        kind="user",
        user_id=str(target_user.id),
        email=target_user.email,
        display_name=target_user.display_name,
        role=access.role,
        created_at=access.created_at,
        updated_at=access.updated_at,
    )


@router.delete("/{agent_id}/shares/org", status_code=status.HTTP_204_NO_CONTENT)
def unshare_agent_org(
    agent_id: UUID,
    session: Session = Depends(get_db),
    auth: AuthContext = Depends(get_auth_context),
) -> None:
    agent, _ = _require_agent_access(session, auth, agent_id, minimum_role=AgentAccessRole.owner)
    agent.org_access_role = None
    _refresh_agent_visibility(session, agent)
    session.commit()


@router.delete("/{agent_id}/shares/teams/{team_id}", status_code=status.HTTP_204_NO_CONTENT)
def unshare_agent_team(
    agent_id: UUID,
    team_id: UUID,
    session: Session = Depends(get_db),
    auth: AuthContext = Depends(get_auth_context),
) -> None:
    agent, _ = _require_agent_access(session, auth, agent_id, minimum_role=AgentAccessRole.owner)
    access = session.exec(
        select(AgentTeamAccess).where(
            AgentTeamAccess.agent_id == agent.id,
            AgentTeamAccess.team_id == team_id,
        )
    ).first()
    if access:
        session.delete(access)
        session.flush()
    _refresh_agent_visibility(session, agent)
    session.commit()


@router.delete("/{agent_id}/shares/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def unshare_agent(
    agent_id: UUID,
    user_id: UUID,
    session: Session = Depends(get_db),
    auth: AuthContext = Depends(get_auth_context),
) -> None:
    agent, _ = _require_agent_access(session, auth, agent_id, minimum_role=AgentAccessRole.owner)
    if user_id == agent.owner_user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Owner cannot remove self from agent",
        )
    access = session.exec(
        select(AgentAccess).where(
            AgentAccess.agent_id == agent.id,
            AgentAccess.user_id == user_id,
        )
    ).first()
    if access:
        session.delete(access)
        session.flush()
    _refresh_agent_visibility(session, agent)
    session.commit()

