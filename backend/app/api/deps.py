from uuid import UUID

from fastapi import Depends, HTTPException, status
from sqlmodel import Session, select

from app.core.security import decode_access_token, oauth2_scheme
from app.db.session import get_session
from app.models import User


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
