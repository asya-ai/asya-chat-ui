from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlmodel import Session, select

from app.api.deps import get_current_user, get_db
from app.models import ApiKey, User
from app.services.api_keys import generate_api_key, resolve_org_id_for_user

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


class ApiKeyCreateRequest(BaseModel):
    name: str


class ApiKeyRead(BaseModel):
    id: str
    name: str
    prefix: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class ApiKeyCreateResponse(ApiKeyRead):
    api_key: str


@router.get("", response_model=list[ApiKeyRead])
def list_api_keys(
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ApiKeyRead]:
    keys = session.exec(
        select(ApiKey)
        .where(ApiKey.user_id == current_user.id)
        .order_by(ApiKey.created_at.desc())
    ).all()
    return [
        ApiKeyRead(
            id=str(key.id),
            name=key.name,
            prefix=key.prefix,
            created_at=key.created_at,
            last_used_at=key.last_used_at,
            revoked_at=key.revoked_at,
        )
        for key in keys
    ]


@router.post("", response_model=ApiKeyCreateResponse)
def create_api_key(
    payload: ApiKeyCreateRequest,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
) -> ApiKeyCreateResponse:
    org_id = resolve_org_id_for_user(session, current_user, x_org_id)
    try:
        org_uuid = UUID(org_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid X-Org-Id",
        ) from exc
    raw_key, prefix, key_hash = generate_api_key()
    record = ApiKey(
        user_id=current_user.id,
        org_id=org_uuid,
        name=payload.name.strip() or "API key",
        prefix=prefix,
        key_hash=key_hash,
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return ApiKeyCreateResponse(
        id=str(record.id),
        name=record.name,
        prefix=record.prefix,
        created_at=record.created_at,
        last_used_at=record.last_used_at,
        revoked_at=record.revoked_at,
        api_key=raw_key,
    )


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_api_key(
    key_id: str,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    try:
        key_uuid = UUID(key_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid API key id"
        ) from exc
    record = session.exec(select(ApiKey).where(ApiKey.id == key_uuid)).first()
    if not record or record.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="API key not found"
        )
    if not record.revoked_at:
        record.revoked_at = datetime.now(timezone.utc)
        session.add(record)
        session.commit()
