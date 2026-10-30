from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import UUID
import re

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_
from sqlmodel import Session, col, select

from app.api.deps import get_current_user, get_db
from app.models import (
    Agent,
    Chat,
    ChatMessage,
    ChatMessageAttachment,
    FeatureEvent,
    IntegrationUserInstall,
    McpServer,
    McpUserConnection,
    Org,
    OrgMembership,
    Prompt,
    UsageEvent,
    User,
)
from app.services.agent_access import user_display_label
from app.services.org_service import require_org_admin

router = APIRouter(prefix="/analytics", tags=["analytics"])

DORMANT_DAYS = 30


class AnalyticsOverview(BaseModel):
    month: str
    seats: int
    active_users: int
    mau: int
    wau: int
    dau_avg: float
    chats_created: int
    user_messages: int
    assistant_messages: int
    dormant_users: int


class AnalyticsDailyPoint(BaseModel):
    date: str
    active_users: int
    chats_created: int
    messages: int


class AnalyticsUserRow(BaseModel):
    user_id: str
    name: str
    last_active_at: datetime | None = None
    chats: int
    messages: int
    dormant: bool


class AnalyticsFeatureRow(BaseModel):
    feature: str
    label: str
    unique_users: int
    event_count: int
    forward_looking: bool = False


class AnalyticsOrgRow(BaseModel):
    org_id: str
    name: str
    active_users: int
    chats_created: int
    top_feature: str | None = None


class AnalyticsFeaturesResponse(BaseModel):
    features: list[AnalyticsFeatureRow]
    by_org: list[AnalyticsOrgRow] = Field(default_factory=list)


def _parse_optional_uuid(value: str | None, *, invalid_detail: str) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=invalid_detail
        ) from exc


def _require_analytics_admin(
    current_user: User, session: Session, org_uuid: UUID | None
) -> None:
    if current_user.is_super_admin:
        return
    if not org_uuid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="org_id is required"
        )
    require_org_admin(session, org_uuid, current_user.id)


def _parse_month_bounds(month: str) -> tuple[datetime, datetime]:
    match = re.match(r"^(\d{4})-(\d{2})$", month)
    if not match:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid month format"
        )
    year = int(match.group(1))
    month_num = int(match.group(2))
    if month_num < 1 or month_num > 12:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid month format"
        )
    start = datetime(year, month_num, 1, tzinfo=timezone.utc)
    if month_num == 12:
        end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(year, month_num + 1, 1, tzinfo=timezone.utc)
    return start, end


def _resolve_month(month: str | None) -> tuple[str, datetime, datetime]:
    if month:
        start, end = _parse_month_bounds(month)
        return month, start, end
    now = datetime.now(timezone.utc)
    label = f"{now.year}-{now.month:02d}"
    start, end = _parse_month_bounds(label)
    return label, start, end


def _as_of(end: datetime) -> datetime:
    now = datetime.now(timezone.utc)
    # end is exclusive month bound
    last = end - timedelta(microseconds=1)
    return min(now, last)


