from uuid import uuid4

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import (
    ChatModel,
    Org,
    OrgMembership,
    OrgModel,
    Role,
    Team,
    TeamMembership,
    TeamModel,
    User,
)
from app.services.team_service import (
    TEAM_SOURCE_MANUAL,
    TEAM_SOURCE_OIDC,
    allowed_model_ids,
    ensure_default_team,
    seed_default_team_model,
    sync_oidc_team_memberships,
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
            ChatModel.__table__,
            OrgModel.__table__,
            Team.__table__,
            TeamMembership.__table__,
            TeamModel.__table__,
        ],
    )
    return Session(engine)


def _seed_org(session: Session) -> tuple[Org, User, ChatModel, ChatModel]:
    org = Org(name=f"Org-{uuid4().hex[:8]}")
    session.add(org)
    session.commit()
    session.refresh(org)

    user = User(
        email=f"user-{uuid4().hex[:8]}@example.com",
        hashed_password="x",
    )
    session.add(user)
    session.commit()
    session.refresh(user)

    role = Role(org_id=org.id, name="member", is_default=True)
    session.add(role)
    session.commit()
    session.refresh(role)
    session.add(OrgMembership(org_id=org.id, user_id=user.id, role_id=role.id))
    session.commit()

    model_a = ChatModel(
        provider="openai",
        model_name="gpt-a",
        display_name="Model A",
        is_active=True,
    )
    model_b = ChatModel(
        provider="openai",
        model_name="gpt-b",
        display_name="Model B",
        is_active=True,
    )
    session.add(model_a)
    session.add(model_b)
    session.commit()
    session.refresh(model_a)
    session.refresh(model_b)

    session.add(OrgModel(org_id=org.id, model_id=model_a.id, is_enabled=True))
    session.add(OrgModel(org_id=org.id, model_id=model_b.id, is_enabled=True))
    session.commit()
    return org, user, model_a, model_b


def test_ensure_default_team_created_once():
    with _session() as session:
        org, _, _, _ = _seed_org(session)
        team = ensure_default_team(session, org.id)
        again = ensure_default_team(session, org.id)
        assert team.id == again.id
        assert team.is_default is True
        assert team.name == "Default"
        teams = session.exec(select(Team).where(Team.org_id == org.id)).all()
        assert len(teams) == 1


def test_allowed_models_default_only_then_union():
    with _session() as session:
        org, user, model_a, model_b = _seed_org(session)
        default = ensure_default_team(session, org.id)
        session.add(TeamModel(team_id=default.id, model_id=model_a.id, is_enabled=True))
        session.commit()

        assert allowed_model_ids(session, org.id, user.id) == {model_a.id}

        extra = Team(
            org_id=org.id, name="Engineering", is_default=False, oidc_group="eng"
        )
        session.add(extra)
        session.commit()
        session.refresh(extra)
        session.add(TeamModel(team_id=extra.id, model_id=model_b.id, is_enabled=True))
        session.add(
            TeamMembership(
                team_id=extra.id, user_id=user.id, source=TEAM_SOURCE_MANUAL
            )
        )
        session.commit()

        assert allowed_model_ids(session, org.id, user.id) == {model_a.id, model_b.id}


def test_org_disabled_model_never_allowed():
    with _session() as session:
        org, user, model_a, model_b = _seed_org(session)
        default = ensure_default_team(session, org.id)
        session.add(TeamModel(team_id=default.id, model_id=model_a.id, is_enabled=True))
        session.add(TeamModel(team_id=default.id, model_id=model_b.id, is_enabled=True))
        link = session.exec(
            select(OrgModel).where(
                OrgModel.org_id == org.id, OrgModel.model_id == model_b.id
            )
        ).one()
        link.is_enabled = False
        session.add(link)
        session.commit()

        assert allowed_model_ids(session, org.id, user.id) == {model_a.id}


def test_oidc_sync_adds_and_removes_without_touching_manual():
    with _session() as session:
        org, user, model_a, _ = _seed_org(session)
        ensure_default_team(session, org.id)
        eng = Team(org_id=org.id, name="Eng", is_default=False, oidc_group="eng")
        sales = Team(org_id=org.id, name="Sales", is_default=False, oidc_group="sales")
        session.add(eng)
        session.add(sales)
        session.commit()
        session.refresh(eng)
        session.refresh(sales)

        session.add(
            TeamMembership(
                team_id=sales.id, user_id=user.id, source=TEAM_SOURCE_MANUAL
            )
        )
        session.commit()

        sync_oidc_team_memberships(session, org.id, user.id, ["eng"])
        memberships = {
            m.team_id: m.source
            for m in session.exec(
                select(TeamMembership).where(TeamMembership.user_id == user.id)
            ).all()
        }
        assert memberships[eng.id] == TEAM_SOURCE_OIDC
        assert memberships[sales.id] == TEAM_SOURCE_MANUAL

        sync_oidc_team_memberships(session, org.id, user.id, [])
        memberships = {
            m.team_id: m.source
            for m in session.exec(
                select(TeamMembership).where(TeamMembership.user_id == user.id)
            ).all()
        }
        assert eng.id not in memberships
        assert memberships[sales.id] == TEAM_SOURCE_MANUAL


def test_seed_default_team_model_on_enable():
    with _session() as session:
        org, _, model_a, model_b = _seed_org(session)
        ensure_default_team(session, org.id)
        seed_default_team_model(session, org.id, model_a.id, enabled=True)
        session.commit()
        seed_default_team_model(session, org.id, model_b.id, enabled=True)
        session.commit()

        default = session.exec(
            select(Team).where(Team.org_id == org.id, Team.is_default.is_(True))
        ).one()
        links = session.exec(
            select(TeamModel).where(TeamModel.team_id == default.id)
        ).all()
        assert {link.model_id for link in links} == {model_a.id, model_b.id}


def test_seed_default_skips_when_default_was_customized():
    with _session() as session:
        org, _, model_a, model_b = _seed_org(session)
        default = ensure_default_team(session, org.id)
        # Org has A+B enabled, but admin only left A on Default.
        session.add(TeamModel(team_id=default.id, model_id=model_a.id, is_enabled=True))
        session.commit()

        model_c = ChatModel(
            provider="openai",
            model_name="gpt-c",
            display_name="Model C",
            is_active=True,
        )
        session.add(model_c)
        session.commit()
        session.refresh(model_c)
        session.add(OrgModel(org_id=org.id, model_id=model_c.id, is_enabled=True))
        session.commit()

        seed_default_team_model(session, org.id, model_c.id, enabled=True)
        session.commit()

        links = session.exec(
            select(TeamModel).where(TeamModel.team_id == default.id)
        ).all()
        assert {link.model_id for link in links if link.is_enabled} == {model_a.id}
        assert model_c.id not in {link.model_id for link in links}
