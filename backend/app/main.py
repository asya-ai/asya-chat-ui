import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api import openai_compat_support as openai_compat_support

from app.api.router import api_router
from app.services.integrations.google_mcp_embed import google_mcp_embed
from app.services.integrations.graph_mcp_embed import graph_mcp_embed

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    force=True,
)
logging.getLogger().setLevel(LOG_LEVEL)
for logger_name in ("gunicorn", "gunicorn.error", "gunicorn.access"):
    logger = logging.getLogger(logger_name)
    logger.setLevel(LOG_LEVEL)
    logger.propagate = True



class _HealthzFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return "/healthz" not in message
for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
    logger = logging.getLogger(logger_name)
    logger.propagate = True
    logger.setLevel(LOG_LEVEL)
    if logger_name == "uvicorn.access":
        logger.addFilter(_HealthzFilter())


@asynccontextmanager
async def lifespan(_app: FastAPI):
    from app.db.session import SessionLocal
    from app.services.instance_providers import migrate_env_providers
    try:
        with SessionLocal() as session:
            migrated = migrate_env_providers(session)
            if migrated:
                logging.getLogger(__name__).info(
                    "Migrated instance provider credentials from env: %s",
                    ", ".join(migrated),
                )
            from app.services.instance_providers import reencrypt_plaintext_instance_secrets

            reencrypt_plaintext_instance_secrets(session)
            from app.services.instance_providers import reencrypt_plaintext_org_provider_secrets

            reencrypt_plaintext_org_provider_secrets(session)
    except Exception:
        logging.getLogger(__name__).exception(
            "Failed to migrate instance provider credentials from env"
        )

    try:
        from app.db.session import SessionLocal
        from app.services.integrations.google_mcp_embed import (
            sync_google_catalog_servers,
        )
        from app.services.integrations.graph_mcp_embed import (
            sync_microsoft_catalog_servers,
        )

        with SessionLocal() as session:
            sync_microsoft_catalog_servers(session)
            sync_google_catalog_servers(session)
    except Exception:
        logging.getLogger(__name__).exception(
            "Failed to sync Microsoft/Google catalog MCP URLs"
        )

    async with graph_mcp_embed.lifespan():
        async with google_mcp_embed.lifespan():
            yield


app = FastAPI(title="ChatUI API", lifespan=lifespan)
app.include_router(api_router)
app.mount("/internal/graph-mcp", graph_mcp_embed)
app.mount("/internal/google-mcp", google_mcp_embed)


@app.middleware("http")
async def refresh_access_token(request: Request, call_next):
    return await call_next(request)


def _redact_sensitive(value):
    if isinstance(value, dict):
        return {
            key: ("***" if key in {"password", "access_token"} else _redact_sensitive(val))
            for key, val in value.items()
        }
    if isinstance(value, list):
        return [_redact_sensitive(item) for item in value]
    return value


def _is_openai_compat_path(request: Request) -> bool:
    return "/v1/" in request.url.path or request.url.path.rstrip("/").endswith("/v1")


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    redacted_errors = []
    for err in exc.errors():
        redacted = dict(err)
        if "input" in redacted:
            redacted["input"] = _redact_sensitive(redacted["input"])
        redacted_errors.append(redacted)
    if _is_openai_compat_path(request):
        first = redacted_errors[0] if redacted_errors else {}
        loc = first.get("loc") or ()
        param = ".".join(str(part) for part in loc if part not in {"body"}) or None
        message = first.get("msg", "Invalid request")
        if param:
            message = f"{param}: {message}"
        return JSONResponse(
            status_code=400,
            content=openai_compat_support.openai_error_body(
                message,
                param=param,
            ),
        )
    return JSONResponse(status_code=422, content={"detail": redacted_errors})


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    if not _is_openai_compat_path(request):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    error_type = "invalid_request_error"
    code = None
    if exc.status_code in {401, 403}:
        error_type = "invalid_request_error"
        code = "permission_denied" if exc.status_code == 403 else "invalid_api_key"
    elif exc.status_code == 404:
        error_type = "invalid_request_error"
        code = "model_not_found"
    elif exc.status_code >= 500:
        error_type = "server_error"
    return JSONResponse(
        status_code=exc.status_code,
        content=openai_compat_support.openai_error_body(
            detail,
            error_type=error_type,
            code=code,
        ),
    )
