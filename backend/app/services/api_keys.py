import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.core.config import settings
from app.models import ApiKey, OrgMembership, User

API_KEY_PREFIX = "ak_"
API_KEY_PREFIX_LEN = 8


def _hash_api_key(raw_key: str) -> str:
    return hmac.new(
        settings.secret_key.encode("utf-8"),
        raw_key.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def generate_api_key() -> tuple[str, str, str]:
    raw = API_KEY_PREFIX + secrets.token_urlsafe(32)
    prefix = raw[:API_KEY_PREFIX_LEN]
    return raw, prefix, _hash_api_key(raw)


@dataclass
class ApiKeyAuth:
    user: User
    org_id: str
    api_key: ApiKey


def resolve_org_id_for_user(
    session: Session, user: User, org_id_header: str | None
) -> str:
    if org_id_header:
        try:
            org_uuid = UUID(org_id_header)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid X-Org-Id",
            ) from exc
        org_membership = session.exec(
            select(OrgMembership).where(
                OrgMembership.user_id == user.id,
                OrgMembership.org_id == org_uuid,
            )
        ).first()
        if org_membership or user.is_super_admin:
            return str(org_uuid)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Org membership required",
        )
    if user.is_super_admin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Org-Id required for super admin",
        )
    membership = session.exec(
        select(OrgMembership).where(OrgMembership.user_id == user.id)
    ).first()
    if membership:
        return str(membership.org_id)
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Organization not found for user",
    )


def authenticate_api_key(session: Session, raw_key: str) -> ApiKeyAuth:
    if not raw_key.startswith(API_KEY_PREFIX):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
        )
    prefix = raw_key[:API_KEY_PREFIX_LEN]
    api_key = session.exec(
        select(ApiKey).where(ApiKey.prefix == prefix)
    ).first()
    if not api_key or api_key.revoked_at:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
        )
    if not hmac.compare_digest(api_key.key_hash, _hash_api_key(raw_key)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
        )
    api_key.last_used_at = datetime.now(timezone.utc)
    session.add(api_key)
    session.commit()
    user = session.exec(select(User).where(User.id == api_key.user_id)).first()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user",
        )
    return ApiKeyAuth(user=user, org_id=str(api_key.org_id), api_key=api_key)
