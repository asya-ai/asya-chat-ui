from fastapi import APIRouter

from app.api.auth import router as auth_router
from app.api.chats import router as chats_router
from app.api.models import router as models_router
from app.api.openai_compat import router as openai_compat_router
from app.api.orgs import router as orgs_router
from app.api.usage import router as usage_router

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(chats_router)
api_router.include_router(models_router)
api_router.include_router(openai_compat_router)
api_router.include_router(orgs_router)
api_router.include_router(usage_router)


@api_router.get("/healthz", tags=["health"])
def health_check() -> dict[str, str]:
    return {"status": "ok"}