def _naive(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _iter_dates(start: date, end_exclusive: date) -> list[str]:
    days: list[str] = []
    cursor = start
    while cursor < end_exclusive:
        days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return days


def _member_user_ids(session: Session, org_uuid: UUID | None) -> set[UUID]:
    stmt = select(OrgMembership.user_id)
    if org_uuid:
        stmt = stmt.where(OrgMembership.org_id == org_uuid)
    return {row for row in session.exec(stmt).all() if row}


def _seat_count(session: Session, org_uuid: UUID | None) -> int:
    stmt = select(func.count(func.distinct(OrgMembership.user_id)))
    if org_uuid:
        stmt = stmt.where(OrgMembership.org_id == org_uuid)
    return int(session.exec(stmt).one() or 0)


def _distinct_usage_users(
    session: Session,
    *,
    org_uuid: UUID | None,
    start: datetime,
    end: datetime,
) -> int:
    stmt = select(func.count(func.distinct(UsageEvent.user_id))).where(
        UsageEvent.created_at >= _naive(start),
        UsageEvent.created_at < _naive(end),
        UsageEvent.user_id.is_not(None),
    )
    if org_uuid:
        stmt = stmt.where(UsageEvent.org_id == org_uuid)
    return int(session.exec(stmt).one() or 0)


def _chat_base_filter(org_uuid: UUID | None):
    filters = [
        Chat.is_deleted.is_(False),
        Chat.is_incognito.is_(False),
        Chat.is_subagent.is_(False),
    ]
    if org_uuid:
        filters.append(Chat.org_id == org_uuid)
    return filters


def _feature_label(feature: str) -> str:
    if feature == "web_search":
        return "Web search"
    if feature == "code_execution":
        return "Code execution"
    if feature.startswith("mcp:"):
        return f"MCP ({feature[4:]})"
    if feature == "agent_chats":
        return "Agent chats"
    if feature == "shared_chats":
        return "Shared chats"
    if feature == "attachments":
        return "Attachments"
    if feature == "integrations":
        return "Integrations"
    if feature == "mcp_connections":
        return "MCP connections"
    if feature == "prompts":
        return "Prompts"
    if feature == "agents":
        return "Agents"
    if feature == "memory":
        return "Memory"
    return feature


@router.get("/overview", response_model=AnalyticsOverview)
def analytics_overview(
    org_id: str | None = None,
    month: str | None = None,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AnalyticsOverview:
    org_uuid = _parse_optional_uuid(org_id, invalid_detail="Invalid org id")
    _require_analytics_admin(current_user, session, org_uuid)
    label, start, end = _resolve_month(month)
    as_of = _as_of(end)
    week_start = as_of - timedelta(days=7)
    dormant_since = as_of - timedelta(days=DORMANT_DAYS)

    seats = _seat_count(session, org_uuid)
    mau = _distinct_usage_users(session, org_uuid=org_uuid, start=start, end=end)
    wau = _distinct_usage_users(
        session, org_uuid=org_uuid, start=week_start, end=as_of + timedelta(microseconds=1)
    )

    day_expr = func.date_trunc("day", UsageEvent.created_at)
    daily_stmt = (
        select(day_expr, func.count(func.distinct(UsageEvent.user_id)))
        .where(
            UsageEvent.created_at >= _naive(start),
            UsageEvent.created_at < _naive(end),
            UsageEvent.user_id.is_not(None),
        )
        .group_by(day_expr)
    )
    if org_uuid:
        daily_stmt = daily_stmt.where(UsageEvent.org_id == org_uuid)
    daily_rows = session.exec(daily_stmt).all()
    days_in_month = max((end.date() - start.date()).days, 1)
    dau_avg = (
        round(sum(int(row[1] or 0) for row in daily_rows) / days_in_month, 2)
        if daily_rows
        else 0.0
    )

    chat_filters = _chat_base_filter(org_uuid) + [
        Chat.created_at >= _naive(start),
        Chat.created_at < _naive(end),
    ]
    chats_created = int(
        session.exec(select(func.count()).select_from(Chat).where(*chat_filters)).one()
        or 0
    )

    msg_stmt = (
        select(ChatMessage.role, func.count())
        .join(Chat, Chat.id == ChatMessage.chat_id)
        .where(
            *_chat_base_filter(org_uuid),
            ChatMessage.created_at >= _naive(start),
            ChatMessage.created_at < _naive(end),
            ChatMessage.is_current.is_(True),
            col(ChatMessage.role).in_(("user", "assistant")),
        )
        .group_by(ChatMessage.role)
    )
    msg_counts = {str(role): int(count or 0) for role, count in session.exec(msg_stmt).all()}

    member_ids = _member_user_ids(session, org_uuid)
    if member_ids:
        active_recent = select(UsageEvent.user_id).where(
            UsageEvent.created_at >= _naive(dormant_since),
            UsageEvent.user_id.is_not(None),
        )
        if org_uuid:
            active_recent = active_recent.where(UsageEvent.org_id == org_uuid)
        active_set = {row for row in session.exec(active_recent.distinct()).all() if row}
        dormant_users = len(member_ids - active_set)
    else:
        dormant_users = 0

    return AnalyticsOverview(
        month=label,
        seats=seats,
        active_users=mau,
        mau=mau,
        wau=wau,
        dau_avg=dau_avg,
        chats_created=chats_created,
        user_messages=msg_counts.get("user", 0),
        assistant_messages=msg_counts.get("assistant", 0),
        dormant_users=dormant_users,
    )


@router.get("/engagement/daily", response_model=list[AnalyticsDailyPoint])
def analytics_engagement_daily(
    org_id: str | None = None,
    month: str | None = None,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[AnalyticsDailyPoint]:
    org_uuid = _parse_optional_uuid(org_id, invalid_detail="Invalid org id")
    _require_analytics_admin(current_user, session, org_uuid)
    _, start, end = _resolve_month(month)

    day_expr = func.date_trunc("day", UsageEvent.created_at)
    day_label = func.to_char(day_expr, "YYYY-MM-DD")
    active_stmt = (
        select(day_label, func.count(func.distinct(UsageEvent.user_id)))
        .where(
            UsageEvent.created_at >= _naive(start),
            UsageEvent.created_at < _naive(end),
            UsageEvent.user_id.is_not(None),
        )
        .group_by(day_label)
    )
    if org_uuid:
        active_stmt = active_stmt.where(UsageEvent.org_id == org_uuid)
    active_map = {
        str(day): int(count or 0) for day, count in session.exec(active_stmt).all()
    }

    chat_day = func.to_char(func.date_trunc("day", Chat.created_at), "YYYY-MM-DD")
    chat_stmt = (
        select(chat_day, func.count())
        .where(
            *_chat_base_filter(org_uuid),
            Chat.created_at >= _naive(start),
            Chat.created_at < _naive(end),
        )
        .group_by(chat_day)
    )
    chat_map = {str(day): int(count or 0) for day, count in session.exec(chat_stmt).all()}

    msg_day = func.to_char(func.date_trunc("day", ChatMessage.created_at), "YYYY-MM-DD")
    msg_stmt = (
        select(msg_day, func.count())
        .join(Chat, Chat.id == ChatMessage.chat_id)
        .where(
            *_chat_base_filter(org_uuid),
            ChatMessage.created_at >= _naive(start),
            ChatMessage.created_at < _naive(end),
            ChatMessage.is_current.is_(True),
            col(ChatMessage.role).in_(("user", "assistant")),
        )
        .group_by(msg_day)
    )
    msg_map = {str(day): int(count or 0) for day, count in session.exec(msg_stmt).all()}

    return [
        AnalyticsDailyPoint(
            date=day,
            active_users=active_map.get(day, 0),
            chats_created=chat_map.get(day, 0),
            messages=msg_map.get(day, 0),
        )
        for day in _iter_dates(start.date(), end.date())
    ]


@router.get("/users", response_model=list[AnalyticsUserRow])
def analytics_users(
    org_id: str | None = None,
    month: str | None = None,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[AnalyticsUserRow]:
    org_uuid = _parse_optional_uuid(org_id, invalid_detail="Invalid org id")
    _require_analytics_admin(current_user, session, org_uuid)
    _, start, end = _resolve_month(month)
    as_of = _as_of(end)
    dormant_since = as_of - timedelta(days=DORMANT_DAYS)

    member_ids = _member_user_ids(session, org_uuid)
    if not member_ids:
        return []

    users = session.exec(select(User).where(col(User.id).in_(member_ids))).all()
    user_map = {user.id: user for user in users}

    last_active_stmt = select(
        UsageEvent.user_id, func.max(UsageEvent.created_at)
    ).where(UsageEvent.user_id.is_not(None))
    if org_uuid:
        last_active_stmt = last_active_stmt.where(UsageEvent.org_id == org_uuid)
    last_active_stmt = last_active_stmt.group_by(UsageEvent.user_id)
    last_active = {
        uid: ts for uid, ts in session.exec(last_active_stmt).all() if uid in member_ids
    }

    chat_stmt = (
        select(Chat.user_id, func.count())
        .where(
            *_chat_base_filter(org_uuid),
            Chat.created_at >= _naive(start),
            Chat.created_at < _naive(end),
            col(Chat.user_id).in_(member_ids),
        )
        .group_by(Chat.user_id)
    )
    chat_counts = {uid: int(count or 0) for uid, count in session.exec(chat_stmt).all()}

    msg_stmt = (
        select(Chat.user_id, func.count())
        .join(ChatMessage, ChatMessage.chat_id == Chat.id)
        .where(
            *_chat_base_filter(org_uuid),
            ChatMessage.created_at >= _naive(start),
            ChatMessage.created_at < _naive(end),
            ChatMessage.is_current.is_(True),
            ChatMessage.role == "user",
            col(Chat.user_id).in_(member_ids),
        )
        .group_by(Chat.user_id)
    )
    msg_counts = {uid: int(count or 0) for uid, count in session.exec(msg_stmt).all()}

    rows: list[AnalyticsUserRow] = []
    dormant_cutoff = _naive(dormant_since)
    for user_id in member_ids:
        user = user_map.get(user_id)
        last = last_active.get(user_id)
        last_naive = _naive(last) if isinstance(last, datetime) else None
        dormant = last_naive is None or last_naive < dormant_cutoff
        rows.append(
            AnalyticsUserRow(
                user_id=str(user_id),
                name=user_display_label(user) or "Unknown user",
                last_active_at=last,
                chats=chat_counts.get(user_id, 0),
                messages=msg_counts.get(user_id, 0),
                dormant=dormant,
            )
        )
    rows.sort(key=lambda row: (row.dormant, -(row.messages), row.name.lower()))
    return rows


@router.get("/features", response_model=AnalyticsFeaturesResponse)
def analytics_features(
    org_id: str | None = None,
    month: str | None = None,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AnalyticsFeaturesResponse:
    org_uuid = _parse_optional_uuid(org_id, invalid_detail="Invalid org id")
    _require_analytics_admin(current_user, session, org_uuid)
    _, start, end = _resolve_month(month)
    start_n, end_n = _naive(start), _naive(end)
    features: list[AnalyticsFeatureRow] = []

    # Agent chats in period
    agent_chat_stmt = select(func.count(), func.count(func.distinct(Chat.user_id))).where(
        *_chat_base_filter(org_uuid),
        Chat.agent_id.is_not(None),
        Chat.created_at >= start_n,
        Chat.created_at < end_n,
    )
    agent_count, agent_users = session.exec(agent_chat_stmt).one()
    features.append(
        AnalyticsFeatureRow(
            feature="agent_chats",
            label=_feature_label("agent_chats"),
            unique_users=int(agent_users or 0),
            event_count=int(agent_count or 0),
        )
    )

    shared_stmt = select(func.count(), func.count(func.distinct(Chat.user_id))).where(
        *_chat_base_filter(org_uuid),
        Chat.share_token.is_not(None),
        Chat.created_at >= start_n,
        Chat.created_at < end_n,
    )
    shared_count, shared_users = session.exec(shared_stmt).one()
    features.append(
        AnalyticsFeatureRow(
            feature="shared_chats",
            label=_feature_label("shared_chats"),
            unique_users=int(shared_users or 0),
            event_count=int(shared_count or 0),
        )
    )

    attach_stmt = (
        select(
            func.count(ChatMessageAttachment.id),
            func.count(func.distinct(Chat.user_id)),
        )
        .select_from(ChatMessageAttachment)
        .join(ChatMessage, ChatMessage.id == ChatMessageAttachment.message_id)
        .join(Chat, Chat.id == ChatMessage.chat_id)
        .where(
            *_chat_base_filter(org_uuid),
            ChatMessageAttachment.created_at >= start_n,
            ChatMessageAttachment.created_at < end_n,
        )
    )
    attach_count, attach_users = session.exec(attach_stmt).one()
    features.append(
        AnalyticsFeatureRow(
            feature="attachments",
            label=_feature_label("attachments"),
            unique_users=int(attach_users or 0),
            event_count=int(attach_count or 0),
        )
    )

    install_stmt = select(
        func.count(),
        func.count(func.distinct(IntegrationUserInstall.user_id)),
    ).where(
        IntegrationUserInstall.status == "connected",
        IntegrationUserInstall.created_at < end_n,
    )
    if org_uuid:
        install_stmt = install_stmt.where(IntegrationUserInstall.org_id == org_uuid)
    install_count, install_users = session.exec(install_stmt).one()
    features.append(
        AnalyticsFeatureRow(
            feature="integrations",
            label=_feature_label("integrations"),
            unique_users=int(install_users or 0),
            event_count=int(install_count or 0),
        )
    )

    mcp_stmt = (
        select(
            func.count(McpUserConnection.id),
            func.count(func.distinct(McpUserConnection.user_id)),
        )
        .select_from(McpUserConnection)
        .join(McpServer, McpServer.id == McpUserConnection.server_id)
        .where(
            McpUserConnection.status == "connected",
            McpUserConnection.created_at < end_n,
        )
    )
    if org_uuid:
        member_ids = _member_user_ids(session, org_uuid)
        if member_ids:
            mcp_stmt = mcp_stmt.where(
                or_(
                    McpServer.org_id == org_uuid,
                    and_(
                        McpServer.scope == "instance",
                        col(McpUserConnection.user_id).in_(member_ids),
                    ),
                )
            )
        else:
            mcp_stmt = mcp_stmt.where(McpServer.org_id == org_uuid)
    mcp_count, mcp_users = session.exec(mcp_stmt).one()
    features.append(
        AnalyticsFeatureRow(
            feature="mcp_connections",
            label=_feature_label("mcp_connections"),
            unique_users=int(mcp_users or 0),
            event_count=int(mcp_count or 0),
        )
    )

    prompt_stmt = select(
        func.count(), func.count(func.distinct(Prompt.owner_user_id))
    ).where(Prompt.created_at >= start_n, Prompt.created_at < end_n)
    if org_uuid:
        prompt_stmt = prompt_stmt.where(Prompt.org_id == org_uuid)
    prompt_count, prompt_users = session.exec(prompt_stmt).one()
    features.append(
        AnalyticsFeatureRow(
            feature="prompts",
            label=_feature_label("prompts"),
            unique_users=int(prompt_users or 0),
            event_count=int(prompt_count or 0),
        )
    )

    agent_stmt = select(
        func.count(), func.count(func.distinct(Agent.owner_user_id))
    ).where(Agent.created_at >= start_n, Agent.created_at < end_n)
    if org_uuid:
        agent_stmt = agent_stmt.where(Agent.org_id == org_uuid)
    agents_count, agents_users = session.exec(agent_stmt).one()
    features.append(
        AnalyticsFeatureRow(
            feature="agents",
            label=_feature_label("agents"),
            unique_users=int(agents_users or 0),
            event_count=int(agents_count or 0),
        )
    )

    memory_stmt = (
        select(func.count(func.distinct(User.id)))
        .select_from(User)
        .join(OrgMembership, OrgMembership.user_id == User.id)
        .where(User.memory_enabled.is_(True))
    )
    if org_uuid:
        memory_stmt = memory_stmt.where(OrgMembership.org_id == org_uuid)
    memory_users = int(session.exec(memory_stmt).one() or 0)
    features.append(
        AnalyticsFeatureRow(
            feature="memory",
            label=_feature_label("memory"),
            unique_users=memory_users,
            event_count=memory_users,
        )
    )

    # Forward-looking tool feature events
    tool_stmt = (
        select(
            FeatureEvent.feature,
            func.count(),
            func.count(func.distinct(FeatureEvent.user_id)),
        )
        .where(
            FeatureEvent.created_at >= start_n,
            FeatureEvent.created_at < end_n,
        )
        .group_by(FeatureEvent.feature)
        .order_by(func.count().desc())
    )
    if org_uuid:
        tool_stmt = tool_stmt.where(FeatureEvent.org_id == org_uuid)
    for feature, count, users in session.exec(tool_stmt).all():
        features.append(
            AnalyticsFeatureRow(
                feature=str(feature),
                label=_feature_label(str(feature)),
                unique_users=int(users or 0),
                event_count=int(count or 0),
                forward_looking=True,
            )
        )

    by_org: list[AnalyticsOrgRow] = []
    if current_user.is_super_admin and org_uuid is None:
        org_active_stmt = (
            select(
                UsageEvent.org_id,
                func.count(func.distinct(UsageEvent.user_id)),
            )
            .where(
                UsageEvent.created_at >= start_n,
                UsageEvent.created_at < end_n,
                UsageEvent.org_id.is_not(None),
                UsageEvent.user_id.is_not(None),
            )
            .group_by(UsageEvent.org_id)
        )
        active_by_org = {
            oid: int(count or 0) for oid, count in session.exec(org_active_stmt).all() if oid
        }

        org_chat_stmt = (
            select(Chat.org_id, func.count())
            .where(
                Chat.is_deleted.is_(False),
                Chat.is_incognito.is_(False),
                Chat.is_subagent.is_(False),
                Chat.created_at >= start_n,
                Chat.created_at < end_n,
            )
            .group_by(Chat.org_id)
        )
        chats_by_org = {
            oid: int(count or 0) for oid, count in session.exec(org_chat_stmt).all() if oid
        }

        # Top tool feature per org (forward-looking); fall back to agent_chats count
        top_tool_stmt = (
            select(
                FeatureEvent.org_id,
                FeatureEvent.feature,
                func.count().label("cnt"),
            )
            .where(
                FeatureEvent.created_at >= start_n,
                FeatureEvent.created_at < end_n,
            )
            .group_by(FeatureEvent.org_id, FeatureEvent.feature)
        )
        top_feature: dict[UUID, tuple[str, int]] = {}
        for oid, feature, cnt in session.exec(top_tool_stmt).all():
            if oid is None:
                continue
            current = top_feature.get(oid)
            if current is None or int(cnt or 0) > current[1]:
                top_feature[oid] = (str(feature), int(cnt or 0))

        orgs = session.exec(select(Org).order_by(Org.name)).all()
        for org in orgs:
            top = top_feature.get(org.id)
            by_org.append(
                AnalyticsOrgRow(
                    org_id=str(org.id),
                    name=org.name,
                    active_users=active_by_org.get(org.id, 0),
                    chats_created=chats_by_org.get(org.id, 0),
                    top_feature=_feature_label(top[0]) if top else None,
                )
            )
        by_org.sort(key=lambda row: (-row.active_users, row.name.lower()))

    features.sort(key=lambda row: (-row.unique_users, -row.event_count, row.label))
    return AnalyticsFeaturesResponse(features=features, by_org=by_org)
