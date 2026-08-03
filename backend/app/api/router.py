from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.auth import router as auth_router
from app.api.api_keys import router as api_keys_router
from app.api.agents import router as agents_router
from app.api.chats import router as chats_router
from app.api.deps import get_current_user
from app.api.models import router as models_router
from app.api.openai_compat import router as openai_compat_router
from app.api.orgs import router as orgs_router
from app.api.usage import router as usage_router
from app.core.config import settings
from app.models import User

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(api_keys_router)
api_router.include_router(agents_router)
api_router.include_router(chats_router)
api_router.include_router(models_router)
api_router.include_router(openai_compat_router)
api_router.include_router(orgs_router)
api_router.include_router(usage_router)


class AttachmentLimitsRead(BaseModel):
    max_files: int
    max_file_bytes: int
    max_total_bytes: int


@api_router.get("/healthz", tags=["health"])
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@api_router.get("/config/attachment-limits", response_model=AttachmentLimitsRead)
def get_attachment_limits(_: User = Depends(get_current_user)) -> AttachmentLimitsRead:
    return AttachmentLimitsRead(
        max_files=settings.attachments_max_files,
        max_file_bytes=settings.attachments_max_file_bytes,
        max_total_bytes=settings.attachments_max_total_bytes,
    )
