"""Resolve project (agent) access from user, team, and org shares."""

from __future__ import annotations

from uuid import UUID

from sqlmodel import Session, select

from app.models import (
    Agent,
    AgentAccess,
    AgentAccessRole,
    AgentTeamAccess,
    AgentUserOrder,
    Chat,
    ChatMessage,
    Team,
    TeamMembership,
    User,
)

ROLE_ORDER = {
    AgentAccessRole.viewer: 1,
    AgentAccessRole.editor: 2,
    AgentAccessRole.owner: 3,
}


def _coerce_role(value: AgentAccessRole | str | None) -> AgentAccessRole | None:
    if value is None:
        return None
    if isinstance(value, AgentAccessRole):
        return value
    return AgentAccessRole(value)


def max_role(*roles: AgentAccessRole | str | None) -> AgentAccessRole | None:
    best: AgentAccessRole | None = None
    for raw in roles:
        role = _coerce_role(raw)
        if role is None:
            continue
        if best is None or ROLE_ORDER[role] > ROLE_ORDER[best]:
            best = role
    return best


def user_team_ids(session: Session, org_id: UUID, user_id: UUID) -> set[UUID]:
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


def get_agent_role(
    session: Session,
    agent: Agent,
    user_id: UUID,
) -> AgentAccessRole | None:
    user_role = session.exec(
        select(AgentAccess.role).where(
            AgentAccess.agent_id == agent.id,
            AgentAccess.user_id == user_id,
        )
    ).first()

    team_roles = session.exec(
        select(AgentTeamAccess.role)
        .join(Team, Team.id == AgentTeamAccess.team_id)
        .join(TeamMembership, TeamMembership.team_id == Team.id)
        .where(
            AgentTeamAccess.agent_id == agent.id,
            Team.org_id == agent.org_id,
            Team.is_default.is_(False),
            TeamMembership.user_id == user_id,
        )
    ).all()

    return max_role(user_role, agent.org_access_role, *team_roles)


def accessible_agent_ids(session: Session, org_id: UUID, user_id: UUID) -> set[UUID]:
    team_ids = user_team_ids(session, org_id, user_id)
    ids: set[UUID] = set(
        session.exec(
            select(AgentAccess.agent_id)
            .join(Agent, Agent.id == AgentAccess.agent_id)
            .where(Agent.org_id == org_id, AgentAccess.user_id == user_id)
        ).all()
    )
    ids.update(
        session.exec(
            select(Agent.id).where(
                Agent.org_id == org_id,
                Agent.org_access_role.is_not(None),
            )
        ).all()
    )
    if team_ids:
        ids.update(
            session.exec(
                select(AgentTeamAccess.agent_id)
                .join(Agent, Agent.id == AgentTeamAccess.agent_id)
                .where(
                    Agent.org_id == org_id,
                    AgentTeamAccess.team_id.in_(team_ids),
                )
            ).all()
        )
    return ids


def list_accessible_agents(
    session: Session, org_id: UUID, user_id: UUID
) -> list[tuple[Agent, AgentAccessRole, int | None]]:
    agent_ids = accessible_agent_ids(session, org_id, user_id)
    if not agent_ids:
        return []
    agents = session.exec(
        select(Agent).where(Agent.org_id == org_id, Agent.id.in_(agent_ids))
    ).all()
    order_rows = session.exec(
        select(AgentUserOrder).where(
            AgentUserOrder.org_id == org_id,
            AgentUserOrder.user_id == user_id,
            AgentUserOrder.agent_id.in_(agent_ids),
        )
    ).all()
    orders = {row.agent_id: row.display_order for row in order_rows}
    ranked: list[tuple[Agent, AgentAccessRole, int | None]] = []
    for agent in agents:
        role = get_agent_role(session, agent, user_id)
        if role is not None:
            ranked.append((agent, role, orders.get(agent.id)))
    ranked.sort(
        key=lambda item: (
            0 if item[2] is not None else 1,
            item[2] if item[2] is not None else 0,
            -(item[0].updated_at.timestamp() if item[0].updated_at else 0.0),
            str(item[0].id),
        )
    )
    return ranked


def can_manage_chat(chat: Chat, user: User) -> bool:
    if user.is_super_admin:
        return True
    return chat.user_id == user.id


def can_access_chat(session: Session, chat: Chat, user: User) -> bool:
    if chat.is_deleted:
        return False
    if can_manage_chat(chat, user):
        return True
    if chat.is_incognito or chat.is_subagent or chat.is_hidden_from_others:
        return False
    if chat.agent_id is None:
        return False
    agent = session.exec(
        select(Agent).where(Agent.id == chat.agent_id, Agent.org_id == chat.org_id)
    ).first()
    if not agent:
        return False
    return get_agent_role(session, agent, user.id) is not None


def can_edit_chat_message(chat: Chat, message: ChatMessage, user: User) -> bool:
    if message.role != "user":
        return False
    if user.is_super_admin:
        return True
    if message.user_id is not None:
        return message.user_id == user.id
    return chat.user_id == user.id


def user_display_label(user: User | None) -> str | None:
    if user is None:
        return None
    return (user.display_name or user.username or user.email or "").strip() or None
