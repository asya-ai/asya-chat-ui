from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_
from sqlmodel import Session, select

from app.api.deps import AuthContext, get_auth_context, get_db
from app.models import (
    Agent,
    AgentAccess,
    AgentAccessRole,
    OrgMembership,
    Prompt,
    PromptTeamShare,
    PromptUserShare,
    PromptVisibility,
    Team,
    TeamMembership,
    User,
)

router = APIRouter(prefix="/prompts", tags=["prompts"])

_ROLE_ORDER = {
    AgentAccessRole.viewer: 1,
    AgentAccessRole.editor: 2,
    AgentAccessRole.owner: 3,
}


class PromptCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    body: str = Field(min_length=1)
    visibility: PromptVisibility = PromptVisibility.private
    team_ids: list[str] = Field(default_factory=list)
    user_ids: list[str] = Field(default_factory=list)
    agent_id: str | None = None


class PromptUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    body: str | None = Field(default=None, min_length=1)
    visibility: PromptVisibility | None = None
    team_ids: list[str] | None = None
    user_ids: list[str] | None = None
    agent_id: str | None = None
    clear_agent: bool = False


class PromptSharedUser(BaseModel):
    user_id: str
    email: str
    display_name: str | None = None


class PromptRead(BaseModel):
    id: str
    name: str
    description: str | None
    body: str
    visibility: PromptVisibility
    team_ids: list[str]
    user_ids: list[str]
    users: list[PromptSharedUser]
    agent_id: str | None
    is_owner: bool
    created_at: datetime
    updated_at: datetime


class PromptShareSuggestion(BaseModel):
    user_id: str
    email: str
    display_name: str | None = None


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


def _require_project_editor(session: Session, auth: AuthContext, agent_id: UUID) -> Agent:
    agent = session.exec(
        select(Agent).where(Agent.id == agent_id, Agent.org_id == auth.org_id)
    ).first()
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    role = _get_agent_role(session, agent.id, auth.user.id)
    if role is None or _ROLE_ORDER[role] < _ROLE_ORDER[AgentAccessRole.editor]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Project editor access required",
        )
    return agent


def _user_team_ids(session: Session, org_id: UUID, user_id: UUID) -> set[UUID]:
    return set(
        session.exec(
            select(Team.id)
            .join(TeamMembership, TeamMembership.team_id == Team.id)
            .where(
                Team.org_id == org_id,
                Team.is_default.is_(False),
                TeamMembership.user_id == user_id,
            )
        ).all()
    )


def _validate_team_ids(
    session: Session,
    auth: AuthContext,
    team_ids: list[str],
    *,
    user_team_ids: set[UUID] | None = None,
) -> list[UUID]:
    if not team_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one team is required for team visibility",
        )
    allowed = user_team_ids if user_team_ids is not None else _user_team_ids(
        session, auth.org_id, auth.user.id
    )
    parsed: list[UUID] = []
    seen: set[UUID] = set()
    for raw in team_ids:
        team_id = _parse_uuid(raw, "Invalid team id")
        if team_id in seen:
            continue
        seen.add(team_id)
        if team_id not in allowed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You can only share with teams you belong to",
            )
        parsed.append(team_id)
    return parsed


def _validate_user_ids(
    session: Session,
    auth: AuthContext,
    user_ids: list[str],
) -> list[UUID]:
    if not user_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one user is required for users visibility",
        )
    parsed: list[UUID] = []
    seen: set[UUID] = set()
    for raw in user_ids:
        user_id = _parse_uuid(raw, "Invalid user id")
        if user_id in seen:
            continue
        seen.add(user_id)
        if user_id == auth.user.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You cannot share a prompt with yourself",
            )
        membership = session.exec(
            select(OrgMembership).where(
                OrgMembership.org_id == auth.org_id,
                OrgMembership.user_id == user_id,
            )
        ).first()
        if not membership:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User must belong to the same organization",
            )
        parsed.append(user_id)
    return parsed


def _replace_team_shares(
    session: Session, prompt: Prompt, team_ids: list[UUID]
) -> None:
    existing = session.exec(
        select(PromptTeamShare).where(PromptTeamShare.prompt_id == prompt.id)
    ).all()
    for share in existing:
        session.delete(share)
    for team_id in team_ids:
        session.add(PromptTeamShare(prompt_id=prompt.id, team_id=team_id))


