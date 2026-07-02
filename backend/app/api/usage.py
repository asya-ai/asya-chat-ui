from datetime import datetime, timezone
from uuid import UUID
import re

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlmodel import Session, select

from app.api.deps import get_current_user, get_db
from app.models import ChatModel, Org, OrgModel, OrgMembership, UsageEvent, User
from app.services.model_pricing import estimate_token_cost_usd
from app.services.org_service import require_org_admin

router = APIRouter(prefix="/usage", tags=["usage"])


class UsageSlice(BaseModel):
    key: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    thinking_tokens: int
    cost_usd: float | None = None
    breakdown: list["UsageSlice"] = Field(default_factory=list)


class ModelUsageMeta(BaseModel):
    display_name: str
    provider: str | None = None
    model_name: str | None = None


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


def _empty_slice(key: str) -> UsageSlice:
    return UsageSlice(
        key=key,
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


def _finalize_group_costs(
    rows: list[UsageSlice], missing_cost_keys: set[str]
) -> list[UsageSlice]:
    for row in rows:
        if row.key in missing_cost_keys:
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
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[UsageSlice]:
    org_uuid: UUID | None = None
    if org_id:
        try:
            org_uuid = UUID(org_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid org id"
            ) from exc

    if not current_user.is_super_admin:
        if not org_uuid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="org_id is required"
            )
        require_org_admin(session, org_uuid, current_user.id)

    if group_by == "org":
        if not current_user.is_super_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only superadmins can group by organization",
            )
        stmt = (
            select(
                UsageEvent.org_id,
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
        stmt = _apply_month_filter(stmt, month)
        results = session.exec(stmt).all()
        org_map = {
            org.id: org.name for org in session.exec(select(Org)).all()
        }
        model_map = _model_usage_map(session)
        rows_by_org: dict[str, UsageSlice] = {}
        missing_cost_orgs: set[str] = set()
        for row in results:
            org_key = org_map.get(row[0], str(row[0]))
            meta = model_map.get(row[1])
            model_key = meta.display_name if meta else str(row[1] or "Unknown model")
            cost_usd = _cost_for_model_row(row, 2, meta)
            child = _slice_from_row(model_key, row, 2, cost_usd)
            parent = rows_by_org.setdefault(org_key, _empty_slice(org_key))
            _add_slice_totals(parent, child)
            parent.breakdown.append(child)
            if child.cost_usd is None and child.total_tokens:
                missing_cost_orgs.add(org_key)
        return _finalize_group_costs(list(rows_by_org.values()), missing_cost_orgs)

    if group_by == "user":
        stmt = (
            select(
                UsageEvent.user_id,
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
        stmt = _apply_month_filter(stmt, month)
        results = session.exec(stmt).all()
        user_map = {user.id: user.email for user in session.exec(select(User)).all()}
        model_map = _model_usage_map(session)
        rows_by_user: dict[str, UsageSlice] = {}
        missing_cost_users: set[str] = set()
        for row in results:
            user_key = user_map.get(row[0], str(row[0]))
            meta = model_map.get(row[1])
            model_key = meta.display_name if meta else str(row[1] or "Unknown model")
            cost_usd = _cost_for_model_row(row, 2, meta)
            child = _slice_from_row(model_key, row, 2, cost_usd)
            parent = rows_by_user.setdefault(user_key, _empty_slice(user_key))
            _add_slice_totals(parent, child)
            parent.breakdown.append(child)
            if child.cost_usd is None and child.total_tokens:
                missing_cost_users.add(user_key)
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
                UsageEvent.user_id,
                func.sum(UsageEvent.prompt_tokens),
                func.sum(UsageEvent.completion_tokens),
                func.sum(UsageEvent.total_tokens),
                func.sum(UsageEvent.input_tokens),
                func.sum(UsageEvent.output_tokens),
                func.sum(UsageEvent.cached_tokens),
                func.sum(UsageEvent.thinking_tokens),
            )
            .group_by(month_label, UsageEvent.user_id)
            .order_by(month_label.desc())
        )
        if org_uuid:
            stmt = stmt.where(UsageEvent.org_id == org_uuid)
        stmt = _apply_month_filter(stmt, month)
        results = session.exec(stmt).all()
        user_map = {user.id: user.email for user in session.exec(select(User)).all()}
        return [
            UsageSlice(
                key=f"{row[0]} — {user_map.get(row[1], str(row[1]))}",
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
            UsageEvent.user_id,
            func.sum(UsageEvent.prompt_tokens),
            func.sum(UsageEvent.completion_tokens),
            func.sum(UsageEvent.total_tokens),
            func.sum(UsageEvent.input_tokens),
            func.sum(UsageEvent.output_tokens),
            func.sum(UsageEvent.cached_tokens),
            func.sum(UsageEvent.thinking_tokens),
        )
        .group_by(UsageEvent.model_id, UsageEvent.user_id)
    )
    if org_uuid:
        stmt = (
            stmt.where(UsageEvent.org_id == org_uuid)
            .join(OrgModel, OrgModel.model_id == UsageEvent.model_id)
            .where(OrgModel.org_id == org_uuid)
        )
    stmt = _apply_month_filter(stmt, month)
    results = session.exec(stmt).all()

    model_map = _model_usage_map(session)
    user_map = {user.id: user.email for user in session.exec(select(User)).all()}
    rows_by_model: dict[str, UsageSlice] = {}
    missing_cost_models: set[str] = set()
    for row in results:
        meta = model_map.get(row[0])
        model_key = meta.display_name if meta else str(row[0] or "Unknown model")
        user_key = user_map.get(row[1], str(row[1]))
        cost_usd = _cost_for_model_row(row, 2, meta)
        child = _slice_from_row(user_key, row, 2, cost_usd)
        parent = rows_by_model.setdefault(model_key, _empty_slice(model_key))
        _add_slice_totals(parent, child)
        parent.breakdown.append(child)
        if child.cost_usd is None and child.total_tokens:
            missing_cost_models.add(model_key)
    return _finalize_group_costs(list(rows_by_model.values()), missing_cost_models)


@router.get("/months", response_model=list[str])
def usage_months(
    org_id: str | None = None,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[str]:
    org_uuid: UUID | None = None
    if org_id:
        try:
            org_uuid = UUID(org_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid org id"
            ) from exc

    if not current_user.is_super_admin:
        if not org_uuid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="org_id is required"
            )
        require_org_admin(session, org_uuid, current_user.id)

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
