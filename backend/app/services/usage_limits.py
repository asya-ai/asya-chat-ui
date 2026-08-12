"""Monthly chat usage cost ceilings for orgs and users."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlmodel import Session, select

from app.models import ChatModel, Org, UsageEvent, User
from app.services.model_pricing import estimate_token_cost_usd

CHAT_USAGE_LIMIT_EXCEEDED = "Chat usage limit exceeded"


def _current_month_bounds() -> tuple[datetime, datetime]:
    now = datetime.utcnow()
    start = datetime(now.year, now.month, 1)
    if now.month == 12:
        end = datetime(now.year + 1, 1, 1)
    else:
        end = datetime(now.year, now.month + 1, 1)
    return start, end


def estimate_scoped_usage_cost_usd(
    session: Session,
    *,
    org_id: UUID | None = None,
    user_id: UUID | None = None,
) -> float:
    """Estimate USD spend for the current calendar month (UTC).

    Cost is derived from aggregated token usage per model using the same pricing
    helpers as the usage page. Unknown model prices contribute 0.
    """
    start, end = _current_month_bounds()
    stmt = (
        select(
            UsageEvent.model_id,
            func.sum(UsageEvent.input_tokens),
            func.sum(UsageEvent.output_tokens),
            func.sum(UsageEvent.cached_tokens),
            func.sum(UsageEvent.thinking_tokens),
        )
        .where(UsageEvent.created_at >= start, UsageEvent.created_at < end)
        .group_by(UsageEvent.model_id)
    )
    if org_id is not None:
        stmt = stmt.where(UsageEvent.org_id == org_id)
    if user_id is not None:
        stmt = stmt.where(UsageEvent.user_id == user_id)

    rows = session.exec(stmt).all()
    if not rows:
        return 0.0

    model_map = {
        model.id: model for model in session.exec(select(ChatModel)).all()
    }
    total = 0.0
    for model_id, input_tokens, output_tokens, cached_tokens, thinking_tokens in rows:
        model = model_map.get(model_id) if model_id else None
        cost = estimate_token_cost_usd(
            model.provider if model else None,
            model.model_name if model else None,
            int(input_tokens or 0),
            int(output_tokens or 0),
            int(cached_tokens or 0),
            int(thinking_tokens or 0),
        )
        if cost is None and model is not None:
            cost = estimate_token_cost_usd(
                model.provider,
                model.display_name,
                int(input_tokens or 0),
                int(output_tokens or 0),
                int(cached_tokens or 0),
                int(thinking_tokens or 0),
            )
        if cost is not None:
            total += cost
    return total


def enforce_chat_usage_limits(
    session: Session,
    *,
    org_id: UUID,
    user_id: UUID,
) -> None:
    """Raise 403 when org or user monthly cost ceiling is already reached."""
    org = session.exec(select(Org).where(Org.id == org_id)).first()
    if org is not None and org.cost_ceiling_usd is not None:
        org_spend = estimate_scoped_usage_cost_usd(session, org_id=org_id)
        if org_spend >= float(org.cost_ceiling_usd):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=CHAT_USAGE_LIMIT_EXCEEDED,
            )

    user = session.exec(select(User).where(User.id == user_id)).first()
    if user is not None and user.cost_ceiling_usd is not None:
        user_spend = estimate_scoped_usage_cost_usd(
            session, org_id=org_id, user_id=user_id
        )
        if user_spend >= float(user.cost_ceiling_usd):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=CHAT_USAGE_LIMIT_EXCEEDED,
            )
