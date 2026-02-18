from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import Session, select

from app.api.deps import get_current_user, get_db
from app.models import ChatModel, Org, UsageEvent, User
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


@router.get("", response_model=list[UsageSlice])
def usage_summary(
    org_id: str | None = None,
    group_by: str = "model",
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
                func.sum(UsageEvent.prompt_tokens),
                func.sum(UsageEvent.completion_tokens),
                func.sum(UsageEvent.total_tokens),
                func.sum(UsageEvent.input_tokens),
                func.sum(UsageEvent.output_tokens),
                func.sum(UsageEvent.cached_tokens),
                func.sum(UsageEvent.thinking_tokens),
            )
            .group_by(UsageEvent.org_id)
        )
        if org_uuid:
            stmt = stmt.where(UsageEvent.org_id == org_uuid)
        results = session.exec(stmt).all()
        org_map = {
            org.id: org.name for org in session.exec(select(Org)).all()
        }
        return [
            UsageSlice(
                key=org_map.get(row[0], str(row[0])),
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

    if group_by == "user":
        stmt = (
            select(
                UsageEvent.user_id,
                func.sum(UsageEvent.prompt_tokens),
                func.sum(UsageEvent.completion_tokens),
                func.sum(UsageEvent.total_tokens),
                func.sum(UsageEvent.input_tokens),
                func.sum(UsageEvent.output_tokens),
                func.sum(UsageEvent.cached_tokens),
                func.sum(UsageEvent.thinking_tokens),
            )
            .group_by(UsageEvent.user_id)
        )
        if org_uuid:
            stmt = stmt.where(UsageEvent.org_id == org_uuid)
        results = session.exec(stmt).all()
        user_map = {user.id: user.email for user in session.exec(select(User)).all()}
        return [
            UsageSlice(
                key=user_map.get(row[0], str(row[0])),
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
            func.sum(UsageEvent.prompt_tokens),
            func.sum(UsageEvent.completion_tokens),
            func.sum(UsageEvent.total_tokens),
            func.sum(UsageEvent.input_tokens),
            func.sum(UsageEvent.output_tokens),
            func.sum(UsageEvent.cached_tokens),
            func.sum(UsageEvent.thinking_tokens),
        )
        .group_by(UsageEvent.model_id)
    )
    if org_uuid:
        stmt = stmt.where(UsageEvent.org_id == org_uuid)
    results = session.exec(stmt).all()

    model_map = {
        model.id: model.display_name
        for model in session.exec(select(ChatModel)).all()
    }
    return [
        UsageSlice(
            key=model_map.get(row[0], str(row[0])),
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
