from datetime import datetime
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.deps import AuthContext
from app.api.prompts import (
    PromptCreateRequest,
    PromptUpdateRequest,
    create_prompt,
    delete_prompt,
    list_prompts,
    update_prompt,
)
from app.api.teams import list_my_teams
from app.models import (
    Agent,
    AgentAccess,
    AgentAccessRole,
    AgentVisibility,
    Org,
    OrgMembership,
    Prompt,
    PromptTeamShare,
    PromptUserShare,
    PromptVisibility,
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
            Prompt.__table__,
            PromptTeamShare.__table__,
            PromptUserShare.__table__,
        ],
    )
    return Session(engine)


def _seed(session: Session) -> tuple[Org, User, User, Team, Team]:
    org = Org(name=f"Org-{uuid4().hex[:8]}")
    session.add(org)
    session.commit()
    session.refresh(org)

    owner = User(email=f"owner-{uuid4().hex[:8]}@example.com", hashed_password="x")
    member = User(email=f"member-{uuid4().hex[:8]}@example.com", hashed_password="x")
    outsider = User(email=f"out-{uuid4().hex[:8]}@example.com", hashed_password="x")
    session.add(owner)
    session.add(member)
    session.add(outsider)
    session.commit()
    session.refresh(owner)
    session.refresh(member)
    session.refresh(outsider)

    role = Role(org_id=org.id, name="member", is_default=True)
    session.add(role)
    session.commit()
    session.refresh(role)
    session.add(OrgMembership(org_id=org.id, user_id=owner.id, role_id=role.id))
    session.add(OrgMembership(org_id=org.id, user_id=member.id, role_id=role.id))
    session.add(OrgMembership(org_id=org.id, user_id=outsider.id, role_id=role.id))
    session.commit()

    team_a = Team(org_id=org.id, name="Alpha", is_default=False)
    team_b = Team(org_id=org.id, name="Beta", is_default=False)
    default = Team(org_id=org.id, name="Default", is_default=True)
    session.add(team_a)
    session.add(team_b)
    session.add(default)
    session.commit()
    session.refresh(team_a)
    session.refresh(team_b)

    session.add(TeamMembership(team_id=team_a.id, user_id=owner.id, source="manual"))
    session.add(TeamMembership(team_id=team_a.id, user_id=member.id, source="manual"))
    session.add(TeamMembership(team_id=team_b.id, user_id=owner.id, source="manual"))
    session.commit()

    return org, owner, member, team_a, team_b


def _auth(user: User, org: Org) -> AuthContext:
    return AuthContext(user=user, org_id=org.id)


def test_private_prompt_visible_only_to_owner():
    with _session() as session:
        org, owner, member, _, _ = _seed(session)
        created = create_prompt(
            PromptCreateRequest(
                name="Private",
                body="secret",
                visibility=PromptVisibility.private,
            ),
            session=session,
            auth=_auth(owner, org),
        )
        owner_list = list_prompts(session=session, auth=_auth(owner, org))
        member_list = list_prompts(session=session, auth=_auth(member, org))
        assert any(item.id == created.id for item in owner_list)
        assert all(item.id != created.id for item in member_list)


def test_org_prompt_visible_to_org_members():
    with _session() as session:
        org, owner, member, _, _ = _seed(session)
        created = create_prompt(
            PromptCreateRequest(
                name="Org wide",
                body="hello",
                visibility=PromptVisibility.org,
            ),
            session=session,
            auth=_auth(owner, org),
        )
        member_list = list_prompts(session=session, auth=_auth(member, org))
        assert any(item.id == created.id for item in member_list)


def test_team_prompt_multi_team_share():
    with _session() as session:
        org, owner, member, team_a, team_b = _seed(session)
        created = create_prompt(
            PromptCreateRequest(
                name="Team prompt",
                body="shared",
                visibility=PromptVisibility.team,
                team_ids=[str(team_a.id), str(team_b.id)],
            ),
            session=session,
            auth=_auth(owner, org),
        )
        assert set(created.team_ids) == {str(team_a.id), str(team_b.id)}
        member_list = list_prompts(session=session, auth=_auth(member, org))
        assert any(item.id == created.id for item in member_list)

        # outsider not on team_a/b should not see it
        outsider = session.exec(
            select(User).where(User.email.startswith("out-"))
        ).first()
        assert outsider is not None
        outsider_list = list_prompts(session=session, auth=_auth(outsider, org))
        assert all(item.id != created.id for item in outsider_list)


def test_users_prompt_shared_with_specific_people():
    with _session() as session:
        org, owner, member, _, _ = _seed(session)
        created = create_prompt(
            PromptCreateRequest(
                name="People prompt",
                body="shared",
                visibility=PromptVisibility.users,
                user_ids=[str(member.id)],
            ),
            session=session,
            auth=_auth(owner, org),
        )
        assert created.user_ids == [str(member.id)]
        assert len(created.users) == 1
        assert created.users[0].email.startswith("member-")

        member_list = list_prompts(session=session, auth=_auth(member, org))
        assert any(item.id == created.id for item in member_list)

        outsider = session.exec(
            select(User).where(User.email.startswith("out-"))
        ).first()
        assert outsider is not None
        outsider_list = list_prompts(session=session, auth=_auth(outsider, org))
        assert all(item.id != created.id for item in outsider_list)


def test_users_visibility_requires_user_ids():
    with _session() as session:
        org, owner, _, _, _ = _seed(session)
        try:
            create_prompt(
                PromptCreateRequest(
                    name="Bad",
                    body="x",
                    visibility=PromptVisibility.users,
                ),
                session=session,
                auth=_auth(owner, org),
            )
            assert False, "expected bad request"
        except HTTPException as exc:
            assert exc.status_code == 400


