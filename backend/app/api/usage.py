from datetime import date, datetime, timedelta, timezone
from uuid import UUID
import re

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlmodel import Session, select

from app.api.deps import get_current_user, get_db
from app.models import ChatModel, OrgModel, OrgMembership, UsageEvent, User
from app.services.model_pricing import estimate_token_cost_usd
from app.services.org_service import require_org_admin

router = APIRouter(prefix="/usage", tags=["usage"])


class UsageSlice(BaseModel):
    key: str
    id: str | None = None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    thinking_tokens: int
    cost_usd: float | None = None
    breakdown: list["UsageSlice"] = Field(default_factory=list)


class UsageDailyPoint(BaseModel):
    date: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    thinking_tokens: int
    cost_usd: float | None = None


class UsageUserOption(BaseModel):
    user_id: str
    name: str


class ModelUsageMeta(BaseModel):
    display_name: str
    provider: str | None = None
    model_name: str | None = None


def _parse_optional_uuid(value: str | None, *, invalid_detail: str) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=invalid_detail
        ) from exc


def _require_usage_admin(
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


def _apply_month_filter(stmt, month: str | None):
    if not month:
        return stmt
    start, end = _parse_month_bounds(month)
    return stmt.where(UsageEvent.created_at >= start, UsageEvent.created_at < end)


def _entity_id(value) -> str | None:
    if value is None:
        return None
    return str(value)


def _empty_slice(key: str, entity_id: str | None = None) -> UsageSlice:
    return UsageSlice(
        key=key,
        id=entity_id,
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        input_tokens=0,
        output_tokens=0,
        cached_tokens=0,
        thinking_tokens=0,
    )


def _slice_from_row(
    key: str,
    row,
    start_index: int,
    cost_usd: float | None = None,
) -> UsageSlice:
    return UsageSlice(
        key=key,
        prompt_tokens=int(row[start_index] or 0),
        completion_tokens=int(row[start_index + 1] or 0),
        total_tokens=int(row[start_index + 2] or 0),
        input_tokens=int(row[start_index + 3] or 0),
        output_tokens=int(row[start_index + 4] or 0),
        cached_tokens=int(row[start_index + 5] or 0),
        thinking_tokens=int(row[start_index + 6] or 0),
        cost_usd=cost_usd,
    )


def _add_slice_totals(target: UsageSlice, source: UsageSlice) -> None:
    target.prompt_tokens += source.prompt_tokens
    target.completion_tokens += source.completion_tokens
    target.total_tokens += source.total_tokens
    target.input_tokens += source.input_tokens
    target.output_tokens += source.output_tokens
    target.cached_tokens += source.cached_tokens
    target.thinking_tokens += source.thinking_tokens


def _model_usage_map(session: Session) -> dict:
    return {
        model.id: ModelUsageMeta(
            display_name=model.display_name,
            provider=model.provider,
            model_name=model.model_name,
        )
        for model in session.exec(select(ChatModel)).all()
    }


def _cost_for_model_row(row, start_index: int, meta: ModelUsageMeta | None) -> float | None:
    cost_usd = estimate_token_cost_usd(
        meta.provider if meta else None,
        meta.model_name if meta else None,
        int(row[start_index + 3] or 0),
        int(row[start_index + 4] or 0),
        int(row[start_index + 5] or 0),
        int(row[start_index + 6] or 0),
    )
    if cost_usd is not None or not meta:
        return cost_usd
    return estimate_token_cost_usd(
        meta.provider,
        meta.display_name,
        int(row[start_index + 3] or 0),
        int(row[start_index + 4] or 0),
        int(row[start_index + 5] or 0),
        int(row[start_index + 6] or 0),
    )


def _apply_org_filter(stmt, org_uuid: UUID | None):
    if not org_uuid:
        return stmt
    return stmt.where(UsageEvent.org_id == org_uuid)


def _apply_user_filter(stmt, user_uuid: UUID | None):
    if not user_uuid:
        return stmt
    return stmt.where(UsageEvent.user_id == user_uuid)


def _apply_model_filter(stmt, model_uuid: UUID | None):
    if not model_uuid:
        return stmt
    return stmt.where(UsageEvent.model_id == model_uuid)


def _empty_daily_point(day: str) -> UsageDailyPoint:
    return UsageDailyPoint(
        date=day,
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        input_tokens=0,
        output_tokens=0,
        cached_tokens=0,
        thinking_tokens=0,
        cost_usd=0.0,
    )


def _iter_dates(start: date, end_exclusive: date) -> list[str]:
    days: list[str] = []
    cursor = start
    while cursor < end_exclusive:
        days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return days


def _aggregate_daily_points(rows, model_map: dict) -> list[UsageDailyPoint]:
    points: dict[str, UsageDailyPoint] = {}
    missing_cost_days: set[str] = set()
    for row in rows:
        day_key = str(row[0])
        meta = model_map.get(row[1])
        cost_usd = _cost_for_model_row(row, 2, meta)
        child = _slice_from_row(day_key, row, 2, cost_usd)
        point = points.get(day_key)
        if point is None:
            point = _empty_daily_point(day_key)
            points[day_key] = point
        point.prompt_tokens += child.prompt_tokens
        point.completion_tokens += child.completion_tokens
        point.total_tokens += child.total_tokens
        point.input_tokens += child.input_tokens
        point.output_tokens += child.output_tokens
        point.cached_tokens += child.cached_tokens
        point.thinking_tokens += child.thinking_tokens
        if child.cost_usd is None:
            if child.total_tokens:
                missing_cost_days.add(day_key)
            continue
        point.cost_usd = (point.cost_usd or 0) + child.cost_usd
    for day_key in missing_cost_days:
        points[day_key].cost_usd = None
    return [points[key] for key in sorted(points)]


def _fill_daily_points(
    points: list[UsageDailyPoint], month: str | None
) -> list[UsageDailyPoint]:
    by_date = {point.date: point for point in points}
    if month:
        start, end = _parse_month_bounds(month)
        days = _iter_dates(start.date(), end.date())
    elif by_date:
        first = datetime.fromisoformat(min(by_date)).date()
        last = datetime.fromisoformat(max(by_date)).date() + timedelta(days=1)
        days = _iter_dates(first, last)
    else:
        return []
    return [by_date.get(day, _empty_daily_point(day)) for day in days]


def _finalize_group_costs(
    rows: list[UsageSlice], missing_cost_keys: set[str]
) -> list[UsageSlice]:
    for row in rows:
        identity = row.id or row.key
        if identity in missing_cost_keys:
            row.cost_usd = None
            continue
        known_costs = [child.cost_usd for child in row.breakdown if child.cost_usd is not None]
        row.cost_usd = sum(known_costs) if known_costs else row.cost_usd
    return rows


@router.get("", response_model=list[UsageSlice])
def usage_summary(
    org_id: str | None = None,
    group_by: str = "model",
    month: str | None = None,
    user_id: str | None = None,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[UsageSlice]:
    org_uuid = _parse_optional_uuid(org_id, invalid_detail="Invalid org id")
    user_uuid = _parse_optional_uuid(user_id, invalid_detail="Invalid user id")
    _require_usage_admin(current_user, session, org_uuid)

    if group_by == "org":
        if not current_user.is_super_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only superadmins can group by organization",
            )
        stmt = (
            select(
                UsageEvent.org_id,
                func.max(UsageEvent.org_name_snapshot),
                UsageEvent.model_id,
                func.sum(UsageEvent.prompt_tokens),
                func.sum(UsageEvent.completion_tokens),
                func.sum(UsageEvent.total_tokens),
                func.sum(UsageEvent.input_tokens),
                func.sum(UsageEvent.output_tokens),
                func.sum(UsageEvent.cached_tokens),
                func.sum(UsageEvent.thinking_tokens),
            )
            .group_by(UsageEvent.org_id, UsageEvent.model_id)
        )
        if org_uuid:
            stmt = (
                stmt.where(UsageEvent.org_id == org_uuid)
                .join(OrgMembership, OrgMembership.user_id == UsageEvent.user_id)
                .where(OrgMembership.org_id == org_uuid)
            )
        stmt = _apply_user_filter(stmt, user_uuid)
        stmt = _apply_month_filter(stmt, month)
        results = session.exec(stmt).all()
        model_map = _model_usage_map(session)
        rows_by_org: dict[str, UsageSlice] = {}
        missing_cost_orgs: set[str] = set()
        for row in results:
            org_id = _entity_id(row[0])
            org_key = str(row[1] or "Unknown organization")
            dict_key = org_id or org_key
            meta = model_map.get(row[2])
            model_key = meta.display_name if meta else str(row[2] or "Unknown model")
            cost_usd = _cost_for_model_row(row, 3, meta)
            child = _slice_from_row(model_key, row, 3, cost_usd)
            parent = rows_by_org.setdefault(dict_key, _empty_slice(org_key, org_id))
            _add_slice_totals(parent, child)
            parent.breakdown.append(child)
            if child.cost_usd is None and child.total_tokens:
                missing_cost_orgs.add(dict_key)
        return _finalize_group_costs(list(rows_by_org.values()), missing_cost_orgs)

    if group_by == "user":
        stmt = (
            select(
                UsageEvent.user_id,
                func.max(UsageEvent.user_name_snapshot),
                UsageEvent.model_id,
                func.sum(UsageEvent.prompt_tokens),
                func.sum(UsageEvent.completion_tokens),
                func.sum(UsageEvent.total_tokens),
                func.sum(UsageEvent.input_tokens),
                func.sum(UsageEvent.output_tokens),
                func.sum(UsageEvent.cached_tokens),
                func.sum(UsageEvent.thinking_tokens),
            )
            .group_by(UsageEvent.user_id, UsageEvent.model_id)
        )
        stmt = _apply_org_filter(stmt, org_uuid)
        stmt = _apply_user_filter(stmt, user_uuid)
        stmt = _apply_month_filter(stmt, month)
        results = session.exec(stmt).all()
        model_map = _model_usage_map(session)
        rows_by_user: dict[str, UsageSlice] = {}
        missing_cost_users: set[str] = set()
        for row in results:
            user_id = _entity_id(row[0])
            user_key = str(row[1] or "Unknown user")
            dict_key = user_id or user_key
            meta = model_map.get(row[2])
            model_key = meta.display_name if meta else str(row[2] or "Unknown model")
            cost_usd = _cost_for_model_row(row, 3, meta)
            child = _slice_from_row(model_key, row, 3, cost_usd)
            parent = rows_by_user.setdefault(dict_key, _empty_slice(user_key, user_id))
            _add_slice_totals(parent, child)
            parent.breakdown.append(child)
            if child.cost_usd is None and child.total_tokens:
                missing_cost_users.add(dict_key)
        return _finalize_group_costs(list(rows_by_user.values()), missing_cost_users)

    if group_by == "month":
        month_expr = func.date_trunc("month", UsageEvent.created_at)
        month_label = func.to_char(month_expr, "YYYY-MM")
        stmt = (
            select(
                month_label,
                func.sum(UsageEvent.prompt_tokens),
                func.sum(UsageEvent.completion_tokens),
                func.sum(UsageEvent.total_tokens),
                func.sum(UsageEvent.input_tokens),
                func.sum(UsageEvent.output_tokens),
                func.sum(UsageEvent.cached_tokens),
                func.sum(UsageEvent.thinking_tokens),
            )
            .group_by(month_label)
            .order_by(month_label.desc())
        )
        if org_uuid:
            stmt = stmt.where(UsageEvent.org_id == org_uuid)
        stmt = _apply_user_filter(stmt, user_uuid)
        stmt = _apply_month_filter(stmt, month)
        results = session.exec(stmt).all()
        return [
            UsageSlice(
                key=str(row[0]),
                prompt_tokens=int(row[1] or 0),
                completion_tokens=int(row[2] or 0),
                total_tokens=int(row[3] or 0),
                input_tokens=int(row[4] or 0),
                output_tokens=int(row[5] or 0),
                cached_tokens=int(row[6] or 0),
                thinking_tokens=int(row[7] or 0),
            )
            for row in results
        ]
    if group_by == "user_month":
        month_expr = func.date_trunc("month", UsageEvent.created_at)
        month_label = func.to_char(month_expr, "YYYY-MM")
        stmt = (
            select(
                month_label,
                UsageEvent.user_name_snapshot,
                func.sum(UsageEvent.prompt_tokens),
                func.sum(UsageEvent.completion_tokens),
                func.sum(UsageEvent.total_tokens),
                func.sum(UsageEvent.input_tokens),
                func.sum(UsageEvent.output_tokens),
                func.sum(UsageEvent.cached_tokens),
                func.sum(UsageEvent.thinking_tokens),
            )
            .group_by(month_label, UsageEvent.user_name_snapshot)
            .order_by(month_label.desc())
        )
        if org_uuid:
            stmt = stmt.where(UsageEvent.org_id == org_uuid)
        stmt = _apply_user_filter(stmt, user_uuid)
        stmt = _apply_month_filter(stmt, month)
        results = session.exec(stmt).all()
        return [
            UsageSlice(
                key=f"{row[0]} — {row[1]}",
                prompt_tokens=int(row[2] or 0),
                completion_tokens=int(row[3] or 0),
                total_tokens=int(row[4] or 0),
                input_tokens=int(row[5] or 0),
                output_tokens=int(row[6] or 0),
                cached_tokens=int(row[7] or 0),
                thinking_tokens=int(row[8] or 0),
            )
            for row in results
        ]
    if group_by == "model_month":
        month_expr = func.date_trunc("month", UsageEvent.created_at)
        month_label = func.to_char(month_expr, "YYYY-MM")
        stmt = (
            select(
                month_label,
                UsageEvent.model_id,
                func.sum(UsageEvent.prompt_tokens),
                func.sum(UsageEvent.completion_tokens),
                func.sum(UsageEvent.total_tokens),
                func.sum(UsageEvent.input_tokens),
                func.sum(UsageEvent.output_tokens),
                func.sum(UsageEvent.cached_tokens),
                func.sum(UsageEvent.thinking_tokens),
            )
            .group_by(month_label, UsageEvent.model_id)
            .order_by(month_label.desc())
        )
        if org_uuid:
            stmt = stmt.where(UsageEvent.org_id == org_uuid)
        stmt = _apply_user_filter(stmt, user_uuid)
        stmt = _apply_month_filter(stmt, month)
        results = session.exec(stmt).all()
        model_map = {
            model.id: model.display_name
            for model in session.exec(select(ChatModel)).all()
        }
        return [
            UsageSlice(
                key=f"{row[0]} — {model_map.get(row[1], str(row[1]))}",
                prompt_tokens=int(row[2] or 0),
                completion_tokens=int(row[3] or 0),
                total_tokens=int(row[4] or 0),
                input_tokens=int(row[5] or 0),
                output_tokens=int(row[6] or 0),
                cached_tokens=int(row[7] or 0),
                thinking_tokens=int(row[8] or 0),
            )
            for row in results
        ]

    stmt = (
        select(
            UsageEvent.model_id,
            UsageEvent.user_name_snapshot,
            func.sum(UsageEvent.prompt_tokens),
            func.sum(UsageEvent.completion_tokens),
            func.sum(UsageEvent.total_tokens),
            func.sum(UsageEvent.input_tokens),
            func.sum(UsageEvent.output_tokens),
            func.sum(UsageEvent.cached_tokens),
            func.sum(UsageEvent.thinking_tokens),
        )
        .group_by(UsageEvent.model_id, UsageEvent.user_name_snapshot)
    )
    if org_uuid:
        stmt = (
            stmt.where(UsageEvent.org_id == org_uuid)
            .join(OrgModel, OrgModel.model_id == UsageEvent.model_id)
            .where(OrgModel.org_id == org_uuid)
        )
    stmt = _apply_user_filter(stmt, user_uuid)
    stmt = _apply_month_filter(stmt, month)
    results = session.exec(stmt).all()

    model_map = _model_usage_map(session)
    rows_by_model: dict[str, UsageSlice] = {}
    missing_cost_models: set[str] = set()
    for row in results:
        model_id = _entity_id(row[0])
        meta = model_map.get(row[0])
        model_key = meta.display_name if meta else str(row[0] or "Unknown model")
        dict_key = model_id or model_key
        user_key = row[1]
        cost_usd = _cost_for_model_row(row, 2, meta)
        child = _slice_from_row(user_key, row, 2, cost_usd)
        parent = rows_by_model.setdefault(dict_key, _empty_slice(model_key, model_id))
        _add_slice_totals(parent, child)
        parent.breakdown.append(child)
        if child.cost_usd is None and child.total_tokens:
            missing_cost_models.add(dict_key)
    return _finalize_group_costs(list(rows_by_model.values()), missing_cost_models)


@router.get("/months", response_model=list[str])
def usage_months(
    org_id: str | None = None,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[str]:
    org_uuid = _parse_optional_uuid(org_id, invalid_detail="Invalid org id")
    _require_usage_admin(current_user, session, org_uuid)

    month_expr = func.date_trunc("month", UsageEvent.created_at)
    month_label = func.to_char(month_expr, "YYYY-MM")
    stmt = select(month_label).distinct().order_by(month_label.desc())
    if org_uuid:
        stmt = stmt.where(UsageEvent.org_id == org_uuid)
    results = session.exec(stmt).all()
    months: list[str] = []
    for row in results:
        value = row[0] if isinstance(row, (tuple, list)) else row
        if value:
            months.append(str(value))
    return months


@router.get("/users", response_model=list[UsageUserOption])
def usage_users(
    org_id: str | None = None,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[UsageUserOption]:
    org_uuid = _parse_optional_uuid(org_id, invalid_detail="Invalid org id")
    _require_usage_admin(current_user, session, org_uuid)

    stmt = (
        select(UsageEvent.user_id, func.max(UsageEvent.user_name_snapshot))
        .where(UsageEvent.user_id.is_not(None))
        .group_by(UsageEvent.user_id)
        .order_by(func.max(UsageEvent.user_name_snapshot))
    )
    stmt = _apply_org_filter(stmt, org_uuid)
    results = session.exec(stmt).all()
    users: list[UsageUserOption] = []
    for row in results:
        user_id = row[0]
        if not user_id:
            continue
        users.append(
            UsageUserOption(
                user_id=str(user_id),
                name=str(row[1] or "Unknown user"),
            )
        )
    return users


@router.get("/daily", response_model=list[UsageDailyPoint])
def usage_daily(
    org_id: str | None = None,
    month: str | None = None,
    user_id: str | None = None,
    model_id: str | None = None,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[UsageDailyPoint]:
    org_uuid = _parse_optional_uuid(org_id, invalid_detail="Invalid org id")
    user_uuid = _parse_optional_uuid(user_id, invalid_detail="Invalid user id")
    model_uuid = _parse_optional_uuid(model_id, invalid_detail="Invalid model id")
    _require_usage_admin(current_user, session, org_uuid)

    day_expr = func.date_trunc("day", UsageEvent.created_at)
    day_label = func.to_char(day_expr, "YYYY-MM-DD")
    stmt = select(
        day_label,
        UsageEvent.model_id,
        func.sum(UsageEvent.prompt_tokens),
        func.sum(UsageEvent.completion_tokens),
        func.sum(UsageEvent.total_tokens),
        func.sum(UsageEvent.input_tokens),
        func.sum(UsageEvent.output_tokens),
        func.sum(UsageEvent.cached_tokens),
        func.sum(UsageEvent.thinking_tokens),
    ).group_by(day_label, UsageEvent.model_id)
    stmt = _apply_org_filter(stmt, org_uuid)
    stmt = _apply_user_filter(stmt, user_uuid)
    stmt = _apply_model_filter(stmt, model_uuid)
    stmt = _apply_month_filter(stmt, month)
    results = session.exec(stmt).all()
    model_map = _model_usage_map(session)
    return _fill_daily_points(_aggregate_daily_points(results, model_map), month)
