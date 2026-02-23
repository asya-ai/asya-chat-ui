from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = Field(default="development", validation_alias="APP_ENV")
    database_url: str = Field(
        default="postgresql+psycopg://chatui:chatui@postgres:5432/chatui",
        validation_alias="DATABASE_URL",
    )
    secret_key: str = Field(validation_alias="JWT_SECRET")
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = Field(
        default=60 * 24 * 7, validation_alias="ACCESS_TOKEN_EXPIRE_MINUTES"
    )
    invite_expire_hours: int = 72
    files_base_dir: str = Field(default="/data/files", validation_alias="FILES_BASE_DIR")
    exec_host_files_dir: str = Field(
        default="", validation_alias="EXEC_HOST_FILES_DIR"
    )
    exec_docker_image: str = Field(
        default="chatui-python-exec:latest", validation_alias="EXEC_DOCKER_IMAGE"
    )
    exec_timeout_seconds: int = Field(
        default=20, validation_alias="EXEC_TIMEOUT_SECONDS"
    )
    exec_max_output_bytes: int = Field(
        default=20000, validation_alias="EXEC_MAX_OUTPUT_BYTES"
    )
    exec_max_output_file_bytes: int = Field(
        default=250000, validation_alias="EXEC_MAX_OUTPUT_FILE_BYTES"
    )
    attachments_max_files: int = Field(
        default=10, validation_alias="ATTACHMENTS_MAX_FILES"
    )
    attachments_max_file_bytes: int = Field(
        default=5_000_000, validation_alias="ATTACHMENTS_MAX_FILE_BYTES"
    )
    attachments_max_total_bytes: int = Field(
        default=10_000_000, validation_alias="ATTACHMENTS_MAX_TOTAL_BYTES"
    )
    exec_max_code_chars: int = Field(
        default=20000, validation_alias="EXEC_MAX_CODE_CHARS"
    )
    exec_cpu_limit: float = Field(default=1.0, validation_alias="EXEC_CPU_LIMIT")
    exec_memory_limit: str = Field(
        default="512m", validation_alias="EXEC_MEMORY_LIMIT"
    )
    openai_api_key: str = Field(default="", validation_alias="OPENAI_API_KEY")
    openai_base_url: str = Field(
        default="https://api.openai.com/v1", validation_alias="OPENAI_BASE_URL"
    )
    openai_chat_model: str = Field(
        default="gpt-4o-mini", validation_alias="OPENAI_CHAT_MODEL"
    )
    openai_image_model: str = Field(
        default="gpt-image-1", validation_alias="OPENAI_IMAGE_MODEL"
    )
    openai_prompt_cache_retention: str | None = Field(
        default=None, validation_alias="OPENAI_PROMPT_CACHE_RETENTION"
    )
    azure_openai_api_key: str = Field(default="", validation_alias="AZURE_OPENAI_API_KEY")
    azure_openai_endpoint: str = Field(default="", validation_alias="AZURE_OPENAI_ENDPOINT")
    azure_openai_deployment: str = Field(
        default="", validation_alias="AZURE_OPENAI_DEPLOYMENT"
    )
    azure_openai_api_version: str = Field(
        default="2024-06-01", validation_alias="AZURE_OPENAI_API_VERSION"
    )
    gemini_api_key: str = Field(default="", validation_alias="GEMINI_API_KEY")
    gemini_base_url: str = Field(
        default="https://generativelanguage.googleapis.com",
        validation_alias="GEMINI_BASE_URL",
    )
    gemini_vertex_json: str = Field(default="{}", validation_alias="GEMINI_VERTEX_JSON")
    gemini_chat_model: str = Field(
        default="gemini-1.5-flash", validation_alias="GEMINI_CHAT_MODEL"
    )
    gemini_image_model: str = Field(
        default="imagen-3.0-generate-001", validation_alias="GEMINI_IMAGE_MODEL"
    )
    gemini_cached_content_enabled: bool = Field(
        default=True, validation_alias="GEMINI_CACHED_CONTENT_ENABLED"
    )
    gemini_cached_content_ttl_seconds: int = Field(
        default=900, validation_alias="GEMINI_CACHED_CONTENT_TTL_SECONDS"
    )
    gemini_cached_content_max_items: int = Field(
        default=512, validation_alias="GEMINI_CACHED_CONTENT_MAX_ITEMS"
    )
    groq_api_key: str = Field(default="", validation_alias="GROQ_API_KEY")
    groq_base_url: str = Field(
        default="https://api.groq.com", validation_alias="GROQ_BASE_URL"
    )
    groq_chat_model: str = Field(
        default="llama-3.1-8b-instant", validation_alias="GROQ_CHAT_MODEL"
    )
    anthropic_api_key: str = Field(default="", validation_alias="ANTHROPIC_API_KEY")
    anthropic_base_url: str = Field(
        default="https://api.anthropic.com", validation_alias="ANTHROPIC_BASE_URL"
    )
    anthropic_chat_model: str = Field(
        default="claude-3-5-sonnet-20241022",
        validation_alias="ANTHROPIC_CHAT_MODEL",
    )
    perplexity_api_key: str = Field(default="", validation_alias="PERPLEXITY_API_KEY")
    perplexity_model: str = Field(
        default="sonar-pro", validation_alias="PERPLEXITY_MODEL"
    )
    smtp_user: str = Field(default="", validation_alias="SMTP_USER")
    smtp_password: str = Field(default="", validation_alias="SMTP_PASSWORD")
    smtp_email: str = Field(default="", validation_alias="SMTP_EMAIL")
    smtp_host: str = Field(default="", validation_alias="SMTP_HOST")
    smtp_port: str = Field(default="", validation_alias="SMTP_PORT")
    password_reset_expire_hours: int = Field(
        default=1, validation_alias="PASSWORD_RESET_EXPIRE_HOURS"
    )
    super_admin_emails: str = Field(
        default="", validation_alias="SUPER_ADMIN_EMAILS"
    )
    scraper_url: str = Field(
        default="http://scraper:3001", validation_alias="SCRAPER_URL"
    )
    web_search_limit: int = Field(default=5, validation_alias="WEB_SEARCH_LIMIT")
    scrape_text_limit: int = Field(default=20000, validation_alias="SCRAPE_TEXT_LIMIT")
    scrape_parallel_max: int = Field(
        default=5, validation_alias="SCRAPE_PARALLEL_MAX"
    )

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


def _normalize_database_url(url: str) -> str:
    if url.startswith("postgresql+asyncpg"):
        return url.replace("postgresql+asyncpg", "postgresql+psycopg", 1)
    return url


def _normalize_openai_base_url(url: str) -> str:
    cleaned = url.rstrip("/")
    if cleaned.endswith("/v1"):
        return cleaned
    return f"{cleaned}/v1"


def _normalize_groq_base_url(url: str) -> str:
    cleaned = url.rstrip("/")
    if cleaned.endswith("/openai/v1"):
        return cleaned[: -len("/openai/v1")]
    if cleaned.endswith("/openai"):
        return cleaned[: -len("/openai")]
    return cleaned


settings = Settings()
settings.database_url = _normalize_database_url(settings.database_url)
settings.openai_base_url = _normalize_openai_base_url(settings.openai_base_url)
settings.groq_base_url = _normalize_groq_base_url(settings.groq_base_url)


def get_super_admin_emails() -> set[str]:
    return {
        email.strip().lower()
        for email in settings.super_admin_emails.split(",")
        if email.strip()
    }