def test_move_prompt_profile_to_space_and_back():
    with _session() as session:
        org, owner, _, _, _ = _seed(session)
        now = datetime.utcnow()
        agent = Agent(
            org_id=org.id,
            owner_user_id=owner.id,
            name="Space",
            master_prompt="",
            visibility=AgentVisibility.private,
            created_at=now,
            updated_at=now,
        )
        session.add(agent)
        session.flush()
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
        session.refresh(agent)

        created = create_prompt(
            PromptCreateRequest(name="Movable", body="body"),
            session=session,
            auth=_auth(owner, org),
        )
        assert created.agent_id is None

        moved = update_prompt(
            UUID(created.id),
            PromptUpdateRequest(agent_id=str(agent.id)),
            session=session,
            auth=_auth(owner, org),
        )
        assert moved.agent_id == str(agent.id)

        back = update_prompt(
            UUID(created.id),
            PromptUpdateRequest(clear_agent=True),
            session=session,
            auth=_auth(owner, org),
        )
        assert back.agent_id is None


def test_non_owner_cannot_update_or_delete():
    with _session() as session:
        org, owner, member, _, _ = _seed(session)
        created = create_prompt(
            PromptCreateRequest(
                name="Owned",
                body="x",
                visibility=PromptVisibility.org,
            ),
            session=session,
            auth=_auth(owner, org),
        )
        try:
            update_prompt(
                UUID(created.id),
                PromptUpdateRequest(name="Hijack"),
                session=session,
                auth=_auth(member, org),
            )
            assert False, "expected forbidden"
        except HTTPException as exc:
            assert exc.status_code == 403

        try:
            delete_prompt(UUID(created.id), session=session, auth=_auth(member, org))
            assert False, "expected forbidden"
        except HTTPException as exc:
            assert exc.status_code == 403


def test_list_my_teams_excludes_default():
    with _session() as session:
        org, owner, _, team_a, team_b = _seed(session)
        teams = list_my_teams(str(org.id), session=session, current_user=owner)
        ids = {team.id for team in teams}
        assert str(team_a.id) in ids
        assert str(team_b.id) in ids
        assert all(team.name != "Default" for team in teams)


def _make_space(
    session: Session, org: Org, owner: User, *, member: User | None = None
) -> Agent:
    now = datetime.utcnow()
    agent = Agent(
        org_id=org.id,
        owner_user_id=owner.id,
        name="Space",
        master_prompt="",
        visibility=AgentVisibility.private,
        created_at=now,
        updated_at=now,
    )
    session.add(agent)
    session.flush()
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
    if member:
        session.add(
            AgentAccess(
                agent_id=agent.id,
                user_id=member.id,
                role=AgentAccessRole.viewer,
                granted_by_user_id=owner.id,
                created_at=now,
                updated_at=now,
            )
        )
    session.commit()
    session.refresh(agent)
    return agent


def test_space_prompt_offered_only_in_that_space_context():
    with _session() as session:
        org, owner, member, _, _ = _seed(session)
        space = _make_space(session, org, owner, member=member)
        other = _make_space(session, org, owner)

        profile = create_prompt(
            PromptCreateRequest(name="Profile", body="p"),
            session=session,
            auth=_auth(owner, org),
        )
        space_prompt = create_prompt(
            PromptCreateRequest(
                name="Space prompt",
                body="s",
                visibility=PromptVisibility.space,
                agent_id=str(space.id),
            ),
            session=session,
            auth=_auth(owner, org),
        )

        personal = list_prompts(session=session, auth=_auth(owner, org))
        assert any(item.id == profile.id for item in personal)
        assert all(item.id != space_prompt.id for item in personal)

        in_space = list_prompts(
            context_agent_id=str(space.id),
            session=session,
            auth=_auth(owner, org),
        )
        assert {item.id for item in in_space} >= {profile.id, space_prompt.id}

        in_other = list_prompts(
            context_agent_id=str(other.id),
            session=session,
            auth=_auth(owner, org),
        )
        assert any(item.id == profile.id for item in in_other)
        assert all(item.id != space_prompt.id for item in in_other)


def test_space_visibility_visible_to_space_members():
    with _session() as session:
        org, owner, member, _, _ = _seed(session)
        space = _make_space(session, org, owner, member=member)
        created = create_prompt(
            PromptCreateRequest(
                name="Shared in space",
                body="hello",
                visibility=PromptVisibility.space,
                agent_id=str(space.id),
            ),
            session=session,
            auth=_auth(owner, org),
        )
        member_list = list_prompts(
            context_agent_id=str(space.id),
            session=session,
            auth=_auth(member, org),
        )
        assert any(item.id == created.id for item in member_list)

        outsider = session.exec(
            select(User).where(User.email.startswith("out-"))
        ).first()
        assert outsider is not None
        outsider_list = list_prompts(
            context_agent_id=str(space.id),
            session=session,
            auth=_auth(outsider, org),
        )
        assert all(item.id != created.id for item in outsider_list)


def test_space_visibility_requires_space_location():
    with _session() as session:
        org, owner, _, _, _ = _seed(session)
        try:
            create_prompt(
                PromptCreateRequest(
                    name="Bad",
                    body="x",
                    visibility=PromptVisibility.space,
                ),
                session=session,
                auth=_auth(owner, org),
            )
            assert False, "expected bad request"
        except HTTPException as exc:
            assert exc.status_code == 400