def _clear_team_shares(session: Session, prompt: Prompt) -> None:
    existing = session.exec(
        select(PromptTeamShare).where(PromptTeamShare.prompt_id == prompt.id)
    ).all()
    for share in existing:
        session.delete(share)


def _replace_user_shares(
    session: Session, prompt: Prompt, user_ids: list[UUID]
) -> None:
    existing = session.exec(
        select(PromptUserShare).where(PromptUserShare.prompt_id == prompt.id)
    ).all()
    for share in existing:
        session.delete(share)
    for user_id in user_ids:
        session.add(PromptUserShare(prompt_id=prompt.id, user_id=user_id))


def _clear_user_shares(session: Session, prompt: Prompt) -> None:
    existing = session.exec(
        select(PromptUserShare).where(PromptUserShare.prompt_id == prompt.id)
    ).all()
    for share in existing:
        session.delete(share)


def _prompt_team_ids(session: Session, prompt_id: UUID) -> list[str]:
    ids = session.exec(
        select(PromptTeamShare.team_id).where(PromptTeamShare.prompt_id == prompt_id)
    ).all()
    return [str(team_id) for team_id in ids]


def _prompt_shared_users(session: Session, prompt_id: UUID) -> list[PromptSharedUser]:
    rows = session.exec(
        select(User)
        .join(PromptUserShare, PromptUserShare.user_id == User.id)
        .where(PromptUserShare.prompt_id == prompt_id)
        .order_by(User.email.asc())
    ).all()
    return [
        PromptSharedUser(
            user_id=str(user.id),
            email=user.email,
            display_name=user.display_name,
        )
        for user in rows
    ]


def _to_prompt_read(session: Session, prompt: Prompt, user_id: UUID) -> PromptRead:
    shared_users = _prompt_shared_users(session, prompt.id)
    return PromptRead(
        id=str(prompt.id),
        name=prompt.name,
        description=prompt.description,
        body=prompt.body,
        visibility=prompt.visibility,
        team_ids=_prompt_team_ids(session, prompt.id),
        user_ids=[user.user_id for user in shared_users],
        users=shared_users,
        agent_id=str(prompt.agent_id) if prompt.agent_id else None,
        is_owner=prompt.owner_user_id == user_id,
        created_at=prompt.created_at,
        updated_at=prompt.updated_at,
    )


def _accessible_agent_ids(session: Session, org_id: UUID, user_id: UUID) -> set[UUID]:
    return set(
        session.exec(
            select(AgentAccess.agent_id)
            .join(Agent, Agent.id == AgentAccess.agent_id)
            .where(Agent.org_id == org_id, AgentAccess.user_id == user_id)
        ).all()
    )


def _can_see_prompt(
    prompt: Prompt,
    user_id: UUID,
    user_team_ids: set[UUID],
    shared_team_ids: set[UUID],
    shared_with_user: bool,
    accessible_agent_ids: set[UUID],
) -> bool:
    if prompt.owner_user_id == user_id:
        return True
    if prompt.visibility == PromptVisibility.org:
        return True
    if prompt.visibility == PromptVisibility.team:
        return bool(shared_team_ids & user_team_ids)
    if prompt.visibility == PromptVisibility.users:
        return shared_with_user
    if prompt.visibility == PromptVisibility.project:
        return (
            prompt.agent_id is not None and prompt.agent_id in accessible_agent_ids
        )
    return False


def _require_owned_prompt(
    session: Session, auth: AuthContext, prompt_id: UUID
) -> Prompt:
    prompt = session.exec(
        select(Prompt).where(Prompt.id == prompt_id, Prompt.org_id == auth.org_id)
    ).first()
    if not prompt:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prompt not found")
    if prompt.owner_user_id != auth.user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the prompt owner can modify it",
        )
    return prompt


def _resolve_agent_id(
    session: Session, auth: AuthContext, agent_id: str | None
) -> UUID | None:
    if agent_id is None or agent_id == "":
        return None
    parsed = _parse_uuid(agent_id, "Invalid project id")
    _require_project_editor(session, auth, parsed)
    return parsed


def _validate_visibility_location(
    visibility: PromptVisibility, agent_id: UUID | None
) -> None:
    if visibility == PromptVisibility.project and agent_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Project visibility requires a project location",
        )


