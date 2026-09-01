from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.agents import delete_agent, delete_source
from app.api.deps import AuthContext
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
    AgentVisibility,
    Chat,
    Org,
    OrgMembership,
    Role,
    Team,
    TeamMembership,
    User,
)
from app.services.agents.runtime import reindex_source


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
            AgentSource.__table__,
            AgentChunk.__table__,
            AgentEmbedding.__table__,
            Chat.__table__,
        ],
    )
    return Session(engine)


def _seed(session: Session) -> tuple[Org, User, Agent, AgentSource]:
    org = Org(name=f"Org-{uuid4().hex[:8]}")
    session.add(org)
    session.commit()
    session.refresh(org)

    owner = User(email=f"owner-{uuid4().hex[:8]}@example.com", hashed_password="x")
    session.add(owner)
    session.commit()
    session.refresh(owner)

    role = Role(org_id=org.id, name="member", is_default=True)
    session.add(role)
    session.commit()
    session.refresh(role)

    session.add(OrgMembership(org_id=org.id, user_id=owner.id, role_id=role.id))
    session.commit()

    now = datetime.utcnow()
    agent = Agent(
        org_id=org.id,
        owner_user_id=owner.id,
        name="Test project",
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
            created_at=now,
            updated_at=now,
        )
    )
    session.commit()

    source = AgentSource(
        agent_id=agent.id,
        kind=AgentSourceKind.file,
        title="notes.txt",
        file_name="notes.txt",
        content_type="text/plain",
        content_text="Hello from a queued source.",
        status=AgentSourceStatus.indexing,
    )
    session.add(source)
    session.commit()
    session.refresh(source)

    return org, owner, agent, source


def test_delete_source_while_indexing() -> None:
    session = _session()
    org, owner, agent, source = _seed(session)
    auth = AuthContext(user=owner, org_id=org.id)

    delete_source(agent.id, source.id, session=session, auth=auth)

    assert session.get(AgentSource, source.id) is None


def test_delete_agent_while_source_indexing() -> None:
    session = _session()
    org, owner, agent, source = _seed(session)
    auth = AuthContext(user=owner, org_id=org.id)

    delete_agent(agent.id, session=session, auth=auth)

    assert session.get(Agent, agent.id) is None
    assert session.get(AgentSource, source.id) is None


def test_reindex_source_exits_when_source_deleted_mid_run(monkeypatch) -> None:
    session = _session()
    _, _, agent, source = _seed(session)
    source.status = AgentSourceStatus.queued
    session.add(source)
    session.commit()
    session.refresh(source)

    def fake_encode(texts: list[str]):
        session.delete(source)
        session.commit()
        return None

    monkeypatch.setattr("app.services.agents.runtime._encode_documents", fake_encode)

    chunks_count, error = reindex_source(session, source)
    session.commit()

    assert chunks_count == 0
    assert error is None
    assert session.exec(select(AgentSource).where(AgentSource.agent_id == agent.id)).first() is None
