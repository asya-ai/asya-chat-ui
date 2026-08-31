from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlmodel import Session

from app.models import ChatModel, OrgModel, UsageEvent
from app.services.tools.image_tool import image_usage_token_fields

_TOOL_USAGE_TOKEN_KEYS = tuple(image_usage_token_fields({}).keys())


def merge_tool_usage_fields(
    target: dict[str, int], source: dict[str, int] | None
) -> dict[str, int]:
    if not source:
        return target
    for key in _TOOL_USAGE_TOKEN_KEYS:
        target[key] = int(target.get(key) or 0) + int(source.get(key) or 0)
    return target


def perplexity_usage_fields(usage: dict[str, Any] | None) -> dict[str, int]:
    usage = usage if isinstance(usage, dict) else {}
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or 0) or prompt_tokens + completion_tokens
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "input_tokens": prompt_tokens,
        "output_tokens": completion_tokens,
        "cached_tokens": 0,
        "thinking_tokens": 0,
    }


def resolve_service_model_id(
    session: Session,
    org_id: UUID,
    provider: str,
    model_name: str,
    *,
    display_name: str | None = None,
) -> UUID:
    model = session.exec(
        select(ChatModel).where(
            ChatModel.provider == provider,
            ChatModel.model_name == model_name,
        )
    ).first()
    if not model:
        model = ChatModel(
            provider=provider,
            model_name=model_name,
            display_name=display_name or f"{provider} {model_name}",
            is_active=False,
        )
        session.add(model)
        session.flush()
    org_link = session.exec(
        select(OrgModel).where(
            OrgModel.org_id == org_id,
            OrgModel.model_id == model.id,
        )
    ).first()
    if not org_link:
        session.add(OrgModel(org_id=org_id, model_id=model.id, is_enabled=False))
        session.flush()
    return model.id


def resolve_tool_model_usage_id(session: Session, org_id: UUID, item: dict[str, Any]) -> UUID | None:
    model_id = item.get("model_id")
    if model_id:
        return UUID(str(model_id))
    provider = item.get("provider")
    model_name = item.get("model_name")
    if isinstance(provider, str) and isinstance(model_name, str) and provider and model_name:
        return resolve_service_model_id(session, org_id, provider, model_name)
    return None


def usage_event_from_tool_model_item(
    *,
    org_id: UUID,
    user_id: UUID,
    chat_id: UUID,
    message_id: UUID,
    model_id: UUID,
    item: dict[str, Any],
) -> UsageEvent:
    fields = image_usage_token_fields(item)
    return UsageEvent(
        org_id=org_id,
        user_id=user_id,
        chat_id=chat_id,
        message_id=message_id,
        model_id=model_id,
        prompt_tokens=fields["prompt_tokens"],
        completion_tokens=fields["completion_tokens"],
        total_tokens=fields["total_tokens"],
        input_tokens=fields["input_tokens"],
        output_tokens=fields["output_tokens"],
        cached_tokens=fields["cached_tokens"],
        thinking_tokens=fields["thinking_tokens"],
        image_width=item.get("image_width"),
        image_height=item.get("image_height"),
        image_count=item.get("image_count"),
        image_format=item.get("image_format"),
    )


def persist_tool_model_usage_events(
    session: Session,
    *,
    org_id: UUID,
    user_id: UUID,
    chat_id: UUID,
    message_id: UUID,
    items: list[dict[str, Any]],
) -> None:
    for item in items:
        model_id = resolve_tool_model_usage_id(session, org_id, item)
        if not model_id:
            continue
        session.add(
            usage_event_from_tool_model_item(
                org_id=org_id,
                user_id=user_id,
                chat_id=chat_id,
                message_id=message_id,
                model_id=model_id,
                item=item,
            )
        )
