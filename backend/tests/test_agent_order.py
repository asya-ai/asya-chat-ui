from datetime import datetime, timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.api.agents import AgentOrderUpdateRequest, list_agents, update_agent_order
from app.api.deps import AuthContext
from app.models import (
    Agent,
    AgentAccess,
    AgentAccessRole,
    AgentTeamAccess,
    AgentUserOrder,
    AgentVisibility,
    Org,
    OrgMembership,
    Role,
    Team,
    TeamMembership,
    User,
)


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
            AgentUserOrder.__table__,
        ],
    )
    return Session(engine)


def _auth(user: User, org: Org) -> AuthContext:
    return AuthContext(user=user, org_id=org.id)


def _seed(session: Session) -> tuple[Org, User, User, list[Agent]]:
    org = Org(name=f"Org-{uuid4().hex[:8]}")
    session.add(org)
    session.commit()
    session.refresh(org)

    owner = User(email=f"owner-{uuid4().hex[:8]}@example.com", hashed_password="x")
    other = User(email=f"other-{uuid4().hex[:8]}@example.com", hashed_password="x")
    session.add(owner)
    session.add(other)
    session.commit()
    session.refresh(owner)
    session.refresh(other)

    role = Role(org_id=org.id, name="member", is_default=True)
    session.add(role)
    session.commit()
    session.refresh(role)
    session.add(OrgMembership(org_id=org.id, user_id=owner.id, role_id=role.id))
    session.add(OrgMembership(org_id=org.id, user_id=other.id, role_id=role.id))

    base = datetime.utcnow()
    agents: list[Agent] = []
    for idx, name in enumerate(("Alpha", "Beta", "Gamma")):
        agent = Agent(
            org_id=org.id,
            owner_user_id=owner.id,
            name=name,
            master_prompt="",
            visibility=AgentVisibility.private,
            created_at=base - timedelta(hours=idx),
            updated_at=base - timedelta(hours=idx),
        )
        session.add(agent)
        session.flush()
        session.add(
            AgentAccess(
                agent_id=agent.id,
                user_id=owner.id,
                role=AgentAccessRole.owner,
                granted_by_user_id=owner.id,
            )
        )
        session.add(
            AgentAccess(
                agent_id=agent.id,
                user_id=other.id,
                role=AgentAccessRole.viewer,
                granted_by_user_id=owner.id,
            )
        )
        agents.append(agent)
    session.commit()
    for agent in agents:
        session.refresh(agent)
    return org, owner, other, agents


def test_list_defaults_to_updated_at_desc():
    with _session() as session:
        org, owner, _other, agents = _seed(session)
        listed = list_agents(session=session, auth=_auth(owner, org))
        assert [item.id for item in listed] == [str(agents[0].id), str(agents[1].id), str(agents[2].id)]
        assert all(item.display_order is None for item in listed)


def test_update_order_persists_per_user():
    with _session() as session:
        org, owner, other, agents = _seed(session)
        alpha, beta, gamma = agents

        update_agent_order(
            [
                AgentOrderUpdateRequest(agent_id=str(gamma.id), display_order=1),
                AgentOrderUpdateRequest(agent_id=str(alpha.id), display_order=2),
                AgentOrderUpdateRequest(agent_id=str(beta.id), display_order=3),
            ],
            session=session,
            auth=_auth(owner, org),
        )

        owner_list = list_agents(session=session, auth=_auth(owner, org))
        assert [item.id for item in owner_list] == [
            str(gamma.id),
            str(alpha.id),
            str(beta.id),
        ]
        assert [item.display_order for item in owner_list] == [1, 2, 3]

        other_list = list_agents(session=session, auth=_auth(other, org))
        assert [item.id for item in other_list] == [
            str(alpha.id),
            str(beta.id),
            str(gamma.id),
        ]
        assert all(item.display_order is None for item in other_list)


def test_update_order_rejects_inaccessible_agent():
    with _session() as session:
        org, owner, _other, agents = _seed(session)
        foreign = Agent(
            org_id=org.id,
            owner_user_id=owner.id,
            name="Secret",
            master_prompt="",
            visibility=AgentVisibility.private,
        )
        session.add(foreign)
        session.commit()
        session.refresh(foreign)

        try:
            update_agent_order(
                [AgentOrderUpdateRequest(agent_id=str(foreign.id), display_order=1)],
                session=session,
                auth=_auth(owner, org),
            )
            raise AssertionError("expected HTTPException")
        except HTTPException as exc:
            assert exc.status_code == 403