def _apply_share_targets(
    session: Session,
    auth: AuthContext,
    prompt: Prompt,
    visibility: PromptVisibility,
    *,
    team_ids: list[str] | None,
    user_ids: list[str] | None,
    user_team_ids: set[UUID],
    require_existing_when_missing: bool,
) -> None:
    if visibility == PromptVisibility.team:
        if team_ids is not None:
            team_uuids = _validate_team_ids(
                session, auth, team_ids, user_team_ids=user_team_ids
            )
            _replace_team_shares(session, prompt, team_uuids)
        elif require_existing_when_missing and not _prompt_team_ids(session, prompt.id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="At least one team is required for team visibility",
            )
        _clear_user_shares(session, prompt)
        return

    if visibility == PromptVisibility.users:
        if user_ids is not None:
            user_uuids = _validate_user_ids(session, auth, user_ids)
            _replace_user_shares(session, prompt, user_uuids)
        elif require_existing_when_missing and not _prompt_shared_users(session, prompt.id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="At least one user is required for users visibility",
            )
        _clear_team_shares(session, prompt)
        return

    if team_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="team_ids are only allowed when visibility is team",
        )
    if user_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="user_ids are only allowed when visibility is users",
        )
    _clear_team_shares(session, prompt)
    _clear_user_shares(session, prompt)


@router.get("/share-suggestions", response_model=list[PromptShareSuggestion])
def share_suggestions(
    q: str | None = None,
    limit: int = 10,
    session: Session = Depends(get_db),
    auth: AuthContext = Depends(get_auth_context),
) -> list[PromptShareSuggestion]:
    capped_limit = max(1, min(limit, 25))
    statement = (
        select(User)
        .join(OrgMembership, OrgMembership.user_id == User.id)
        .where(
            OrgMembership.org_id == auth.org_id,
            User.id != auth.user.id,
        )
    )
    needle = (q or "").strip().lower()
    if needle:
        pattern = f"%{needle}%"
        statement = statement.where(
            func.lower(User.email).like(pattern)
            | func.lower(func.coalesce(User.display_name, "")).like(pattern)
            | func.lower(func.coalesce(User.username, "")).like(pattern)
        )
    statement = statement.order_by(User.email.asc()).limit(capped_limit)
    return [
        PromptShareSuggestion(
            user_id=str(user.id),
            email=user.email,
            display_name=user.display_name,
        )
        for user in session.exec(statement).all()
    ]


@router.get("", response_model=list[PromptRead])
def list_prompts(
    agent_id: Annotated[str | None, Query()] = None,
    context_agent_id: Annotated[str | None, Query()] = None,
    session: Session = Depends(get_db),
    auth: AuthContext = Depends(get_auth_context),
) -> list[PromptRead]:
    user_team_ids = _user_team_ids(session, auth.org_id, auth.user.id)
    accessible_agent_ids = _accessible_agent_ids(session, auth.org_id, auth.user.id)
    statement = select(Prompt).where(Prompt.org_id == auth.org_id)

    # Exact location filter (optional)
    if agent_id is not None:
        if agent_id == "" or agent_id.lower() == "null":
            statement = statement.where(Prompt.agent_id.is_(None))
        else:
            parsed_agent = _parse_uuid(agent_id, "Invalid project id")
            statement = statement.where(Prompt.agent_id == parsed_agent)
    elif context_agent_id is not None:
        # Offer profile prompts always; project prompts only in that project's chats
        if context_agent_id == "" or context_agent_id.lower() == "null":
            statement = statement.where(Prompt.agent_id.is_(None))
        else:
            parsed_context = _parse_uuid(context_agent_id, "Invalid project id")
            statement = statement.where(
                or_(Prompt.agent_id.is_(None), Prompt.agent_id == parsed_context)
            )
    else:
        # Default usable list outside a project: profile prompts only
        statement = statement.where(Prompt.agent_id.is_(None))

    clauses = [
        Prompt.owner_user_id == auth.user.id,
        Prompt.visibility == PromptVisibility.org,
        Prompt.id.in_(
            select(PromptUserShare.prompt_id).where(
                PromptUserShare.user_id == auth.user.id
            )
        ),
    ]
    if user_team_ids:
        clauses.append(
            Prompt.id.in_(
                select(PromptTeamShare.prompt_id).where(
                    PromptTeamShare.team_id.in_(user_team_ids)
                )
            )
        )
    if accessible_agent_ids:
        clauses.append(
            and_(
                Prompt.visibility == PromptVisibility.project,
                Prompt.agent_id.in_(accessible_agent_ids),
            )
        )
    statement = statement.where(or_(*clauses)).order_by(
        Prompt.updated_at.desc(), Prompt.name.asc()
    )
    prompts = session.exec(statement).all()

    team_share_map: dict[UUID, set[UUID]] = {}
    user_share_ids: set[UUID] = set()
    if prompts:
        prompt_ids = [p.id for p in prompts]
        team_shares = session.exec(
            select(PromptTeamShare).where(PromptTeamShare.prompt_id.in_(prompt_ids))
        ).all()
        for share in team_shares:
            team_share_map.setdefault(share.prompt_id, set()).add(share.team_id)
        user_share_ids = set(
            session.exec(
                select(PromptUserShare.prompt_id).where(
                    PromptUserShare.prompt_id.in_(prompt_ids),
                    PromptUserShare.user_id == auth.user.id,
                )
            ).all()
        )

    visible = [
        prompt
        for prompt in prompts
        if _can_see_prompt(
            prompt,
            auth.user.id,
            user_team_ids,
            team_share_map.get(prompt.id, set()),
            prompt.id in user_share_ids,
            accessible_agent_ids,
        )
    ]
    return [_to_prompt_read(session, prompt, auth.user.id) for prompt in visible]


