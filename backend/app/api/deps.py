from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from sqlmodel import Session, select

from app.core.security import decode_access_token_claims, oauth2_scheme
from app.db.session import get_session
from app.models import OrgMembership, User
from app.services.api_keys import API_KEY_PREFIX, authenticate_api_key


def _resolve_user_from_token_claims(
    session: Session, claims: dict[str, Any]
) -> User:
    subject = claims.get("sub")
    if not subject:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
        )
    try:
        user_id = UUID(str(subject))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
        ) from exc
    user = session.exec(select(User).where(User.id == user_id)).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user",
        )
    token_version = claims.get("ver", 0)
    try:
        token_ver_int = int(token_version)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
        ) from exc
    if token_ver_int != int(user.token_version):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
        )
    return user


def get_db() -> Session:
    yield from get_session()


def get_current_user(
    session: Session = Depends(get_db), token: str = Depends(oauth2_scheme)
) -> User:
    claims = decode_access_token_claims(token)
    return _resolve_user_from_token_claims(session, claims)


@dataclass
class AuthContext:
    user: User
    org_id: UUID
    api_key_id: UUID | None = None


def get_auth_context(
    session: Session = Depends(get_db),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
) -> AuthContext:
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
        )
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
        )
    token = authorization.split(" ", 1)[1].strip()
    if token.startswith(API_KEY_PREFIX):
        auth = authenticate_api_key(session, token)
        return AuthContext(
            user=auth.user, org_id=UUID(auth.org_id), api_key_id=auth.api_key.id
        )
    try:
        claims = decode_access_token_claims(token)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
        ) from exc
    user = _resolve_user_from_token_claims(session, claims)
    if x_org_id:
        try:
            org_id = UUID(x_org_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid X-Org-Id",
            ) from exc
        if not user.is_super_admin:
            membership = session.exec(
                select(OrgMembership).where(
                    OrgMembership.user_id == user.id,
                    OrgMembership.org_id == org_id,
                )
            ).first()
            if not membership:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Org membership required",
                )
        return AuthContext(user=user, org_id=org_id)
    membership = session.exec(
        select(OrgMembership).where(OrgMembership.user_id == user.id)
    ).first()
    if user.is_super_admin and membership:
        return AuthContext(user=user, org_id=membership.org_id)
    if user.is_super_admin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Organization not found for user",
        )
    if not membership:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Organization not found for user",
        )
    return AuthContext(user=user, org_id=membership.org_id)
