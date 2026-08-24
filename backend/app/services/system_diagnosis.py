from __future__ import annotations

import json
import os
import resource
import shutil
import smtplib
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Literal
from urllib.parse import unquote, urlparse

import psycopg
import redis
from pydantic import BaseModel, Field
from sqlmodel import Session, col, func, select

from app.core.config import settings
from app.core.exceptions import format_exception_detail
from app.db.session import engine
from app.models import (
    Chat,
    ChatGenerationTask,
    ChatMessageAttachment,
    GenerationStatus,
    Org,
    User,
)
from app.services.email_service import _smtp_port
from app.services.model_suggestions import (
    _anthropic_models,
    _azure_models,
    _gemini_models,
    _groq_models,
    _openai_models,
    _openrouter_models,
    _vertex_models,
)

EnvStatus = Literal["ok", "invalid", "missing"]


class EnvKeyDiagnosis(BaseModel):
    key: str
    category: str
    status: EnvStatus
    required: bool = False
    detail: str | None = None
    value: str | None = None


class DiskUsageInfo(BaseModel):
    label: str
    path: str
    total_bytes: int | None = None
    used_bytes: int | None = None
    free_bytes: int | None = None
    used_percent: float | None = None
    error: str | None = None


class DependencyCheck(BaseModel):
    name: str
    status: EnvStatus
    latency_ms: float | None = None
    detail: str | None = None


class ResourceMetric(BaseModel):
    name: str
    value: str
    detail: str | None = None
    status: Literal["ok", "invalid", "warning"] | None = None


class ProviderSnapshot(BaseModel):
    provider: str
    status: EnvStatus
    latency_ms: float | None = None
    detail: str | None = None


class McpServerCheck(BaseModel):
    id: str
    name: str
    transport: str | None = None
    status: EnvStatus
    latency_ms: float | None = None
    tools: int | None = None
    resources: int | None = None
    prompts: int | None = None
    detail: str | None = None


class DataVolumeMetric(BaseModel):
    name: str
    value: str
    detail: str | None = None


class WorkerLoadInfo(BaseModel):
    name: str
    active: int = 0
    reserved: int = 0
    concurrency: int | None = None
    load_percent: float | None = None
    status: Literal["ok", "invalid", "warning"] | None = None


class TaskWaitStats(BaseModel):
    queued_now: int = 0
    oldest_queue_wait_seconds: float | None = None
    avg_wait_seconds_1h: float | None = None
    p95_wait_seconds_1h: float | None = None
    max_wait_seconds_1h: float | None = None
    sample_size_1h: int = 0
    detail: str | None = None


class WorkersSnapshot(BaseModel):
    worker_count: int = 0
    active_tasks: int = 0
    reserved_tasks: int = 0
    queue_depth: int = 0
    total_concurrency: int | None = None
    load_percent: float | None = None
    workers: list[WorkerLoadInfo] = Field(default_factory=list)
    waits: TaskWaitStats = Field(default_factory=TaskWaitStats)
    status: Literal["ok", "invalid", "warning"] | None = None
    detail: str | None = None


class SystemDiagnosis(BaseModel):
    keys: list[EnvKeyDiagnosis]
    disks: list[DiskUsageInfo] = Field(default_factory=list)
    dependencies: list[DependencyCheck] = Field(default_factory=list)
    resources: list[ResourceMetric] = Field(default_factory=list)
    providers: list[ProviderSnapshot] = Field(default_factory=list)
    mcp_servers: list[McpServerCheck] = Field(default_factory=list)
    data_volume: list[DataVolumeMetric] = Field(default_factory=list)
    workers: WorkersSnapshot = Field(default_factory=WorkersSnapshot)
    summary: dict[str, int] = Field(
        default_factory=lambda: {"ok": 0, "invalid": 0, "missing": 0}
    )


@dataclass(frozen=True)
class _EnvSpec:
    key: str
    category: str
    attr: str | None = None
    required: bool = False
    secret: bool = False
    empty_means_missing: bool = True
    validate: Callable[[], str | None] | None = None
    # Documented compose/runtime default when the key is not on Settings.
    env_default: str | None = None


def _truncate(message: str, limit: int = 240) -> str:
    text = " ".join(str(message).split())
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3]}..."


@lru_cache(maxsize=1)
def _dotenv_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for candidate in (Path(".env"), Path("/app/.env"), Path(__file__).resolve().parents[3] / ".env"):
        if not candidate.is_file():
            continue
        try:
            for line in candidate.read_text(encoding="utf-8").splitlines():
                text = line.strip()
                if not text or text.startswith("#") or "=" not in text:
                    continue
                key, raw = text.split("=", 1)
                key = key.strip()
                if not key or key in values:
                    continue
                value = raw.strip().strip("'").strip('"')
                values[key] = value
        except Exception:
            continue
    return values


def _env_raw(key: str) -> str | None:
    if key in os.environ:
        return os.environ[key]
    dotenv = _dotenv_values()
    if key in dotenv:
        return dotenv[key]
    return None


def _is_entered(spec: _EnvSpec) -> bool:
    # Empty-default fields: settings value is the source of truth (covers .env + process env).
    if spec.empty_means_missing:
        if spec.attr:
            value = getattr(settings, spec.attr, None)
            if value is None:
                return False
            if isinstance(value, str):
                text = value.strip()
                if not text or text in {"...", "changeme", "replace-me", "todo"}:
                    return False
                if spec.key == "GEMINI_VERTEX_JSON" and text in {"{}", "null"}:
                    return False
                return True
            return True
        raw = _env_raw(spec.key)
        return bool(raw and str(raw).strip() and str(raw).strip() not in {"...", "changeme"})

    # Non-empty defaults / compose defaults: entered when present in process env or .env.
    raw = _env_raw(spec.key)
    if raw is None:
        return False
    text = str(raw).strip()
    if not text or text in {"...", "changeme", "replace-me", "todo"}:
        return False
    if spec.key == "GEMINI_VERTEX_JSON" and text in {"{}", "null"}:
        return False
    return True


def _settings_str(attr: str) -> str:
    value = getattr(settings, attr, None)
    if value is None:
        return ""
    return str(value).strip()


def _effective_raw(spec: _EnvSpec) -> str | None:
    """Resolved non-secret value: env → settings → documented default."""
    raw = _env_raw(spec.key)
    if raw is not None and str(raw).strip() != "":
        return str(raw).strip()
    if spec.attr:
        value = getattr(settings, spec.attr, None)
        if value is None:
            return spec.env_default
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, float):
            return f"{value:g}"
        text = str(value).strip()
        return text if text else spec.env_default
    return spec.env_default


def _presence_detail(spec: _EnvSpec) -> str | None:
    if spec.required:
        return "Required environment variable is not set"
    if spec.env_default is not None:
        return "Not set; using compose/runtime default"
    if not spec.empty_means_missing:
        return "Not set; using built-in default"
    return "Not set"


