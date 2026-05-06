from __future__ import annotations

import base64
import binascii
import ipaddress
import re
import socket
from datetime import datetime
from html import unescape
from urllib.parse import urlparse
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.api.deps import AuthContext, get_auth_context, get_db
from app.models import (
    Agent,
    AgentAccess,
    AgentAccessRole,
    AgentChunk,
    AgentEmbedding,
    AgentSource,
    AgentSourceKind,
    AgentSourceStatus,
    AgentVisibility,
    OrgMembership,
    User,
)
from app.services.agents.source_parsing import extract_text_from_file
from app.workers.celery_app import celery_app

router = APIRouter(prefix="/agents", tags=["agents"])


def _enqueue_source_reindex_task(source_id: UUID) -> None:
    celery_app.send_task(
        "chatui.reindex_agent_source",
        args=[str(source_id)],
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
    created_at: datetime
    updated_at: datetime


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
    created_at: datetime
    updated_at: datetime


class AgentShareRequest(BaseModel):
    email: str
    role: AgentAccessRole = AgentAccessRole.viewer


class AgentShareRead(BaseModel):
    user_id: str
    email: str
    role: AgentAccessRole
    created_at: datetime
    updated_at: datetime


def _to_agent_read(agent: Agent, user_id: UUID, role: AgentAccessRole) -> AgentRead:
    return AgentRead(
        id=str(agent.id),
        name=agent.name,
        description=agent.description,
        preferred_model_id=str(agent.preferred_model_id) if agent.preferred_model_id else None,
        master_prompt=agent.master_prompt,
        visibility=agent.visibility,
        is_owner=agent.owner_user_id == user_id,
        role=role,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
    )


def _parse_uuid(value: str, detail: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail) from exc


def _get_agent_role(session: Session, agent_id: UUID, user_id: UUID) -> AgentAccessRole | None:
    access = session.exec(
        select(AgentAccess).where(
            AgentAccess.agent_id == agent_id, AgentAccess.user_id == user_id
        )
    ).first()
    return access.role if access else None


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

    role = _get_agent_role(session, agent.id, auth.user.id)
    if role is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Agent access required")

    role_order = {
        AgentAccessRole.viewer: 1,
        AgentAccessRole.editor: 2,
        AgentAccessRole.owner: 3,
    }
    if role_order[role] < role_order[minimum_role]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient agent permissions",
        )
    return agent, role


def _decode_source_text(payload: AgentSourceCreateRequest) -> str:
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
        try:
            raw = base64.b64decode(payload.data_base64, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid base64 source content",
            ) from exc
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
        try:
            raw = base64.b64decode(payload.data_base64, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid base64 source content",
            ) from exc
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


def _is_private_hostname(hostname: str) -> bool:
    if hostname in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}:
        return True
    if hostname.endswith((".local", ".internal")):
        return True
    try:
        ip = ipaddress.ip_address(hostname)
        return not ip.is_global
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(hostname, None)
    except Exception:
        return True
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            return True
    return False


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
    if not parsed.hostname or _is_private_hostname(parsed.hostname):
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


def _to_source_read(source: AgentSource) -> AgentSourceRead:
    metadata = source.metadata_json if isinstance(source.metadata_json, dict) else {}
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
        created_at=source.created_at,
        updated_at=source.updated_at,
    )


@router.get("", response_model=list[AgentRead])
def list_agents(
    session: Session = Depends(get_db),
    auth: AuthContext = Depends(get_auth_context),
) -> list[AgentRead]:
    accesses = session.exec(
        select(AgentAccess, Agent)
        .join(Agent, Agent.id == AgentAccess.agent_id)
        .where(Agent.org_id == auth.org_id, AgentAccess.user_id == auth.user.id)
        .order_by(Agent.updated_at.desc())
    ).all()
    return [_to_agent_read(agent, auth.user.id, access.role) for access, agent in accesses]


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

    access_rows = session.exec(
        select(AgentAccess).where(AgentAccess.agent_id == agent.id)
    ).all()
    for row in access_rows:
        session.delete(row)

    sources = session.exec(select(AgentSource).where(AgentSource.agent_id == agent.id)).all()
    source_ids = [source.id for source in sources]
    if source_ids:
        chunks = session.exec(select(AgentChunk).where(AgentChunk.source_id.in_(source_ids))).all()
        chunk_ids = [chunk.id for chunk in chunks]
        if chunk_ids:
            embeddings = session.exec(
                select(AgentEmbedding).where(AgentEmbedding.chunk_id.in_(chunk_ids))
            ).all()
            for embedding in embeddings:
                session.delete(embedding)
        for chunk in chunks:
            session.delete(chunk)
    for source in sources:
        session.delete(source)

    session.delete(agent)
    session.commit()


