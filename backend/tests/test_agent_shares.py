from datetime import datetime
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.agents import (
    AgentShareRequest,
    list_agents,
    list_shares,
    share_agent,
    share_suggestions,
    unshare_agent_org,
    unshare_agent_team,
)
from app.api.deps import AuthContext
from app.models import (
    Agent,
    AgentAccess,
    AgentAccessRole,
    AgentTeamAccess,
    AgentVisibility,
    Org,
    OrgMembership,
    Role,
    Team,
    TeamMembership,
    User,
)
from app.services.agent_access import get_agent_role


def _session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            Org.__table__,
            Role.__table__,
            OrgMembership.__table__,
            Team.__table__,
            TeamMembership.__table__,
            Agent.__table__,
            AgentAccess.__table__,
            AgentTeamAccess.__table__,
        ],
    )
    return Session(engine)


def _auth(user: User, org: Org) -> AuthContext:
    return AuthContext(user=user, org_id=org.id)


def _seed(session: Session) -> tuple[Org, User, User, User, Team, Agent]:
    org = Org(name=f"Org-{uuid4().hex[:8]}")
    session.add(org)
    session.commit()
    session.refresh(org)

    owner = User(email=f"owner-{uuid4().hex[:8]}@example.com", hashed_password="x")
    teammate = User(email=f"team-{uuid4().hex[:8]}@example.com", hashed_password="x")
    outsider = User(email=f"out-{uuid4().hex[:8]}@example.com", hashed_password="x")
    session.add(owner)
    session.add(teammate)
    session.add(outsider)
    session.commit()
    session.refresh(owner)
    session.refresh(teammate)
    session.refresh(outsider)

    role = Role(org_id=org.id, name="member", is_default=True)
    session.add(role)
    session.commit()
    session.refresh(role)
    session.add(OrgMembership(org_id=org.id, user_id=owner.id, role_id=role.id))
    session.add(OrgMembership(org_id=org.id, user_id=teammate.id, role_id=role.id))
    session.add(OrgMembership(org_id=org.id, user_id=outsider.id, role_id=role.id))

    team = Team(org_id=org.id, name="Alpha", is_default=False)
    default = Team(org_id=org.id, name="Default", is_default=True)
    session.add(team)
    session.add(default)
    session.commit()
    session.refresh(team)
    session.add(TeamMembership(team_id=team.id, user_id=teammate.id, source="manual"))

    now = datetime.utcnow()
    agent = Agent(
        org_id=org.id,
        owner_user_id=owner.id,
        name="Shared project",
        master_prompt="",
        visibility=AgentVisibility.private,
        created_at=now,
        updated_at=now,
    )
    session.add(agent)
    session.commit()
    session.refresh(agent)
    session.add(
        AgentAccess(
            agent_id=agent.id,
            user_id=owner.id,
            role=AgentAccessRole.owner,
            granted_by_user_id=owner.id,
        )
    )
    session.commit()
    return org, owner, teammate, outsider, team, agent


def test_org_share_grants_access_to_all_members():
    with _session() as session:
        org, owner, teammate, outsider, _team, agent = _seed(session)
        share_agent(
            agent.id,
            AgentShareRequest(org=True, role=AgentAccessRole.viewer),
            session=session,
            auth=_auth(owner, org),
        )

        listed = list_agents(session=session, auth=_auth(outsider, org))
        assert any(item.id == str(agent.id) for item in listed)
        assert listed[0].role == AgentAccessRole.viewer
        assert get_agent_role(session, agent, outsider.id) == AgentAccessRole.viewer
        assert get_agent_role(session, agent, teammate.id) == AgentAccessRole.viewer


def test_team_share_overrides_org_role():
    with _session() as session:
        org, owner, teammate, outsider, team, agent = _seed(session)
        share_agent(
            agent.id,
            AgentShareRequest(org=True, role=AgentAccessRole.viewer),
            session=session,
            auth=_auth(owner, org),
        )
        share_agent(
            agent.id,
            AgentShareRequest(team_id=str(team.id), role=AgentAccessRole.editor),
            session=session,
            auth=_auth(owner, org),
        )

        session.refresh(agent)
        assert get_agent_role(session, agent, outsider.id) == AgentAccessRole.viewer
        assert get_agent_role(session, agent, teammate.id) == AgentAccessRole.editor
        shares = list_shares(agent.id, session=session, auth=_auth(owner, org))
        kinds = {share.kind for share in shares}
        assert "org" in kinds
        assert "team" in kinds


def test_unshare_org_and_team_revokes_access():
    with _session() as session:
        org, owner, teammate, outsider, team, agent = _seed(session)
        share_agent(
            agent.id,
            AgentShareRequest(org=True, role=AgentAccessRole.viewer),
            session=session,
            auth=_auth(owner, org),
        )
        share_agent(
            agent.id,
            AgentShareRequest(team_id=str(team.id), role=AgentAccessRole.editor),
            session=session,
            auth=_auth(owner, org),
        )
        unshare_agent_org(agent.id, session=session, auth=_auth(owner, org))
        session.refresh(agent)
        assert get_agent_role(session, agent, outsider.id) is None
        assert get_agent_role(session, agent, teammate.id) == AgentAccessRole.editor

        unshare_agent_team(agent.id, team.id, session=session, auth=_auth(owner, org))
        session.refresh(agent)
        assert get_agent_role(session, agent, teammate.id) is None
        listed = list_agents(session=session, auth=_auth(teammate, org))
        assert listed == []


def test_cannot_share_with_default_team():
    with _session() as session:
        org, owner, _teammate, _outsider, _team, agent = _seed(session)
        default = session.exec(select(Team).where(Team.is_default.is_(True))).first()
        assert default is not None
        try:
            share_agent(
                agent.id,
                AgentShareRequest(team_id=str(default.id), role=AgentAccessRole.viewer),
                session=session,
                auth=_auth(owner, org),
            )
            raise AssertionError("expected HTTPException")
        except HTTPException as exc:
            assert exc.status_code == 400


def test_share_suggestions_include_org_and_teams():
    with _session() as session:
        org, owner, _teammate, _outsider, team, agent = _seed(session)
        suggestions = share_suggestions(
            agent.id, q="", session=session, auth=_auth(owner, org)
        )
        kinds = {item.kind for item in suggestions}
        assert "org" in kinds
        assert any(item.kind == "team" and item.team_id == str(team.id) for item in suggestions)
