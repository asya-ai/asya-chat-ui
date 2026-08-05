from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlmodel import Session, select, func

from app.api.deps import get_current_user, get_db
from app.models import (
    ChatModel,
    Org,
    OrgMembership,
    OrgModel,
    Team,
    TeamMembership,
    TeamModel,
    User,
)
from app.services.org_service import require_org_admin
from app.services.team_service import (
    TEAM_SOURCE_MANUAL,
    ensure_default_team,
)

router = APIRouter(prefix="/orgs", tags=["teams"])


class TeamCreateRequest(BaseModel):
    name: str
    oidc_group: str | None = None


class TeamUpdateRequest(BaseModel):
    name: str | None = None
    oidc_group: str | None = None


class TeamModelUpdateRequest(BaseModel):
    model_id: str
    is_enabled: bool = True


class TeamMembersUpdateRequest(BaseModel):
    user_ids: list[str]


class TeamMemberRead(BaseModel):
    user_id: str
    email: str
    username: str | None = None
    display_name: str | None = None
    source: str


class TeamModelRead(BaseModel):
    model_id: str
    display_name: str
    provider: str
    model_name: str
    is_enabled: bool


class TeamRead(BaseModel):
    id: str
    name: str
    is_default: bool
    oidc_group: str | None = None
    member_count: int = 0
    model_count: int = 0


def _ensure_org_admin_or_super(
    session: Session, org_id: UUID, current_user: User
) -> None:
    if current_user.is_super_admin:
        return
    require_org_admin(session, org_id, current_user.id)


def _parse_org_id(org_id: str) -> UUID:
    try:
        return UUID(org_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid org id"
        ) from exc


def _get_org(session: Session, org_uuid: UUID) -> Org:
    org = session.exec(select(Org).where(Org.id == org_uuid)).first()
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Org not found")
    return org


def _get_team(session: Session, org_uuid: UUID, team_id: str) -> Team:
    try:
        team_uuid = UUID(team_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid team id"
        ) from exc
    team = session.exec(
        select(Team).where(Team.id == team_uuid, Team.org_id == org_uuid)
    ).first()
    if not team:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    return team


def _normalize_oidc_group(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _team_read(session: Session, team: Team) -> TeamRead:
    member_count = session.exec(
        select(func.count()).select_from(TeamMembership).where(
            TeamMembership.team_id == team.id
        )
    ).one()
    model_count = session.exec(
        select(func.count()).select_from(TeamModel).where(
            TeamModel.team_id == team.id, TeamModel.is_enabled.is_(True)
        )
    ).one()
    return TeamRead(
        id=str(team.id),
        name=team.name,
        is_default=team.is_default,
        oidc_group=team.oidc_group,
        member_count=int(member_count or 0),
        model_count=int(model_count or 0),
    )


def _ensure_unique_oidc_group(
    session: Session, org_id: UUID, oidc_group: str | None, *, exclude_team_id: UUID | None = None
) -> None:
    if not oidc_group:
        return
    existing = session.exec(
        select(Team).where(Team.org_id == org_id, Team.oidc_group == oidc_group)
    ).first()
    if existing and existing.id != exclude_team_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="OIDC group already assigned to another team",
        )


