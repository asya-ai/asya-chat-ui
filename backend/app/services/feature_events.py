from __future__ import annotations

from typing import Iterable
from uuid import UUID

from sqlmodel import Session

from app.models import FeatureEvent
from app.services.mcp.bridge import parse_mcp_tool_name

_TRACKED_BUILTIN = frozenset({"web_search", "code_execution"})


def feature_key_for_tool(name: str | None) -> str | None:
    raw = (name or "").strip()
    if not raw:
        return None
    if raw in _TRACKED_BUILTIN:
        return raw
    parsed = parse_mcp_tool_name(raw)
    if parsed:
        return f"mcp:{parsed[0]}"
    return None


def persist_feature_events(
    session: Session,
    *,
    org_id: UUID,
    user_id: UUID,
    chat_id: UUID | None,
    features: Iterable[str],
) -> None:
    for feature in features:
        key = (feature or "").strip()
        if not key:
            continue
        session.add(
            FeatureEvent(
                org_id=org_id,
                user_id=user_id,
                chat_id=chat_id,
                feature=key,
            )
        )
