from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.orgs import remove_member
from app.models import Org, OrgMembership, Role, Team, TeamMembership, User


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
        ],
    )
    return Session(engine)


def _seed(session: Session) -> tuple[Org, User, User, User, Role, Team]:
    org = Org(name=f"Org-{uuid4().hex[:8]}")
    session.add(org)
    session.commit()
    session.refresh(org)

    admin = User(email=f"admin-{uuid4().hex[:8]}@example.com", hashed_password="x")
    other_admin = User(
        email=f"admin2-{uuid4().hex[:8]}@example.com", hashed_password="x"
    )
    member = User(email=f"member-{uuid4().hex[:8]}@example.com", hashed_password="x")
    session.add(admin)
    session.add(other_admin)
    session.add(member)
    session.commit()
    session.refresh(admin)
    session.refresh(other_admin)
    session.refresh(member)

    admin_role = Role(org_id=org.id, name="admin", is_default=False)
    member_role = Role(org_id=org.id, name="member", is_default=True)
    session.add(admin_role)
    session.add(member_role)
    session.commit()
    session.refresh(admin_role)
    session.refresh(member_role)

    session.add(OrgMembership(org_id=org.id, user_id=admin.id, role_id=admin_role.id))
    session.add(
        OrgMembership(org_id=org.id, user_id=other_admin.id, role_id=admin_role.id)
    )
    session.add(OrgMembership(org_id=org.id, user_id=member.id, role_id=member_role.id))
    session.commit()

    team = Team(org_id=org.id, name="Engineering", is_default=False)
    session.add(team)
    session.commit()
    session.refresh(team)
    session.add(TeamMembership(team_id=team.id, user_id=member.id, source="manual"))
    session.commit()

    return org, admin, other_admin, member, admin_role, team


def test_admin_can_remove_member():
    with _session() as session:
        org, admin, _, member, _, team = _seed(session)

        remove_member(str(org.id), str(member.id), session=session, current_user=admin)

        remaining = session.exec(
            select(OrgMembership).where(
                OrgMembership.org_id == org.id, OrgMembership.user_id == member.id
            )
        ).first()
        assert remaining is None
        leftover_team = session.exec(
            select(TeamMembership).where(
                TeamMembership.team_id == team.id, TeamMembership.user_id == member.id
            )
        ).first()
        assert leftover_team is None


def test_cannot_remove_yourself():
    with _session() as session:
        org, admin, _, _, _, _ = _seed(session)

        with pytest.raises(HTTPException) as exc:
            remove_member(str(org.id), str(admin.id), session=session, current_user=admin)
        assert exc.value.status_code == 400


def test_cannot_remove_last_admin():
    with _session() as session:
        org, admin, other_admin, _, admin_role, _ = _seed(session)
        extra = session.exec(
            select(OrgMembership).where(
                OrgMembership.org_id == org.id, OrgMembership.user_id == other_admin.id
            )
        ).first()
        assert extra is not None
        session.delete(extra)
        session.commit()

        super_admin = User(
            email=f"super-{uuid4().hex[:8]}@example.com",
            hashed_password="x",
            is_super_admin=True,
        )
        session.add(super_admin)
        session.commit()
        session.refresh(super_admin)

        with pytest.raises(HTTPException) as exc:
            remove_member(
                str(org.id), str(admin.id), session=session, current_user=super_admin
            )
        assert exc.value.status_code == 400

        still_admin = session.exec(
            select(OrgMembership).where(
                OrgMembership.org_id == org.id,
                OrgMembership.user_id == admin.id,
                OrgMembership.role_id == admin_role.id,
            )
        ).first()
        assert still_admin is not None


def test_super_admin_can_remove_member_without_org_membership():
    with _session() as session:
        org, _, _, member, _, _ = _seed(session)
        super_admin = User(
            email=f"super-{uuid4().hex[:8]}@example.com",
            hashed_password="x",
            is_super_admin=True,
        )
        session.add(super_admin)
        session.commit()
        session.refresh(super_admin)

        remove_member(
            str(org.id), str(member.id), session=session, current_user=super_admin
        )

        remaining = session.exec(
            select(OrgMembership).where(
                OrgMembership.org_id == org.id, OrgMembership.user_id == member.id
            )
        ).first()
        assert remaining is None