@router.get("/{agent_id}/sources", response_model=list[AgentSourceRead])
def list_sources(
    agent_id: UUID,
    session: Session = Depends(get_db),
    auth: AuthContext = Depends(get_auth_context),
) -> list[AgentSourceRead]:
    _require_agent_access(session, auth, agent_id, minimum_role=AgentAccessRole.viewer)
    items = session.exec(
        select(AgentSource)
        .where(AgentSource.agent_id == agent_id)
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
    now = datetime.utcnow()
    source = AgentSource(
        agent_id=agent_id,
        kind=payload.kind,
        title=(payload.title.strip() if payload.title else (payload.file_name or payload.url or "Source")),
        url=payload.url,
        file_name=payload.file_name,
        content_type=payload.content_type,
        content_text=_decode_source_text(payload),
        status=AgentSourceStatus.queued,
        created_at=now,
        updated_at=now,
    )
    session.add(source)
    session.flush()
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

    chunks = session.exec(select(AgentChunk).where(AgentChunk.source_id == source.id)).all()
    chunk_ids = [chunk.id for chunk in chunks]
    if chunk_ids:
        embeddings = session.exec(
            select(AgentEmbedding).where(AgentEmbedding.chunk_id.in_(chunk_ids))
        ).all()
        for embedding in embeddings:
            session.delete(embedding)
    for chunk in chunks:
        session.delete(chunk)
    session.delete(source)
    session.commit()


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
    source.status = AgentSourceStatus.queued
    source.error_message = None
    source.updated_at = datetime.utcnow()
    session.add(source)
    session.commit()
    session.refresh(source)
    _enqueue_source_reindex_task(source.id)
    return _to_source_read(source)


@router.get("/{agent_id}/shares", response_model=list[AgentShareRead])
def list_shares(
    agent_id: UUID,
    session: Session = Depends(get_db),
    auth: AuthContext = Depends(get_auth_context),
) -> list[AgentShareRead]:
    _require_agent_access(session, auth, agent_id, minimum_role=AgentAccessRole.owner)
    rows = session.exec(
        select(AgentAccess, User)
        .join(User, User.id == AgentAccess.user_id)
        .where(AgentAccess.agent_id == agent_id)
        .order_by(AgentAccess.created_at.asc())
    ).all()
    return [
        AgentShareRead(
            user_id=str(user.id),
            email=user.email,
            role=access.role,
            created_at=access.created_at,
            updated_at=access.updated_at,
        )
        for access, user in rows
    ]


@router.post("/{agent_id}/shares", response_model=AgentShareRead)
def share_agent(
    agent_id: UUID,
    payload: AgentShareRequest,
    session: Session = Depends(get_db),
    auth: AuthContext = Depends(get_auth_context),
) -> AgentShareRead:
    _require_agent_access(session, auth, agent_id, minimum_role=AgentAccessRole.owner)
    target_user = session.exec(select(User).where(User.email == payload.email.strip().lower())).first()
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

    now = datetime.utcnow()
    access = session.exec(
        select(AgentAccess).where(
            AgentAccess.agent_id == agent_id,
            AgentAccess.user_id == target_user.id,
        )
    ).first()
    if access:
        access.role = payload.role
        access.updated_at = now
    else:
        access = AgentAccess(
            agent_id=agent_id,
            user_id=target_user.id,
            role=payload.role,
            granted_by_user_id=auth.user.id,
            created_at=now,
            updated_at=now,
        )
    session.add(access)

    agent = session.exec(select(Agent).where(Agent.id == agent_id)).first()
    if agent and agent.visibility == AgentVisibility.private:
        agent.visibility = AgentVisibility.shared
        agent.updated_at = now
        session.add(agent)

    session.commit()
    session.refresh(access)
    return AgentShareRead(
        user_id=str(target_user.id),
        email=target_user.email,
        role=access.role,
        created_at=access.created_at,
        updated_at=access.updated_at,
    )


@router.delete("/{agent_id}/shares/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def unshare_agent(
    agent_id: UUID,
    user_id: UUID,
    session: Session = Depends(get_db),
    auth: AuthContext = Depends(get_auth_context),
) -> None:
    _require_agent_access(session, auth, agent_id, minimum_role=AgentAccessRole.owner)
    if user_id == auth.user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Owner cannot remove self from agent",
        )
    access = session.exec(
        select(AgentAccess).where(
            AgentAccess.agent_id == agent_id,
            AgentAccess.user_id == user_id,
        )
    ).first()
    if access:
        session.delete(access)
        session.commit()
