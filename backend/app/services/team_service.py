from __future__ import annotations

from typing import Iterable
from uuid import UUID

from sqlmodel import Session, select

from app.models import OrgModel, Team, TeamMembership, TeamModel

TEAM_SOURCE_OIDC = "oidc"
TEAM_SOURCE_MANUAL = "manual"
DEFAULT_TEAM_NAME = "Default"


def get_default_team(session: Session, org_id: UUID) -> Team | None:
    return session.exec(
        select(Team).where(Team.org_id == org_id, Team.is_default.is_(True))
    ).first()


def ensure_default_team(session: Session, org_id: UUID, *, commit: bool = True) -> Team:
    team = get_default_team(session, org_id)
    if team:
        return team
    team = Team(org_id=org_id, name=DEFAULT_TEAM_NAME, is_default=True)
    session.add(team)
    if commit:
        session.commit()
        session.refresh(team)
    else:
        session.flush()
    return team


def _org_has_only_default_team(session: Session, org_id: UUID) -> bool:
    extra = session.exec(
        select(Team.id).where(Team.org_id == org_id, Team.is_default.is_(False)).limit(1)
    ).first()
    return extra is None


def seed_default_team_model(
    session: Session, org_id: UUID, model_id: UUID, *, enabled: bool = True
) -> None:
    """Add a newly enabled org model to Default so it appears in the chat picker.

    If Default has never been configured, mirror the full org-enabled set.
    When the org has only Default (no other teams), always enable the model on
    Default — including re-enabling a previously disabled row.
    When other teams exist, existing Default rows (including manual disables)
    are left unchanged.
    """
    if not enabled:
        return
    team = ensure_default_team(session, org_id, commit=False)
    link = session.exec(
        select(TeamModel).where(
            TeamModel.team_id == team.id, TeamModel.model_id == model_id
        )
    ).first()
    if link:
        if _org_has_only_default_team(session, org_id) and not link.is_enabled:
            link.is_enabled = True
            session.add(link)
        # Already configured (enabled, or manually disabled with other teams).
        return

    org_enabled = set(
        session.exec(
            select(OrgModel.model_id).where(
                OrgModel.org_id == org_id, OrgModel.is_enabled.is_(True)
            )
        ).all()
    )
    # Include the model being enabled even if OrgModel row is not flushed yet.
    org_enabled.add(model_id)

    existing_links = session.exec(
        select(TeamModel).where(TeamModel.team_id == team.id)
    ).all()
    if not existing_links:
        # Default has never been configured — mirror the full org-enabled set.
        for mid in org_enabled:
            session.add(TeamModel(team_id=team.id, model_id=mid, is_enabled=True))
        return

    session.add(TeamModel(team_id=team.id, model_id=model_id, is_enabled=True))


def allowed_model_ids(session: Session, org_id: UUID, user_id: UUID) -> set[UUID]:
    """Union of Default team models + models from teams the user belongs to,
    intersected with org-enabled models.
    """
    ensure_default_team(session, org_id, commit=False)

    default_ids = set(
        session.exec(
            select(TeamModel.model_id)
            .join(Team, Team.id == TeamModel.team_id)
            .where(
                Team.org_id == org_id,
                Team.is_default.is_(True),
                TeamModel.is_enabled.is_(True),
            )
        ).all()
    )
    member_ids = set(
        session.exec(
            select(TeamModel.model_id)
            .join(Team, Team.id == TeamModel.team_id)
            .join(TeamMembership, TeamMembership.team_id == Team.id)
            .where(
                Team.org_id == org_id,
                TeamMembership.user_id == user_id,
                TeamModel.is_enabled.is_(True),
            )
        ).all()
    )
    team_ids = default_ids | member_ids
    if not team_ids:
        return set()

    org_enabled = set(
        session.exec(
            select(OrgModel.model_id).where(
                OrgModel.org_id == org_id, OrgModel.is_enabled.is_(True)
            )
        ).all()
    )
    return team_ids & org_enabled


def normalize_oidc_groups(value) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        raw = value.strip()
        return {raw} if raw else set()
    if isinstance(value, (list, tuple, set)):
        groups: set[str] = set()
        for item in value:
            if isinstance(item, str):
                cleaned = item.strip()
                if cleaned:
                    groups.add(cleaned)
        return groups
    return set()


def remember_oidc_groups(session: Session, org_id: UUID, groups: Iterable[str]) -> None:
    """Merge group names into the org's known OIDC groups list."""
    from app.models import Org

    cleaned = sorted({g.strip() for g in groups if isinstance(g, str) and g.strip()})
    if not cleaned:
        return
    org = session.exec(select(Org).where(Org.id == org_id)).first()
    if not org:
        return
    existing = {
        item.strip()
        for item in (org.oidc_known_groups or [])
        if isinstance(item, str) and item.strip()
    }
    merged = sorted(existing | set(cleaned))
    if merged == sorted(existing):
        return
    org.oidc_known_groups = merged
    session.add(org)


def list_known_oidc_groups(session: Session, org_id: UUID) -> list[str]:
    from app.models import Org

    org = session.exec(select(Org).where(Org.id == org_id)).first()
    known = {
        item.strip()
        for item in ((org.oidc_known_groups if org else None) or [])
        if isinstance(item, str) and item.strip()
    }
    team_groups = session.exec(
        select(Team.oidc_group).where(
            Team.org_id == org_id, Team.oidc_group.is_not(None)
        )
    ).all()
    for group in team_groups:
        if isinstance(group, str) and group.strip():
            known.add(group.strip())
    return sorted(known)


def sync_oidc_team_memberships(
    session: Session,
    org_id: UUID,
    user_id: UUID,
    groups: Iterable[str],
) -> None:
    """Upsert oidc-sourced memberships for matching teams; remove stale oidc ones."""
    group_set = {g.strip() for g in groups if isinstance(g, str) and g.strip()}
    teams = session.exec(
        select(Team).where(
            Team.org_id == org_id,
            Team.is_default.is_(False),
            Team.oidc_group.is_not(None),
        )
    ).all()
    matched_team_ids: set[UUID] = set()
    for team in teams:
        if team.oidc_group and team.oidc_group in group_set:
            matched_team_ids.add(team.id)
            existing = session.exec(
                select(TeamMembership).where(
                    TeamMembership.team_id == team.id,
                    TeamMembership.user_id == user_id,
                )
            ).first()
            if existing:
                if existing.source != TEAM_SOURCE_OIDC and existing.source != TEAM_SOURCE_MANUAL:
                    existing.source = TEAM_SOURCE_OIDC
                    session.add(existing)
                # Keep manual memberships as-is if also matched via OIDC
                continue
            session.add(
                TeamMembership(
                    team_id=team.id,
                    user_id=user_id,
                    source=TEAM_SOURCE_OIDC,
                )
            )

    # Remove oidc-sourced memberships for non-matching teams in this org
    org_team_ids = [
        t.id
        for t in session.exec(
            select(Team).where(Team.org_id == org_id, Team.is_default.is_(False))
        ).all()
    ]
    if not org_team_ids:
        session.commit()
        return

    oidc_memberships = session.exec(
        select(TeamMembership).where(
            TeamMembership.user_id == user_id,
            TeamMembership.source == TEAM_SOURCE_OIDC,
            TeamMembership.team_id.in_(org_team_ids),
        )
    ).all()
    for membership in oidc_memberships:
        if membership.team_id not in matched_team_ids:
            session.delete(membership)
    session.commit()
