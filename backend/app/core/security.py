from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import HTTPException, status
from fastapi.security import OAuth2PasswordBearer
import jwt
from jwt.exceptions import InvalidTokenError
import re

from passlib.context import CryptContext

from app.core.config import settings

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    return pwd_context.verify(password, hashed_password)


_PASSWORD_POLICY = re.compile(
    r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z0-9]).{10,}$"
)


def validate_password(password: str) -> bool:
    return bool(_PASSWORD_POLICY.match(password))


def create_access_token(
    subject: str,
    expires_minutes: Optional[int] = None,
    token_version: int = 0,
    *,
    expires_at: Optional[datetime] = None,
    expires_seconds: Optional[int] = None,
    extra_claims: Optional[dict[str, Any]] = None,
) -> str:
    if expires_at is not None:
        expire = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)
    elif expires_seconds is not None:
        expire = datetime.now(timezone.utc) + timedelta(seconds=expires_seconds)
    elif expires_minutes is not None:
        expire = datetime.now(timezone.utc) + timedelta(minutes=expires_minutes)
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.access_token_expire_minutes
        )
    to_encode: dict[str, Any] = {"sub": subject, "exp": expire, "ver": token_version}
    if extra_claims:
        to_encode.update(extra_claims)
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> str:
    payload = decode_access_token_claims(token)
    subject = payload.get("sub")
    if subject is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
        )
    return str(subject)


def decode_access_token_claims(
    token: str, *, verify_exp: bool = True, leeway_seconds: int = 0
) -> dict[str, Any]:
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.jwt_algorithm],
            options={"verify_exp": verify_exp},
            leeway=leeway_seconds,
        )
        return payload
    except (InvalidTokenError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
        ) from exc
