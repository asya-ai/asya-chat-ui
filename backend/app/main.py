import logging
import os

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.router import api_router


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