@router.get("/{org_id}/teams", response_model=list[TeamRead])
def list_teams(
    org_id: str,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[TeamRead]:
    org_uuid = _parse_org_id(org_id)
    _ensure_org_admin_or_super(session, org_uuid, current_user)
    _get_org(session, org_uuid)
    ensure_default_team(session, org_uuid)
    teams = session.exec(
        select(Team).where(Team.org_id == org_uuid).order_by(Team.is_default.desc(), Team.name)
    ).all()
    return [_team_read(session, team) for team in teams]


@router.post("/{org_id}/teams", response_model=TeamRead)
def create_team(
    org_id: str,
    payload: TeamCreateRequest,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TeamRead:
    org_uuid = _parse_org_id(org_id)
    _ensure_org_admin_or_super(session, org_uuid, current_user)
    _get_org(session, org_uuid)
    ensure_default_team(session, org_uuid)

    name = payload.name.strip()
    if not name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Team name is required"
        )
    oidc_group = _normalize_oidc_group(payload.oidc_group)
    _ensure_unique_oidc_group(session, org_uuid, oidc_group)

    team = Team(org_id=org_uuid, name=name, is_default=False, oidc_group=oidc_group)
    session.add(team)
    session.commit()
    session.refresh(team)
    return _team_read(session, team)


@router.patch("/{org_id}/teams/{team_id}", response_model=TeamRead)
def update_team(
    org_id: str,
    team_id: str,
    payload: TeamUpdateRequest,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TeamRead:
    org_uuid = _parse_org_id(org_id)
    _ensure_org_admin_or_super(session, org_uuid, current_user)
    team = _get_team(session, org_uuid, team_id)
    updates = payload.model_dump(exclude_unset=True)
    if "name" in updates:
        name = (updates["name"] or "").strip()
        if not name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Team name is required"
            )
        team.name = name
    if "oidc_group" in updates:
        if team.is_default and updates["oidc_group"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Default team cannot have an OIDC group",
            )
        oidc_group = _normalize_oidc_group(updates["oidc_group"])
        _ensure_unique_oidc_group(
            session, org_uuid, oidc_group, exclude_team_id=team.id
        )
        team.oidc_group = oidc_group
    session.add(team)
    session.commit()
    session.refresh(team)
    return _team_read(session, team)


@router.delete("/{org_id}/teams/{team_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_team(
    org_id: str,
    team_id: str,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    org_uuid = _parse_org_id(org_id)
    _ensure_org_admin_or_super(session, org_uuid, current_user)
    team = _get_team(session, org_uuid, team_id)
    if team.is_default:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete the default team",
        )
    for membership in session.exec(
        select(TeamMembership).where(TeamMembership.team_id == team.id)
    ).all():
        session.delete(membership)
    for link in session.exec(
        select(TeamModel).where(TeamModel.team_id == team.id)
    ).all():
        session.delete(link)
    session.delete(team)
    session.commit()


@router.get("/{org_id}/teams/{team_id}/models", response_model=list[TeamModelRead])
def list_team_models(
    org_id: str,
    team_id: str,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[TeamModelRead]:
    org_uuid = _parse_org_id(org_id)
    _ensure_org_admin_or_super(session, org_uuid, current_user)
    team = _get_team(session, org_uuid, team_id)

    org_enabled = session.exec(
        select(OrgModel).where(
            OrgModel.org_id == org_uuid, OrgModel.is_enabled.is_(True)
        )
    ).all()
    team_links = {
        link.model_id: link
        for link in session.exec(
            select(TeamModel).where(TeamModel.team_id == team.id)
        ).all()
    }
    results: list[TeamModelRead] = []
    for org_link in org_enabled:
        model = session.exec(
            select(ChatModel).where(ChatModel.id == org_link.model_id)
        ).first()
        if not model or not model.is_active:
            continue
        team_link = team_links.get(model.id)
        results.append(
            TeamModelRead(
                model_id=str(model.id),
                display_name=model.display_name,
                provider=model.provider,
                model_name=model.model_name,
                is_enabled=bool(team_link and team_link.is_enabled),
            )
        )
    results.sort(key=lambda item: (item.display_name.lower(), item.model_id))
    return results


@router.put("/{org_id}/teams/{team_id}/models", response_model=list[TeamModelRead])
def set_team_models(
    org_id: str,
    team_id: str,
    payload: list[TeamModelUpdateRequest],
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[TeamModelRead]:
    org_uuid = _parse_org_id(org_id)
    _ensure_org_admin_or_super(session, org_uuid, current_user)
    team = _get_team(session, org_uuid, team_id)

    org_enabled_ids = set(
        session.exec(
            select(OrgModel.model_id).where(
                OrgModel.org_id == org_uuid, OrgModel.is_enabled.is_(True)
            )
        ).all()
    )
    for item in payload:
        try:
            model_uuid = UUID(item.model_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid model id"
            ) from exc
        if model_uuid not in org_enabled_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Model is not enabled for this organization",
            )
        link = session.exec(
            select(TeamModel).where(
                TeamModel.team_id == team.id, TeamModel.model_id == model_uuid
            )
        ).first()
        if link:
            link.is_enabled = item.is_enabled
            session.add(link)
        else:
            session.add(
                TeamModel(
                    team_id=team.id, model_id=model_uuid, is_enabled=item.is_enabled
                )
            )
    session.commit()
    return list_team_models(org_id, team_id, session, current_user)


@router.get("/{org_id}/teams/{team_id}/members", response_model=list[TeamMemberRead])
def list_team_members(
    org_id: str,
    team_id: str,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[TeamMemberRead]:
    org_uuid = _parse_org_id(org_id)
    _ensure_org_admin_or_super(session, org_uuid, current_user)
    team = _get_team(session, org_uuid, team_id)
    memberships = session.exec(
        select(TeamMembership).where(TeamMembership.team_id == team.id)
    ).all()
    results: list[TeamMemberRead] = []
    for membership in memberships:
        user = session.exec(select(User).where(User.id == membership.user_id)).first()
        if not user:
            continue
        results.append(
            TeamMemberRead(
                user_id=str(user.id),
                email=user.email,
                username=user.username,
                display_name=user.display_name,
                source=membership.source,
            )
        )
    results.sort(key=lambda item: item.email.lower())
    return results


@router.put("/{org_id}/teams/{team_id}/members", response_model=list[TeamMemberRead])
def set_team_members(
    org_id: str,
    team_id: str,
    payload: TeamMembersUpdateRequest,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[TeamMemberRead]:
    org_uuid = _parse_org_id(org_id)
    _ensure_org_admin_or_super(session, org_uuid, current_user)
    team = _get_team(session, org_uuid, team_id)
    if team.is_default:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Default team membership is implicit",
        )

    desired_ids: set[UUID] = set()
    for raw in payload.user_ids:
        try:
            user_uuid = UUID(raw)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user id"
            ) from exc
        org_membership = session.exec(
            select(OrgMembership).where(
                OrgMembership.org_id == org_uuid, OrgMembership.user_id == user_uuid
            )
        ).first()
        if not org_membership:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User is not a member of this organization",
            )
        desired_ids.add(user_uuid)

    existing = session.exec(
        select(TeamMembership).where(TeamMembership.team_id == team.id)
    ).all()
    existing_by_user = {m.user_id: m for m in existing}

    # Remove manual memberships not in desired set; keep oidc ones unless also removed from desired
    for membership in existing:
        if membership.user_id in desired_ids:
            continue
        if membership.source == TEAM_SOURCE_MANUAL:
            session.delete(membership)

    for user_uuid in desired_ids:
        membership = existing_by_user.get(user_uuid)
        if membership:
            continue
        session.add(
            TeamMembership(
                team_id=team.id,
                user_id=user_uuid,
                source=TEAM_SOURCE_MANUAL,
            )
        )
    session.commit()
    return list_team_members(org_id, team_id, session, current_user)
