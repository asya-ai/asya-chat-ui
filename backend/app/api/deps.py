from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from sqlmodel import Session, select

from app.core.security import decode_access_token, oauth2_scheme
from app.db.session import get_session
from app.models import OrgMembership, User
from app.services.api_keys import API_KEY_PREFIX, authenticate_api_key


def get_db() -> Session:
    yield from get_session()


def get_current_user(
    session: Session = Depends(get_db), token: str = Depends(oauth2_scheme)
) -> User:
    try:
        user_id = UUID(decode_access_token(token))
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
    return user


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
    user_id = UUID(decode_access_token(token))
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
    if x_org_id:
        try:
            org_id = UUID(x_org_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid X-Org-Id",
            ) from exc
        return AuthContext(user=user, org_id=org_id)
    if user.is_super_admin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Org-Id required for super admin",
        )
    membership = session.exec(
        select(OrgMembership).where(OrgMembership.user_id == user.id)
    ).first()
    if not membership:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Organization not found for user",
        )
    return AuthContext(user=user, org_id=membership.org_id)