def _check_database() -> str | None:
    parsed = urlparse(settings.database_url)
    username = unquote(parsed.username or "")
    password = unquote(parsed.password or "")
    hostname = parsed.hostname or ""
    port = parsed.port or 5432
    database = (parsed.path or "").lstrip("/")
    if not username or not password or not hostname or not database:
        return "DATABASE_URL is missing username, password, host, or database"
    try:
        with psycopg.connect(
            host=hostname,
            port=port,
            dbname=database,
            user=username,
            password=password,
            connect_timeout=3,
        ) as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1")
        return None
    except Exception as exc:
        return _truncate(f"Database connection failed: {exc}")


def _check_path_writable(attr: str, *, must_exist: bool = False) -> Callable[[], str | None]:
    def _check() -> str | None:
        path_value = _settings_str(attr)
        if not path_value:
            return "Path is empty"
        path = Path(path_value)
        if must_exist and not path.exists():
            return f"Path does not exist: {path}"
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".diagnosis_write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return None
        except Exception as exc:
            return _truncate(f"Path not writable: {exc}")

    return _check


def _check_vertex_json() -> str | None:
    raw = _settings_str("gemini_vertex_json")
    if not raw or raw in {"{}", "null"}:
        return "Value is empty"
    try:
        parsed = json.loads(raw)
    except Exception as exc:
        return _truncate(f"Invalid JSON: {exc}")
    if not isinstance(parsed, dict):
        return "GEMINI_VERTEX_JSON must be a JSON object"
    _, error = _vertex_models()
    if error and "not set" in error.lower():
        # JSON present but project/location may still come from GOOGLE_VERTEX_*.
        return None
    if error:
        return _truncate(error)
    return None


def _check_url(attr: str) -> Callable[[], str | None]:
    def _check() -> str | None:
        raw = _settings_str(attr)
        if not raw:
            return "URL is empty"
        parsed = urlparse(raw)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return "URL must start with http:// or https://"
        return None

    return _check


def _check_positive_int(attr: str) -> Callable[[], str | None]:
    def _check() -> str | None:
        value = getattr(settings, attr, None)
        try:
            number = int(value)
        except Exception:
            return "Value is not an integer"
        if number <= 0:
            return "Value must be greater than 0"
        return None

    return _check


def _check_float(attr: str) -> Callable[[], str | None]:
    def _check() -> str | None:
        value = getattr(settings, attr, None)
        try:
            float(value)
            return None
        except Exception:
            return "Value is not a number"

    return _check


def _check_memory_limit() -> str | None:
    raw = _settings_str("exec_memory_limit")
    if not raw:
        return "Value is empty"
    if not any(ch.isdigit() for ch in raw):
        return "Memory limit must include a number (e.g. 512m)"
    return None


def _check_provider(list_fn: Callable[[], tuple[list, str | None]], label: str) -> Callable[[], str | None]:
    def _check() -> str | None:
        _, error = list_fn()
        if error:
            return _truncate(f"{label}: {error}")
        return None

    return _check


