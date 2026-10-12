from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.api.deps import get_current_user, get_db
from app.models import User
from app.services.org_service import require_super_admin
from app.services.whitelabel import (
    clear_logo,
    get_whitelabel_settings,
    read_logo,
    store_logo,
    update_app_title,
)

public_router = APIRouter(prefix="/config", tags=["config"])
admin_router = APIRouter(prefix="/admin/whitelabel", tags=["admin"])


class BrandingRead(BaseModel):
    app_title: str | None = None
    has_logo: bool = False
    logo_updated_at: str | None = None


class WhitelabelRead(BaseModel):
    app_title: str | None = None
    has_logo: bool = False
    logo_content_type: str | None = None
    updated_at: datetime


class WhitelabelUpdate(BaseModel):
    app_title: str | None = Field(default=None)


def _to_branding(row) -> BrandingRead:
    return BrandingRead(
        app_title=row.app_title,
        has_logo=bool(row.logo_path),
        logo_updated_at=row.updated_at.isoformat() if row.logo_path else None,
    )


def _to_admin(row) -> WhitelabelRead:
    return WhitelabelRead(
        app_title=row.app_title,
        has_logo=bool(row.logo_path),
        logo_content_type=row.logo_content_type,
        updated_at=row.updated_at,
    )


@public_router.get("/branding", response_model=BrandingRead)
def get_branding(session: Session = Depends(get_db)) -> BrandingRead:
    return _to_branding(get_whitelabel_settings(session))


@public_router.get("/branding/logo")
def get_branding_logo(session: Session = Depends(get_db)) -> Response:
    payload = read_logo(session)
    if not payload:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Logo not set")
    data, content_type = payload
    row = get_whitelabel_settings(session)
    return Response(
        content=data,
        media_type=content_type,
        headers={
            "Cache-Control": "public, max-age=3600",
            "ETag": f'"{int(row.updated_at.timestamp())}"',
        },
    )


@admin_router.get("", response_model=WhitelabelRead)
def get_whitelabel(
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> WhitelabelRead:
    require_super_admin(current_user)
    return _to_admin(get_whitelabel_settings(session))


@admin_router.put("", response_model=WhitelabelRead)
def put_whitelabel(
    payload: WhitelabelUpdate,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> WhitelabelRead:
    require_super_admin(current_user)
    row = update_app_title(session, payload.app_title)
    return _to_admin(row)


@admin_router.post("/logo", response_model=WhitelabelRead)
async def upload_whitelabel_logo(
    file: UploadFile = File(...),
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> WhitelabelRead:
    require_super_admin(current_user)
    raw = await file.read()
    try:
        row = store_logo(
            session,
            data=raw,
            content_type=file.content_type or "application/octet-stream",
            file_name=file.filename or "logo",
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _to_admin(row)


@admin_router.delete("/logo", response_model=WhitelabelRead)
def delete_whitelabel_logo(
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> WhitelabelRead:
    require_super_admin(current_user)
    return _to_admin(clear_logo(session))
