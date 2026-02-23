import logging
import os

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.router import api_router
from app.core.security import create_access_token, decode_access_token


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

app = FastAPI(title="ChatUI API")
app.include_router(api_router)


@app.middleware("http")
async def refresh_access_token(request: Request, call_next):
    response = await call_next(request)
    auth_header = request.headers.get("authorization")
    if not auth_header or not auth_header.lower().startswith("bearer "):
        return response
    token = auth_header.split(" ", 1)[1].strip()
    if not token:
        return response
    try:
        subject = decode_access_token(token)
    except Exception:
        return response
    refreshed = create_access_token(subject)
    response.headers["x-access-token"] = refreshed
    return response


def _redact_sensitive(value):
    if isinstance(value, dict):
        return {
            key: ("***" if key in {"password", "access_token"} else _redact_sensitive(val))
            for key, val in value.items()
        }
    if isinstance(value, list):
        return [_redact_sensitive(item) for item in value]
    return value


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
    return JSONResponse(status_code=422, content={"detail": redacted_errors})