def _check_perplexity() -> str | None:
    if not settings.perplexity_api_key:
        return "PERPLEXITY_API_KEY is empty"
    url = "https://api.perplexity.ai/chat/completions"
    payload = json.dumps(
        {
            "model": settings.perplexity_model or "sonar-pro",
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {settings.perplexity_api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            response.read(256)
        return None
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8")
        except Exception:
            body = ""
        if exc.code in {401, 403}:
            return _truncate(f"Perplexity auth failed ({exc.code}): {body or exc.reason}")
        # Model/quota issues still mean the key was accepted.
        if exc.code in {400, 402, 429}:
            return None
        return _truncate(f"Perplexity error ({exc.code}): {body or exc.reason}")
    except Exception as exc:
        return _truncate(f"Perplexity request failed: {exc}")


def _check_scraper() -> str | None:
    base = _settings_str("scraper_url").rstrip("/")
    if not base:
        return "SCRAPER_URL is empty"
    url = f"{base}/healthz"
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            if response.status >= 400:
                return f"Scraper health check returned HTTP {response.status}"
        return None
    except Exception as exc:
        return _truncate(f"Scraper unreachable: {exc}")


def _check_smtp_host() -> str | None:
    if not settings.smtp_host.strip():
        return "SMTP_HOST is empty"
    port = _smtp_port()
    if not port:
        return "SMTP_PORT is missing or invalid"
    try:
        if port == 465:
            with smtplib.SMTP_SSL(settings.smtp_host, port, timeout=5) as smtp:
                smtp.ehlo()
                if settings.smtp_user:
                    smtp.login(settings.smtp_user, settings.smtp_password)
        else:
            with smtplib.SMTP(settings.smtp_host, port, timeout=5) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.ehlo()
                if settings.smtp_user:
                    smtp.login(settings.smtp_user, settings.smtp_password)
        return None
    except Exception as exc:
        return _truncate(f"SMTP connection failed: {exc}")


def _check_smtp_port_value() -> str | None:
    if not settings.smtp_port.strip():
        return "SMTP_PORT is empty"
    if _smtp_port() is None:
        return "SMTP_PORT must be an integer"
    return None


def _check_smtp_sender() -> str | None:
    if not (settings.smtp_email or settings.smtp_user):
        return "Set SMTP_EMAIL or SMTP_USER"
    return None


def _check_jwt_secret() -> str | None:
    secret = _settings_str("secret_key")
    if not secret:
        return "JWT_SECRET is empty"
    if len(secret) < 16:
        return "JWT_SECRET should be at least 16 characters"
    return None


def _check_super_admin_emails() -> str | None:
    raw = _settings_str("super_admin_emails")
    if not raw:
        return "Value is empty"
    emails = [part.strip() for part in raw.split(",") if part.strip()]
    if not emails:
        return "No emails found"
    invalid = [email for email in emails if "@" not in email]
    if invalid:
        return _truncate(f"Invalid email entries: {', '.join(invalid)}")
    return None


def _build_specs() -> list[_EnvSpec]:
    return [
        _EnvSpec(
            "APP_ENV",
            "core",
            "app_env",
            empty_means_missing=False,
        ),
        _EnvSpec(
            "JWT_SECRET",
            "core",
            "secret_key",
            required=True,
            secret=True,
            validate=_check_jwt_secret,
        ),
        _EnvSpec(
            "ACCESS_TOKEN_EXPIRE_MINUTES",
            "core",
            "access_token_expire_minutes",
            empty_means_missing=False,
            validate=_check_positive_int("access_token_expire_minutes"),
        ),
        _EnvSpec(
            "SUPER_ADMIN_EMAILS",
            "core",
            "super_admin_emails",
            validate=_check_super_admin_emails,
        ),
        _EnvSpec(
            "PUBLIC_API_BASE_URL",
            "core",
            "public_api_base_url",
            validate=_check_url("public_api_base_url"),
        ),
        _EnvSpec(
            "DATABASE_URL",
            "database",
            "database_url",
            required=True,
            secret=True,
            empty_means_missing=False,
            validate=_check_database,
        ),
        _EnvSpec(
            "OPENAI_API_KEY",
            "providers",
            "openai_api_key",
            secret=True,
            validate=_check_provider(_openai_models, "OpenAI"),
        ),
        _EnvSpec(
            "OPENAI_BASE_URL",
            "providers",
            "openai_base_url",
            empty_means_missing=False,
            validate=_check_url("openai_base_url"),
        ),
        _EnvSpec(
            "OPENAI_PROMPT_CACHE_RETENTION",
            "providers",
            "openai_prompt_cache_retention",
        ),
        _EnvSpec(
            "AZURE_OPENAI_API_KEY",
            "providers",
            "azure_openai_api_key",
            secret=True,
            validate=_check_provider(_azure_models, "Azure OpenAI"),
        ),
        _EnvSpec(
            "AZURE_OPENAI_ENDPOINT",
            "providers",
            "azure_openai_endpoint",
            validate=_check_url("azure_openai_endpoint"),
        ),
        _EnvSpec(
            "AZURE_OPENAI_API_VERSION",
            "providers",
            "azure_openai_api_version",
            empty_means_missing=False,
        ),
        _EnvSpec(
            "GEMINI_API_KEY",
            "providers",
            "gemini_api_key",
            secret=True,
            validate=_check_provider(_gemini_models, "Gemini"),
        ),
        _EnvSpec(
            "GEMINI_BASE_URL",
            "providers",
            "gemini_base_url",
            empty_means_missing=False,
            validate=_check_url("gemini_base_url"),
        ),
        _EnvSpec(
            "GEMINI_VERTEX_JSON",
            "providers",
            "gemini_vertex_json",
            secret=True,
            validate=_check_vertex_json,
        ),
        _EnvSpec(
            "GEMINI_CACHED_CONTENT_ENABLED",
            "providers",
            "gemini_cached_content_enabled",
            empty_means_missing=False,
        ),
        _EnvSpec(
            "GEMINI_CACHED_CONTENT_TTL_SECONDS",
            "providers",
            "gemini_cached_content_ttl_seconds",
            empty_means_missing=False,
            validate=_check_positive_int("gemini_cached_content_ttl_seconds"),
        ),
        _EnvSpec(
            "GEMINI_CACHED_CONTENT_MAX_ITEMS",
            "providers",
            "gemini_cached_content_max_items",
            empty_means_missing=False,
            validate=_check_positive_int("gemini_cached_content_max_items"),
        ),
        _EnvSpec(
            "GOOGLE_VERTEX_PROJECT",
            "providers",
            "google_vertex_project",
        ),
        _EnvSpec(
            "GOOGLE_VERTEX_LOCATION",
            "providers",
            "google_vertex_location",
            empty_means_missing=False,
            env_default="global",
        ),
        _EnvSpec(
            "OPENROUTER_API_KEY",
            "providers",
            "openrouter_api_key",
            secret=True,
            validate=_check_provider(_openrouter_models, "OpenRouter"),
        ),
        _EnvSpec(
            "GROQ_API_KEY",
            "providers",
            "groq_api_key",
            secret=True,
            validate=_check_provider(_groq_models, "Groq"),
        ),
        _EnvSpec(
            "GROQ_BASE_URL",
            "providers",
            "groq_base_url",
            empty_means_missing=False,
            validate=_check_url("groq_base_url"),
        ),
        _EnvSpec(
            "ANTHROPIC_API_KEY",
            "providers",
            "anthropic_api_key",
            secret=True,
            validate=_check_provider(_anthropic_models, "Anthropic"),
        ),
        _EnvSpec(
            "ANTHROPIC_BASE_URL",
            "providers",
            "anthropic_base_url",
            empty_means_missing=False,
            validate=_check_url("anthropic_base_url"),
        ),
        _EnvSpec(
            "PERPLEXITY_API_KEY",
            "providers",
            "perplexity_api_key",
            secret=True,
            validate=_check_perplexity,
        ),
        _EnvSpec(
            "PERPLEXITY_MODEL",
            "providers",
            "perplexity_model",
            empty_means_missing=False,
        ),
        _EnvSpec(
            "SMTP_HOST",
            "smtp",
            "smtp_host",
            validate=_check_smtp_host,
        ),
        _EnvSpec(
            "SMTP_PORT",
            "smtp",
            "smtp_port",
            validate=_check_smtp_port_value,
        ),
        _EnvSpec(
            "SMTP_USER",
            "smtp",
            "smtp_user",
            secret=True,
        ),
        _EnvSpec(
            "SMTP_PASSWORD",
            "smtp",
            "smtp_password",
            secret=True,
        ),
        _EnvSpec(
            "SMTP_EMAIL",
            "smtp",
            "smtp_email",
            validate=_check_smtp_sender,
        ),
        _EnvSpec(
            "INVITE_EXPIRE_HOURS",
            "smtp",
            "invite_expire_hours",
            empty_means_missing=False,
            validate=_check_positive_int("invite_expire_hours"),
        ),
        _EnvSpec(
            "PASSWORD_RESET_EXPIRE_HOURS",
            "smtp",
            "password_reset_expire_hours",
            empty_means_missing=False,
            validate=_check_positive_int("password_reset_expire_hours"),
        ),
        _EnvSpec(
            "FILES_BASE_DIR",
            "files",
            "files_base_dir",
            empty_means_missing=False,
            env_default="/data/files",
            validate=_check_path_writable("files_base_dir"),
        ),
        _EnvSpec(
            "ATTACHMENTS_MAX_FILES",
            "files",
            "attachments_max_files",
            empty_means_missing=False,
            validate=_check_positive_int("attachments_max_files"),
        ),
        _EnvSpec(
            "ATTACHMENTS_MAX_FILE_BYTES",
            "files",
            "attachments_max_file_bytes",
            empty_means_missing=False,
            validate=_check_positive_int("attachments_max_file_bytes"),
        ),
        _EnvSpec(
            "ATTACHMENTS_MAX_TOTAL_BYTES",
            "files",
            "attachments_max_total_bytes",
            empty_means_missing=False,
            validate=_check_positive_int("attachments_max_total_bytes"),
        ),
        _EnvSpec(
            "ATTACHMENT_URL_EXPIRE_MINUTES",
            "files",
            "attachment_url_expire_minutes",
            empty_means_missing=False,
            validate=_check_positive_int("attachment_url_expire_minutes"),
        ),
        _EnvSpec(
            "EXEC_HOST_FILES_DIR",
            "exec",
            "exec_host_files_dir",
            env_default="/data/files",
            validate=_check_path_writable("exec_host_files_dir"),
        ),
        _EnvSpec(
            "EXEC_DOCKER_IMAGE",
            "exec",
            "exec_docker_image",
            empty_means_missing=False,
        ),
        _EnvSpec(
            "EXEC_TIMEOUT_SECONDS",
            "exec",
            "exec_timeout_seconds",
            empty_means_missing=False,
            validate=_check_positive_int("exec_timeout_seconds"),
        ),
        _EnvSpec(
            "EXEC_MAX_OUTPUT_BYTES",
            "exec",
            "exec_max_output_bytes",
            empty_means_missing=False,
            validate=_check_positive_int("exec_max_output_bytes"),
        ),
        _EnvSpec(
            "EXEC_MAX_OUTPUT_FILE_BYTES",
            "exec",
            "exec_max_output_file_bytes",
            empty_means_missing=False,
            validate=_check_positive_int("exec_max_output_file_bytes"),
        ),
        _EnvSpec(
            "EXEC_MAX_CODE_CHARS",
            "exec",
            "exec_max_code_chars",
            empty_means_missing=False,
            validate=_check_positive_int("exec_max_code_chars"),
        ),
        _EnvSpec(
            "EXEC_CPU_LIMIT",
            "exec",
            "exec_cpu_limit",
            empty_means_missing=False,
            validate=_check_float("exec_cpu_limit"),
        ),
        _EnvSpec(
            "EXEC_MEMORY_LIMIT",
            "exec",
            "exec_memory_limit",
            empty_means_missing=False,
            validate=_check_memory_limit,
        ),
        _EnvSpec(
            "EXEC_PIDS_LIMIT",
            "exec",
            "exec_pids_limit",
            empty_means_missing=False,
            validate=_check_positive_int("exec_pids_limit"),
        ),
        _EnvSpec(
            "EXEC_TMPFS_SIZE",
            "exec",
            "exec_tmpfs_size",
            empty_means_missing=False,
        ),
        _EnvSpec(
            "EXEC_ULIMIT_NOFILE",
            "exec",
            "exec_ulimit_nofile",
            empty_means_missing=False,
            validate=_check_positive_int("exec_ulimit_nofile"),
        ),
        _EnvSpec(
            "EXEC_ULIMIT_FSIZE_BYTES",
            "exec",
            "exec_ulimit_fsize_bytes",
            empty_means_missing=False,
            validate=_check_positive_int("exec_ulimit_fsize_bytes"),
        ),
        _EnvSpec(
            "EXEC_ULIMIT_NPROC",
            "exec",
            "exec_ulimit_nproc",
            empty_means_missing=False,
            validate=_check_positive_int("exec_ulimit_nproc"),
        ),
        _EnvSpec(
            "SCRAPER_URL",
            "web",
            "scraper_url",
            empty_means_missing=False,
            validate=_check_scraper,
        ),
        _EnvSpec(
            "WEB_SEARCH_LIMIT",
            "web",
            "web_search_limit",
            empty_means_missing=False,
            validate=_check_positive_int("web_search_limit"),
        ),
        _EnvSpec(
            "SCRAPE_TEXT_LIMIT",
            "web",
            "scrape_text_limit",
            empty_means_missing=False,
            validate=_check_positive_int("scrape_text_limit"),
        ),
        _EnvSpec(
            "SCRAPE_PARALLEL_MAX",
            "web",
            "scrape_parallel_max",
            empty_means_missing=False,
            validate=_check_positive_int("scrape_parallel_max"),
        ),
        _EnvSpec(
            "AGENT_EMBEDDING_MODEL",
            "agents",
            "agent_embedding_model",
            empty_means_missing=False,
            env_default="BAAI/bge-m3",
            validate=_check_embedding_model_cached,
        ),
        _EnvSpec(
            "AGENT_EMBEDDING_BATCH_SIZE",
            "agents",
            "agent_embedding_batch_size",
            empty_means_missing=False,
            validate=_check_positive_int("agent_embedding_batch_size"),
        ),
        # Injected by docker-compose (not Settings fields).
        _EnvSpec(
            "CELERY_BROKER_URL",
            "runtime",
            None,
            empty_means_missing=False,
            env_default="redis://redis:6379/0",
        ),
        _EnvSpec(
            "CELERY_RESULT_BACKEND",
            "runtime",
            None,
            empty_means_missing=False,
            env_default="redis://redis:6379/0",
        ),
        _EnvSpec(
            "DOCKER_HOST",
            "runtime",
            None,
            empty_means_missing=False,
            env_default="tcp://dind:2375",
        ),
        _EnvSpec(
            "PROJECT_ROOT_DIR",
            "runtime",
            None,
            empty_means_missing=True,
        ),
        _EnvSpec(
            "HF_HOME",
            "runtime",
            None,
            empty_means_missing=False,
            env_default="/opt/hf-cache",
        ),
    ]


def _check_embedding_model_cached() -> str | None:
    model = (settings.agent_embedding_model or "").strip()
    if not model:
        return "AGENT_EMBEDDING_MODEL is empty"
    slug = "models--" + model.replace("/", "--")
    cache_roots = [
        Path(os.environ.get("SENTENCE_TRANSFORMERS_HOME", "") or ""),
        Path(os.environ.get("TRANSFORMERS_CACHE", "") or ""),
        Path(os.environ.get("HF_HOME", "") or ""),
        Path("/opt/hf-cache"),
        Path.home() / ".cache" / "huggingface",
    ]
    for root in cache_roots:
        if not root or str(root) in {"", "."}:
            continue
        candidates = [root / slug, root / "hub" / slug]
        for candidate in candidates:
            if candidate.exists():
                return None
        hub = root / "hub" if (root / "hub").exists() else root
        if hub.exists():
            try:
                if any(hub.glob(f"**/{slug}")):
                    return None
            except Exception:
                pass
    return (
        f"Cached weights not found for {model}. "
        "Rebuild the backend image with matching AGENT_EMBEDDING_MODEL."
    )


def _public_value(spec: _EnvSpec) -> str | None:
    """Effective setting value for non-secret keys (configured, env, or default)."""
    if spec.secret:
        return None
    return _effective_raw(spec)


def _with_public_value(spec: _EnvSpec, result: EnvKeyDiagnosis) -> EnvKeyDiagnosis:
    return result.model_copy(update={"value": _public_value(spec)})


def _diagnose_one(spec: _EnvSpec) -> EnvKeyDiagnosis:
    return _with_public_value(spec, _diagnose_one_body(spec))


def _diagnose_one_body(spec: _EnvSpec) -> EnvKeyDiagnosis:
    entered = _is_entered(spec)

    # Vertex: project is required to use the provider; location defaults to "global".
    if spec.key in {"GOOGLE_VERTEX_PROJECT", "GOOGLE_VERTEX_LOCATION"}:
        project_set = bool(_settings_str("google_vertex_project") or _env_raw("GOOGLE_VERTEX_PROJECT"))
        location_explicit = bool(
            (_env_raw("GOOGLE_VERTEX_LOCATION") or "").strip()
            and (_env_raw("GOOGLE_VERTEX_LOCATION") or "").strip() != "global"
        )
        vertex_json_set = bool(
            _settings_str("gemini_vertex_json")
            and _settings_str("gemini_vertex_json") not in {"", "{}"}
        )
        if not entered:
            return EnvKeyDiagnosis(
                key=spec.key,
                category=spec.category,
                status="missing",
                required=spec.required,
                detail=_presence_detail(spec),
            )
        if project_set:
            _, error = _vertex_models()
            if error:
                return EnvKeyDiagnosis(
                    key=spec.key,
                    category=spec.category,
                    status="invalid",
                    required=spec.required,
                    detail=_truncate(error or "Vertex credentials invalid"),
                )
            return EnvKeyDiagnosis(
                key=spec.key,
                category=spec.category,
                status="ok",
                required=spec.required,
                detail=(
                    "Vertex project/location accepted"
                    if location_explicit
                    else "Vertex project accepted (location defaults to global)"
                ),
            )
        if vertex_json_set:
            return EnvKeyDiagnosis(
                key=spec.key,
                category=spec.category,
                status="ok",
                required=spec.required,
                detail="Provided; GEMINI_VERTEX_JSON may also supply project/location",
            )
        return EnvKeyDiagnosis(
            key=spec.key,
            category=spec.category,
            status="invalid",
            required=spec.required,
            detail="GOOGLE_VERTEX_PROJECT is required (location defaults to global)",
        )

    # Azure key/endpoint are a pair.
    if spec.key == "AZURE_OPENAI_API_KEY" and entered:
        if not _settings_str("azure_openai_endpoint"):
            return EnvKeyDiagnosis(
                key=spec.key,
                category=spec.category,
                status="invalid",
                required=spec.required,
                detail="AZURE_OPENAI_ENDPOINT is also required",
            )
    if spec.key == "AZURE_OPENAI_ENDPOINT" and entered:
        if not _settings_str("azure_openai_api_key"):
            return EnvKeyDiagnosis(
                key=spec.key,
                category=spec.category,
                status="invalid",
                required=spec.required,
                detail="AZURE_OPENAI_API_KEY is also required",
            )

    if not entered:
        if spec.key == "GEMINI_VERTEX_JSON":
            raw = _settings_str("gemini_vertex_json")
            if raw in {"", "{}"}:
                return EnvKeyDiagnosis(
                    key=spec.key,
                    category=spec.category,
                    status="missing",
                    required=False,
                    detail="Not set",
                )
        return EnvKeyDiagnosis(
            key=spec.key,
            category=spec.category,
            status="missing",
            required=spec.required,
            detail=_presence_detail(spec),
        )

    if spec.validate is None:
        return EnvKeyDiagnosis(
            key=spec.key,
            category=spec.category,
            status="ok",
            required=spec.required,
            detail="Set",
        )

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            error = executor.submit(spec.validate).result(timeout=20)
    except TimeoutError:
        error = "Check timed out"
    except Exception as exc:
        error = _truncate(f"Check failed: {exc}")

    if error:
        return EnvKeyDiagnosis(
            key=spec.key,
            category=spec.category,
            status="invalid",
            required=spec.required,
            detail=error,
        )
    return EnvKeyDiagnosis(
        key=spec.key,
        category=spec.category,
        status="ok",
        required=spec.required,
        detail="Working",
    )


def _probe_disk(label: str, path_value: str) -> DiskUsageInfo:
    raw = (path_value or "").strip() or "/"
    path = Path(raw)
    try:
        # Prefer an existing ancestor so disk_usage works before mkdir.
        probe = path
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        if not probe.exists():
            probe = Path("/")
        usage = shutil.disk_usage(probe)
        used = usage.total - usage.free
        percent = (used / usage.total * 100.0) if usage.total else 0.0
        return DiskUsageInfo(
            label=label,
            path=str(path),
            total_bytes=usage.total,
            used_bytes=used,
            free_bytes=usage.free,
            used_percent=round(percent, 1),
        )
    except Exception as exc:
        return DiskUsageInfo(
            label=label,
            path=str(path),
            error=_truncate(f"Unable to read disk usage: {exc}"),
        )


def _collect_disk_usage() -> list[DiskUsageInfo]:
    targets: list[tuple[str, str]] = [
        ("System root", "/"),
        ("FILES_BASE_DIR", settings.files_base_dir or "/data/files"),
    ]
    exec_host = (settings.exec_host_files_dir or "").strip()
    if exec_host:
        targets.append(("EXEC_HOST_FILES_DIR", exec_host))

    disks: list[DiskUsageInfo] = []
    seen_paths: set[str] = set()
    for label, path_value in targets:
        resolved = str(Path(path_value or "/"))
        if resolved in seen_paths:
            continue
        seen_paths.add(resolved)
        disks.append(_probe_disk(label, path_value))
    return disks


def _timed_ms(fn: Callable[[], Any]) -> tuple[float, Any, str | None]:
    started = time.perf_counter()
    try:
        result = fn()
        return (time.perf_counter() - started) * 1000.0, result, None
    except Exception as exc:
        return (
            (time.perf_counter() - started) * 1000.0,
            None,
            _truncate(format_exception_detail(exc, limit=400), limit=320),
        )


def _db_connect_kwargs() -> dict[str, Any]:
    parsed = urlparse(settings.database_url)
    return {
        "host": parsed.hostname or "",
        "port": parsed.port or 5432,
        "dbname": (parsed.path or "").lstrip("/"),
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "connect_timeout": 3,
    }


def _check_postgres_dependency() -> DependencyCheck:
    def _probe() -> str:
        with psycopg.connect(**_db_connect_kwargs()) as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.execute("SELECT count(*) FROM pg_stat_activity")
                count = cursor.fetchone()[0]
        return f"{count} DB connections"

    ms, detail, error = _timed_ms(_probe)
    if error:
        return DependencyCheck(
            name="PostgreSQL",
            status="invalid",
            latency_ms=round(ms, 1),
            detail=error,
        )
    return DependencyCheck(
        name="PostgreSQL",
        status="ok",
        latency_ms=round(ms, 1),
        detail=detail if isinstance(detail, str) else "Connected",
    )


def _redis_url(kind: str) -> str:
    if kind == "broker":
        return os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
    return os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")


def _check_redis_dependency(name: str, url: str) -> DependencyCheck:
    def _probe() -> str:
        client = redis.Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)
        try:
            client.ping()
            info = client.info(section="server")
            version = info.get("redis_version", "?")
            return f"Redis {version}"
        finally:
            client.close()

    ms, detail, error = _timed_ms(_probe)
    if error:
        return DependencyCheck(
            name=name,
            status="invalid",
            latency_ms=round(ms, 1),
            detail=error,
        )
    return DependencyCheck(
        name=name,
        status="ok",
        latency_ms=round(ms, 1),
        detail=detail if isinstance(detail, str) else "Connected",
    )


def _check_scraper_dependency() -> DependencyCheck:
    base = (settings.scraper_url or "").rstrip("/")
    if not base:
        return DependencyCheck(
            name="Scraper",
            status="missing",
            detail="SCRAPER_URL not set",
        )

    def _probe() -> str:
        request = urllib.request.Request(f"{base}/healthz", method="GET")
        with urllib.request.urlopen(request, timeout=3) as response:
            if response.status >= 400:
                raise RuntimeError(f"HTTP {response.status}")
            return f"healthz HTTP {response.status}"

    ms, detail, error = _timed_ms(_probe)
    if error:
        return DependencyCheck(
            name="Scraper",
            status="invalid",
            latency_ms=round(ms, 1),
            detail=error,
        )
    return DependencyCheck(
        name="Scraper",
        status="ok",
        latency_ms=round(ms, 1),
        detail=detail if isinstance(detail, str) else "Healthy",
    )


def _check_docker_dependency() -> DependencyCheck:
    image = settings.exec_docker_image or "chatui-python-exec:latest"

    def _probe() -> str:
        import docker

        client = docker.from_env(timeout=3)
        try:
            client.ping()
            try:
                client.images.get(image)
                return f"Daemon OK; image present ({image})"
            except Exception:
                return f"Daemon OK; image missing ({image})"
        finally:
            try:
                client.close()
            except Exception:
                pass

    ms, detail, error = _timed_ms(_probe)
    if error:
        return DependencyCheck(
            name="Docker",
            status="invalid",
            latency_ms=round(ms, 1),
            detail=error,
        )
    text = detail if isinstance(detail, str) else "OK"
    status: EnvStatus = "ok" if "image present" in text else "invalid"
    return DependencyCheck(
        name="Docker",
        status=status,
        latency_ms=round(ms, 1),
        detail=text,
    )


def _collect_dependencies() -> list[DependencyCheck]:
    broker = _redis_url("broker")
    backend = _redis_url("backend")
    checks = [
        _check_postgres_dependency(),
        _check_redis_dependency("Redis (Celery broker)", broker),
    ]
    if backend != broker:
        checks.append(_check_redis_dependency("Redis (Celery result)", backend))
    checks.append(_check_scraper_dependency())
    checks.append(_check_docker_dependency())
    return checks


def _read_meminfo() -> tuple[int | None, int | None]:
    path = Path("/proc/meminfo")
    if not path.exists():
        return None, None
    total = available = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("MemTotal:"):
            total = int(line.split()[1]) * 1024
        elif line.startswith("MemAvailable:"):
            available = int(line.split()[1]) * 1024
    return total, available


def _format_bytes(value: int | None) -> str:
    if value is None:
        return "—"
    units = ["B", "KB", "MB", "GB", "TB"]
    amount = float(max(0, value))
    unit = 0
    while amount >= 1024 and unit < len(units) - 1:
        amount /= 1024
        unit += 1
    digits = 0 if amount >= 100 or unit == 0 else 1 if amount >= 10 else 2
    return f"{amount:.{digits}f} {units[unit]}"


def _collect_resources() -> list[ResourceMetric]:
    metrics: list[ResourceMetric] = []

    total, available = _read_meminfo()
    if total is not None and available is not None:
        used = total - available
        percent = (used / total * 100.0) if total else 0.0
        status: Literal["ok", "invalid", "warning"] | None = "ok"
        if percent >= 90:
            status = "invalid"
        elif percent >= 75:
            status = "warning"
        metrics.append(
            ResourceMetric(
                name="Memory",
                value=f"{_format_bytes(used)} / {_format_bytes(total)} ({percent:.1f}%)",
                detail=f"Available {_format_bytes(available)}",
                status=status,
            )
        )
    else:
        try:
            import sys

            usage = resource.getrusage(resource.RUSAGE_SELF)
            rss = int(usage.ru_maxrss)
            # Linux reports KB; macOS reports bytes.
            if sys.platform != "darwin":
                rss *= 1024
            metrics.append(
                ResourceMetric(
                    name="Process RSS",
                    value=_format_bytes(rss),
                    detail="Host /proc/meminfo unavailable",
                )
            )
        except Exception as exc:
            metrics.append(
                ResourceMetric(
                    name="Memory",
                    value="—",
                    detail=_truncate(str(exc)),
                    status="invalid",
                )
            )

    try:
        load1, load5, load15 = os.getloadavg()
        metrics.append(
            ResourceMetric(
                name="CPU load",
                value=f"{load1:.2f} / {load5:.2f} / {load15:.2f}",
                detail="1m / 5m / 15m",
            )
        )
    except Exception as exc:
        metrics.append(
            ResourceMetric(
                name="CPU load",
                value="—",
                detail=_truncate(str(exc)),
                status="invalid",
            )
        )

    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        fd_dir = Path("/proc/self/fd")
        open_fds = len(list(fd_dir.iterdir())) if fd_dir.exists() else None
        if open_fds is not None:
            metrics.append(
                ResourceMetric(
                    name="Open file descriptors",
                    value=f"{open_fds} / {soft}",
                    detail=f"Hard limit {hard}",
                    status="warning" if soft and open_fds / soft >= 0.75 else "ok",
                )
            )
        else:
            metrics.append(
                ResourceMetric(
                    name="FD limit",
                    value=str(soft),
                    detail=f"Hard limit {hard}",
                )
            )
    except Exception as exc:
        metrics.append(
            ResourceMetric(
                name="File descriptors",
                value="—",
                detail=_truncate(str(exc)),
                status="invalid",
            )
        )

    try:
        with psycopg.connect(**_db_connect_kwargs()) as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT count(*) FROM pg_stat_activity")
                count = int(cursor.fetchone()[0])
                cursor.execute("SHOW max_connections")
                max_conn = int(cursor.fetchone()[0])
        ratio = count / max_conn if max_conn else 0
        metrics.append(
            ResourceMetric(
                name="DB connections",
                value=f"{count} / {max_conn}",
                detail="pg_stat_activity / max_connections",
                status="invalid" if ratio >= 0.9 else "warning" if ratio >= 0.75 else "ok",
            )
        )
    except Exception as exc:
        metrics.append(
            ResourceMetric(
                name="DB connections",
                value="—",
                detail=_truncate(str(exc)),
                status="invalid",
            )
        )

    return metrics


def _percentile(sorted_values: list[float], pct: float) -> float | None:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (len(sorted_values) - 1) * pct
    low = int(rank)
    high = min(low + 1, len(sorted_values) - 1)
    weight = rank - low
    return sorted_values[low] * (1 - weight) + sorted_values[high] * weight


def _collect_task_waits() -> TaskWaitStats:
    now = datetime.utcnow()
    cutoff = now - timedelta(hours=1)
    try:
        with Session(engine) as session:
            queued_rows = session.exec(
                select(ChatGenerationTask.created_at).where(
                    ChatGenerationTask.status == GenerationStatus.queued
                )
            ).all()
            queued_now = len(queued_rows)
            oldest_queue_wait = None
            if queued_rows:
                oldest = min(queued_rows)
                oldest_queue_wait = max(0.0, (now - oldest).total_seconds())

            started_rows = session.exec(
                select(ChatGenerationTask.created_at, ChatGenerationTask.started_at).where(
                    col(ChatGenerationTask.started_at).is_not(None),
                    ChatGenerationTask.started_at >= cutoff,
                )
            ).all()
        waits: list[float] = []
        for created, started in started_rows:
            if created is None or started is None:
                continue
            delta = (started - created).total_seconds()
            if delta >= 0:
                waits.append(delta)
        waits.sort()
        avg = (sum(waits) / len(waits)) if waits else None
        return TaskWaitStats(
            queued_now=queued_now,
            oldest_queue_wait_seconds=round(oldest_queue_wait, 1)
            if oldest_queue_wait is not None
            else None,
            avg_wait_seconds_1h=round(avg, 1) if avg is not None else None,
            p95_wait_seconds_1h=round(_percentile(waits, 0.95), 1)
            if waits
            else None,
            max_wait_seconds_1h=round(waits[-1], 1) if waits else None,
            sample_size_1h=len(waits),
            detail="Wait = started_at − created_at for generation tasks",
        )
    except Exception as exc:
        return TaskWaitStats(detail=_truncate(str(exc)))


def _collect_workers() -> WorkersSnapshot:
    waits = _collect_task_waits()
    queue_depth = 0
    try:
        client = redis.Redis.from_url(
            _redis_url("broker"), socket_connect_timeout=2, socket_timeout=2
        )
        try:
            queue_depth = int(client.llen("celery") or 0)
            queue_depth += int(client.llen("generation") or 0)
            queue_depth += int(client.llen("embedding") or 0)
        finally:
            client.close()
    except Exception:
        queue_depth = waits.queued_now

    try:
        from app.workers.celery_app import celery_app

        inspect = celery_app.control.inspect(timeout=2.0)
        ping = inspect.ping() or {}
        active = inspect.active() or {}
        reserved = inspect.reserved() or {}
        stats = inspect.stats() or {}

        worker_names = sorted(set(ping) | set(active) | set(reserved) | set(stats))
        workers: list[WorkerLoadInfo] = []
        total_concurrency = 0
        concurrency_known = True
        active_tasks = 0
        reserved_tasks = 0

        for name in worker_names:
            active_count = len(active.get(name) or [])
            reserved_count = len(reserved.get(name) or [])
            active_tasks += active_count
            reserved_tasks += reserved_count
            concurrency = None
            worker_stats = stats.get(name) or {}
            pool = worker_stats.get("pool") if isinstance(worker_stats, dict) else None
            if isinstance(pool, dict):
                raw_conc = pool.get("max-concurrency")
                try:
                    concurrency = int(raw_conc)
                except Exception:
                    concurrency = None
            if concurrency and concurrency > 0:
                total_concurrency += concurrency
                load = ((active_count + reserved_count) / concurrency) * 100.0
            else:
                concurrency_known = False
                load = None
            status: Literal["ok", "invalid", "warning"] | None = "ok"
            if load is not None:
                if load >= 95:
                    status = "invalid"
                elif load >= 75:
                    status = "warning"
            workers.append(
                WorkerLoadInfo(
                    name=name,
                    active=active_count,
                    reserved=reserved_count,
                    concurrency=concurrency,
                    load_percent=round(load, 1) if load is not None else None,
                    status=status,
                )
            )

        load_percent = None
        if concurrency_known and total_concurrency > 0:
            load_percent = round(
                ((active_tasks + reserved_tasks) / total_concurrency) * 100.0, 1
            )

        status: Literal["ok", "invalid", "warning"] | None = "ok"
        if not worker_names:
            status = "invalid"
        elif load_percent is not None:
            if load_percent >= 95 or queue_depth >= 100:
                status = "invalid"
            elif load_percent >= 75 or queue_depth >= 50:
                status = "warning"
        elif queue_depth >= 100 or waits.queued_now >= 100:
            status = "invalid"
        elif queue_depth >= 50 or waits.queued_now >= 50:
            status = "warning"

        if waits.oldest_queue_wait_seconds is not None:
            if waits.oldest_queue_wait_seconds >= 120:
                status = "invalid"
            elif waits.oldest_queue_wait_seconds >= 30 and status == "ok":
                status = "warning"

        return WorkersSnapshot(
            worker_count=len(worker_names),
            active_tasks=active_tasks,
            reserved_tasks=reserved_tasks,
            queue_depth=queue_depth,
            total_concurrency=total_concurrency if concurrency_known else None,
            load_percent=load_percent,
            workers=workers,
            waits=waits,
            status=status,
            detail=None
            if worker_names
            else "No Celery workers responded to inspect ping",
        )
    except Exception as exc:
        return WorkersSnapshot(
            queue_depth=queue_depth,
            waits=waits,
            status="invalid",
            detail=_truncate(str(exc)),
        )


def _provider_snapshot(
    provider: str,
    *,
    configured: bool,
    list_fn: Callable[[], tuple[list, str | None]] | None = None,
    check_fn: Callable[[], str | None] | None = None,
) -> ProviderSnapshot:
    if not configured:
        return ProviderSnapshot(
            provider=provider,
            status="missing",
            detail="API key not set",
        )

    def _probe() -> str:
        if check_fn is not None:
            error = check_fn()
            if error:
                raise RuntimeError(error)
            return "Reachable"
        assert list_fn is not None
        _, error = list_fn()
        if error:
            lowered = error.lower()
            if "not set" in lowered:
                raise RuntimeError(error)
            if any(token in lowered for token in ("401", "403", "auth", "invalid api", "incorrect api")):
                raise RuntimeError(error)
            raise RuntimeError(error)
        return "Reachable"

    ms, detail, error = _timed_ms(_probe)
    if error:
        lowered = error.lower()
        status: EnvStatus = "invalid"
        if "not set" in lowered:
            status = "missing"
        return ProviderSnapshot(
            provider=provider,
            status=status,
            latency_ms=round(ms, 1),
            detail=error,
        )
    return ProviderSnapshot(
        provider=provider,
        status="ok",
        latency_ms=round(ms, 1),
        detail=detail if isinstance(detail, str) else "Reachable",
    )


def _collect_providers() -> list[ProviderSnapshot]:
    return [
        _provider_snapshot(
            "openai",
            configured=bool(settings.openai_api_key),
            list_fn=_openai_models,
        ),
        _provider_snapshot(
            "azure",
            configured=bool(settings.azure_openai_api_key and settings.azure_openai_endpoint),
            list_fn=_azure_models,
        ),
        _provider_snapshot(
            "gemini",
            configured=bool(settings.gemini_api_key),
            list_fn=_gemini_models,
        ),
        _provider_snapshot(
            "groq",
            configured=bool(settings.groq_api_key),
            list_fn=_groq_models,
        ),
        _provider_snapshot(
            "anthropic",
            configured=bool(settings.anthropic_api_key),
            list_fn=_anthropic_models,
        ),
        _provider_snapshot(
            "openrouter",
            configured=bool(settings.openrouter_api_key),
            list_fn=_openrouter_models,
        ),
        _provider_snapshot(
            "vertex",
            configured=bool(
                settings.google_vertex_project
                or (
                    settings.gemini_vertex_json
                    and settings.gemini_vertex_json.strip() not in {"", "{}"}
                )
            ),
            list_fn=_vertex_models,
        ),
        _provider_snapshot(
            "perplexity",
            configured=bool(settings.perplexity_api_key),
            check_fn=_check_perplexity,
        ),
    ]


def _probe_mcp_server(server: Any) -> McpServerCheck:
    import asyncio

    from app.services.mcp.client import discover_server_capabilities

    def _probe() -> dict[str, Any]:
        return asyncio.run(discover_server_capabilities(server))

    ms, discovered, error = _timed_ms(_probe)
    if error:
        return McpServerCheck(
            id=server.id,
            name=server.name,
            transport=server.transport,
            status="invalid",
            latency_ms=round(ms, 1),
            detail=_truncate(error),
        )
    assert isinstance(discovered, dict)
    tools = discovered.get("tools") or []
    resources = discovered.get("resources") or []
    templates = discovered.get("resource_templates") or []
    prompts = discovered.get("prompts") or []
    tool_count = len(tools) if isinstance(tools, list) else 0
    resource_count = (len(resources) if isinstance(resources, list) else 0) + (
        len(templates) if isinstance(templates, list) else 0
    )
    prompt_count = len(prompts) if isinstance(prompts, list) else 0
    return McpServerCheck(
        id=server.id,
        name=server.name,
        transport=server.transport,
        status="ok",
        latency_ms=round(ms, 1),
        tools=tool_count,
        resources=resource_count,
        prompts=prompt_count,
        detail=(
            f"{tool_count} tools, {resource_count} resources, {prompt_count} prompts"
        ),
    )


def _collect_mcp_servers() -> list[McpServerCheck]:
    from app.services.mcp.catalog import (
        MCP_SERVERS_CONFIG_PATH,
        CatalogLoadError,
        load_mcp_catalog,
    )

    path = Path(MCP_SERVERS_CONFIG_PATH)
    if not path.is_file():
        return [
            McpServerCheck(
                id="catalog",
                name="MCP catalog",
                status="missing",
                detail=f"Catalog not found: {path}",
            )
        ]

    try:
        servers = load_mcp_catalog(path)
    except CatalogLoadError as exc:
        return [
            McpServerCheck(
                id="catalog",
                name="MCP catalog",
                status="invalid",
                detail=_truncate(str(exc)),
            )
        ]
    except Exception as exc:
        return [
            McpServerCheck(
                id="catalog",
                name="MCP catalog",
                status="invalid",
                detail=_truncate(f"Failed to load catalog: {exc}"),
            )
        ]

    if not servers:
        return [
            McpServerCheck(
                id="catalog",
                name="MCP catalog",
                status="ok",
                detail=f"No enabled servers in {path}",
            )
        ]

    checks: list[McpServerCheck] = []
    with ThreadPoolExecutor(max_workers=min(8, len(servers))) as executor:
        futures = {executor.submit(_probe_mcp_server, server): server for server in servers}
        for future in as_completed(futures):
            server = futures[future]
            try:
                checks.append(future.result())
            except Exception as exc:  # pragma: no cover - defensive
                checks.append(
                    McpServerCheck(
                        id=server.id,
                        name=server.name,
                        transport=getattr(server, "transport", None),
                        status="invalid",
                        detail=_truncate(format_exception_detail(exc, limit=400), limit=320),
                    )
                )
    checks.sort(key=lambda item: item.id)
    return checks


def _dir_size_bytes(path: Path, *, max_entries: int = 50_000) -> tuple[int | None, str | None]:
    if not path.exists():
        return None, "Path does not exist"
    total = 0
    entries = 0
    try:
        for root, _dirs, files in os.walk(path):
            for name in files:
                entries += 1
                if entries > max_entries:
                    return total, f"Partial size after {max_entries} files"
                try:
                    total += (Path(root) / name).stat().st_size
                except OSError:
                    continue
        return total, None
    except Exception as exc:
        return None, _truncate(str(exc))


def _collect_data_volume() -> list[DataVolumeMetric]:
    metrics: list[DataVolumeMetric] = []
    try:
        with Session(engine) as session:
            orgs = session.exec(select(func.count()).select_from(Org)).one()
            users = session.exec(select(func.count()).select_from(User)).one()
            chats = session.exec(select(func.count()).select_from(Chat)).one()
            attachments = session.exec(
                select(func.count()).select_from(ChatMessageAttachment)
            ).one()
            pending_statuses = (
                GenerationStatus.queued,
                GenerationStatus.running,
                GenerationStatus.streaming,
            )
            pending = session.exec(
                select(func.count())
                .select_from(ChatGenerationTask)
                .where(col(ChatGenerationTask.status).in_(pending_statuses))
            ).one()
            queued = session.exec(
                select(func.count())
                .select_from(ChatGenerationTask)
                .where(ChatGenerationTask.status == GenerationStatus.queued)
            ).one()
            running = session.exec(
                select(func.count())
                .select_from(ChatGenerationTask)
                .where(
                    col(ChatGenerationTask.status).in_(
                        (GenerationStatus.running, GenerationStatus.streaming)
                    )
                )
            ).one()
        metrics.extend(
            [
                DataVolumeMetric(name="Organisations", value=str(orgs)),
                DataVolumeMetric(name="Users", value=str(users)),
                DataVolumeMetric(name="Chats", value=str(chats)),
                DataVolumeMetric(name="Attachments", value=str(attachments)),
                DataVolumeMetric(
                    name="Pending generations",
                    value=str(pending),
                    detail=f"Queued {queued}, running/streaming {running}",
                ),
            ]
        )
    except Exception as exc:
        metrics.append(
            DataVolumeMetric(
                name="Database counts",
                value="—",
                detail=_truncate(str(exc)),
            )
        )

    files_dir = Path(settings.files_base_dir or "/data/files")
    size, note = _dir_size_bytes(files_dir)
    metrics.append(
        DataVolumeMetric(
            name="FILES_BASE_DIR size",
            value=_format_bytes(size) if size is not None else "—",
            detail=note or str(files_dir),
        )
    )
    return metrics


def diagnose_system() -> SystemDiagnosis:
    specs = _build_specs()
    results: list[EnvKeyDiagnosis | None] = [None] * len(specs)

    disks: list[DiskUsageInfo] = []
    dependencies: list[DependencyCheck] = []
    resources: list[ResourceMetric] = []
    providers: list[ProviderSnapshot] = []
    mcp_servers: list[McpServerCheck] = []
    data_volume: list[DataVolumeMetric] = []
    workers = WorkersSnapshot()

    # Live checks can be slow; run env + panels concurrently.
    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {
            executor.submit(_diagnose_one, spec): ("env", index)
            for index, spec in enumerate(specs)
        }
        futures[executor.submit(_collect_disk_usage)] = ("disks", None)
        futures[executor.submit(_collect_dependencies)] = ("dependencies", None)
        futures[executor.submit(_collect_resources)] = ("resources", None)
        futures[executor.submit(_collect_providers)] = ("providers", None)
        futures[executor.submit(_collect_mcp_servers)] = ("mcp_servers", None)
        futures[executor.submit(_collect_data_volume)] = ("data_volume", None)
        futures[executor.submit(_collect_workers)] = ("workers", None)

        for future in as_completed(futures):
            kind, index = futures[future]
            try:
                value = future.result()
            except Exception as exc:  # pragma: no cover - defensive
                if kind == "env" and index is not None:
                    spec = specs[index]
                    results[index] = EnvKeyDiagnosis(
                        key=spec.key,
                        category=spec.category,
                        status="invalid",
                        required=spec.required,
                        detail=_truncate(f"Diagnosis failed: {exc}"),
                    )
                continue
            if kind == "env" and index is not None:
                results[index] = value
            elif kind == "disks":
                disks = value
            elif kind == "dependencies":
                dependencies = value
            elif kind == "resources":
                resources = value
            elif kind == "providers":
                providers = value
            elif kind == "mcp_servers":
                mcp_servers = value
            elif kind == "data_volume":
                data_volume = value
            elif kind == "workers":
                workers = value

    keys = [item for item in results if item is not None]
    summary = {"ok": 0, "invalid": 0, "missing": 0}
    for item in keys:
        summary[item.status] = summary.get(item.status, 0) + 1
    return SystemDiagnosis(
        keys=keys,
        disks=disks,
        dependencies=dependencies,
        resources=resources,
        providers=providers,
        mcp_servers=mcp_servers,
        data_volume=data_volume,
        workers=workers,
        summary=summary,
    )
