"""Redis wait/publish + preferences for external write approvals."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from sqlmodel import Session, select

from app.models.entities import IntegrationWritePreference
from app.services.generation_event_bus import get_sync_redis

logger = logging.getLogger(__name__)

WriteDecision = Literal["once", "session", "always", "deny"]

SESSION_TTL_SECONDS = 12 * 60 * 60
APPROVAL_WAIT_SECONDS = 5 * 60


def session_allow_key(*, org_id: UUID, user_id: UUID, chat_id: UUID) -> str:
    return f"chatui:write_session:{org_id}:{user_id}:{chat_id}"


def approval_wait_key(task_id: UUID, request_id: str) -> str:
    return f"chatui:write_approval:{task_id}:{request_id}"


def has_session_allow(*, org_id: UUID, user_id: UUID, chat_id: UUID) -> bool:
    try:
        client = get_sync_redis()
        return bool(client.get(session_allow_key(org_id=org_id, user_id=user_id, chat_id=chat_id)))
    except Exception:
        logger.debug("session allow redis read failed", exc_info=True)
        return False


def set_session_allow(*, org_id: UUID, user_id: UUID, chat_id: UUID) -> None:
    client = get_sync_redis()
    client.setex(
        session_allow_key(org_id=org_id, user_id=user_id, chat_id=chat_id),
        SESSION_TTL_SECONDS,
        "1",
    )


def clear_session_allow(*, org_id: UUID, user_id: UUID, chat_id: UUID) -> None:
    try:
        client = get_sync_redis()
        client.delete(session_allow_key(org_id=org_id, user_id=user_id, chat_id=chat_id))
    except Exception:
        logger.debug("session allow redis delete failed", exc_info=True)


def list_session_allows(*, org_id: UUID, user_id: UUID) -> list[str]:
    """Return chat_ids with active session allow (best-effort SCAN)."""
    pattern = f"chatui:write_session:{org_id}:{user_id}:*"
    out: list[str] = []
    try:
        client = get_sync_redis()
        for key in client.scan_iter(match=pattern, count=100):
            raw = str(key)
            chat_id = raw.rsplit(":", 1)[-1]
            if chat_id:
                out.append(chat_id)
    except Exception:
        logger.debug("session allow scan failed", exc_info=True)
    return out


def get_always_allow_providers(
    session: Session, *, user_id: UUID, org_id: UUID
) -> set[str]:
    rows = session.exec(
        select(IntegrationWritePreference).where(
            IntegrationWritePreference.user_id == user_id,
            IntegrationWritePreference.org_id == org_id,
            IntegrationWritePreference.always_allow.is_(True),
        )
    ).all()
    return {row.provider_id for row in rows}


def upsert_always_allow(
    session: Session,
    *,
    user_id: UUID,
    org_id: UUID,
    provider_ids: set[str] | list[str],
) -> None:
    now = datetime.utcnow()
    for provider_id in provider_ids:
        if provider_id not in {"google", "microsoft"}:
            continue
        row = session.exec(
            select(IntegrationWritePreference).where(
                IntegrationWritePreference.user_id == user_id,
                IntegrationWritePreference.org_id == org_id,
                IntegrationWritePreference.provider_id == provider_id,
            )
        ).first()
        if row is None:
            row = IntegrationWritePreference(
                user_id=user_id,
                org_id=org_id,
                provider_id=provider_id,
                always_allow=True,
                created_at=now,
                updated_at=now,
            )
        else:
            row.always_allow = True
            row.updated_at = now
        session.add(row)
    session.commit()


def revoke_always_allow(
    session: Session,
    *,
    user_id: UUID,
    org_id: UUID,
    provider_id: str,
) -> bool:
    row = session.exec(
        select(IntegrationWritePreference).where(
            IntegrationWritePreference.user_id == user_id,
            IntegrationWritePreference.org_id == org_id,
            IntegrationWritePreference.provider_id == provider_id,
        )
    ).first()
    if row is None:
        return False
    session.delete(row)
    session.commit()
    return True


def list_always_allow(
    session: Session, *, user_id: UUID, org_id: UUID
) -> list[IntegrationWritePreference]:
    return list(
        session.exec(
            select(IntegrationWritePreference).where(
                IntegrationWritePreference.user_id == user_id,
                IntegrationWritePreference.org_id == org_id,
                IntegrationWritePreference.always_allow.is_(True),
            )
        ).all()
    )


def publish_write_decision(
    task_id: UUID, request_id: str, decision: WriteDecision
) -> None:
    client = get_sync_redis()
    key = approval_wait_key(task_id, request_id)
    client.rpush(key, json.dumps({"decision": decision}))
    client.expire(key, APPROVAL_WAIT_SECONDS + 60)


def wait_for_write_decision(
    task_id: UUID,
    request_id: str,
    *,
    timeout_seconds: int = APPROVAL_WAIT_SECONDS,
) -> WriteDecision:
    """Block until user decides or timeout (treated as deny)."""
    try:
        client = get_sync_redis()
        key = approval_wait_key(task_id, request_id)
        item = client.blpop(key, timeout=max(1, int(timeout_seconds)))
        if not item:
            return "deny"
        _key, raw = item
        payload = json.loads(raw)
        decision = str(payload.get("decision") or "deny")
        if decision in {"once", "session", "always", "deny"}:
            return decision  # type: ignore[return-value]
        return "deny"
    except Exception:
        logger.warning("write approval wait failed task=%s", task_id, exc_info=True)
        return "deny"


def providers_covered(
    *,
    providers: set[str],
    always: set[str],
    session_ok: bool,
) -> bool:
    if not providers:
        return True
    if session_ok:
        return True
    return providers.issubset(always)


def cancelled_write_output(tool_name: str, *, reason: str = "User denied write") -> dict[str, Any]:
    return {
        "error": reason,
        "cancelled": True,
        "requires_write_approval": True,
        "tool": tool_name,
    }