@router.post("", response_model=PromptRead, status_code=status.HTTP_201_CREATED)
def create_prompt(
    payload: PromptCreateRequest,
    session: Session = Depends(get_db),
    auth: AuthContext = Depends(get_auth_context),
) -> PromptRead:
    agent_uuid = _resolve_agent_id(session, auth, payload.agent_id)
    _validate_visibility_location(payload.visibility, agent_uuid)
    user_team_ids = _user_team_ids(session, auth.org_id, auth.user.id)

    now = datetime.utcnow()
    prompt = Prompt(
        org_id=auth.org_id,
        owner_user_id=auth.user.id,
        agent_id=agent_uuid,
        name=payload.name.strip(),
        description=(payload.description.strip() if payload.description else None) or None,
        body=payload.body.strip(),
        visibility=payload.visibility,
        created_at=now,
        updated_at=now,
    )
    session.add(prompt)
    session.flush()
    _apply_share_targets(
        session,
        auth,
        prompt,
        payload.visibility,
        team_ids=payload.team_ids,
        user_ids=payload.user_ids,
        user_team_ids=user_team_ids,
        require_existing_when_missing=False,
    )
    session.commit()
    session.refresh(prompt)
    return _to_prompt_read(session, prompt, auth.user.id)


@router.patch("/{prompt_id}", response_model=PromptRead)
def update_prompt(
    prompt_id: UUID,
    payload: PromptUpdateRequest,
    session: Session = Depends(get_db),
    auth: AuthContext = Depends(get_auth_context),
) -> PromptRead:
    prompt = _require_owned_prompt(session, auth, prompt_id)
    user_team_ids = _user_team_ids(session, auth.org_id, auth.user.id)

    if payload.name is not None:
        prompt.name = payload.name.strip()
    if payload.description is not None:
        cleaned = payload.description.strip()
        prompt.description = cleaned or None
    if payload.body is not None:
        prompt.body = payload.body.strip()

    if payload.clear_agent:
        prompt.agent_id = None
    elif payload.agent_id is not None:
        prompt.agent_id = _resolve_agent_id(session, auth, payload.agent_id)

    next_visibility = payload.visibility if payload.visibility is not None else prompt.visibility
    if payload.visibility is not None:
        prompt.visibility = payload.visibility

    _validate_visibility_location(next_visibility, prompt.agent_id)
    _apply_share_targets(
        session,
        auth,
        prompt,
        next_visibility,
        team_ids=payload.team_ids,
        user_ids=payload.user_ids,
        user_team_ids=user_team_ids,
        require_existing_when_missing=True,
    )

    prompt.updated_at = datetime.utcnow()
    session.add(prompt)
    session.commit()
    session.refresh(prompt)
    return _to_prompt_read(session, prompt, auth.user.id)


@router.delete("/{prompt_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_prompt(
    prompt_id: UUID,
    session: Session = Depends(get_db),
    auth: AuthContext = Depends(get_auth_context),
) -> None:
    prompt = _require_owned_prompt(session, auth, prompt_id)
    _clear_team_shares(session, prompt)
    _clear_user_shares(session, prompt)
    session.delete(prompt)
    session.commit()
