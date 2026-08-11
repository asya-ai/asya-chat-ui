# Environment Variables and Configuration

This file documents runtime configuration from `backend/app/core/config.py`, compose files, and frontend/scraper runtime.

## Required Variables

- `JWT_SECRET`: JWT signing key.
- Database settings:
  - For production compose: `POSTGRES_PASSWORD` (and optionally `POSTGRES_USER`, `POSTGRES_DB`).
  - For local compose file: `DATABASE_USER`, `DATABASE_PASSWORD`, `DATABASE_NAME` are used for the Postgres container environment.
- At least one model provider key:
  - `OPENAI_API_KEY`, `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `GROQ_API_KEY`, or `OPENROUTER_API_KEY`.

## Backend Core Variables

- App/auth:
  - `APP_ENV` (default: `development`)
  - `JWT_SECRET`
  - `ACCESS_TOKEN_EXPIRE_MINUTES` (default: 10080)
  - `PASSWORD_RESET_EXPIRE_HOURS` (default: 1)
  - `SUPER_ADMIN_EMAILS` (comma-separated)
- Storage/attachments:
  - `FILES_BASE_DIR` (default: `/data/files`)
  - `ATTACHMENTS_MAX_FILES` (default: 50)
  - `ATTACHMENTS_MAX_FILE_BYTES` (default: 20000000)
  - `ATTACHMENTS_MAX_TOTAL_BYTES` (default: 50000000)
  - `ATTACHMENT_URL_EXPIRE_MINUTES` (default: 60)
- Execution sandbox:
  - `EXEC_HOST_FILES_DIR`
  - `EXEC_DOCKER_IMAGE` (default: `chatui-python-exec:latest`)
  - `EXEC_TIMEOUT_SECONDS`
  - `EXEC_MAX_OUTPUT_BYTES`
  - `EXEC_MAX_OUTPUT_FILE_BYTES`
  - `EXEC_MAX_CODE_CHARS`
  - `EXEC_CPU_LIMIT`
  - `EXEC_MEMORY_LIMIT`
  - `EXEC_PIDS_LIMIT`
  - `EXEC_TMPFS_SIZE`
  - `EXEC_ULIMIT_NOFILE`
  - `EXEC_ULIMIT_FSIZE_BYTES`
  - `EXEC_ULIMIT_NPROC`
- Search/scraping/RAG:
  - `SCRAPER_URL` (default: `http://scraper:3001`)
  - `WEB_SEARCH_LIMIT` (default: 5)
  - `SCRAPE_TEXT_LIMIT` (default: 20000)
  - `SCRAPE_PARALLEL_MAX` (default: 5)
  - `AGENT_EMBEDDING_MODEL` (default: `BAAI/bge-m3`)
  - `AGENT_EMBEDDING_BATCH_SIZE` (default: 16)
  - `AGENT_EMBEDDING_DEVICE` (default: `cpu`)
- Public URL:
  - `PUBLIC_API_BASE_URL` (used for links/externally visible API references)

## Provider Configuration

- OpenAI:
  - `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_CHAT_MODEL`, `OPENAI_IMAGE_MODEL`, `OPENAI_PROMPT_CACHE_RETENTION`
- Azure OpenAI:
  - `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT`, `AZURE_OPENAI_API_VERSION`
- Gemini / Vertex:
  - `GEMINI_API_KEY`, `GEMINI_BASE_URL`, `GEMINI_VERTEX_JSON`, `GEMINI_CHAT_MODEL`, `GEMINI_IMAGE_MODEL`
  - `GEMINI_CACHED_CONTENT_ENABLED`, `GEMINI_CACHED_CONTENT_TTL_SECONDS`, `GEMINI_CACHED_CONTENT_MAX_ITEMS`
  - `GOOGLE_VERTEX_PROJECT`, `GOOGLE_VERTEX_LOCATION`
- Groq:
  - `GROQ_API_KEY`, `GROQ_BASE_URL`, `GROQ_CHAT_MODEL`
- Anthropic:
  - `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, `ANTHROPIC_CHAT_MODEL`
- OpenRouter:
  - `OPENROUTER_API_KEY`
- Perplexity:
  - `PERPLEXITY_API_KEY`, `PERPLEXITY_MODEL`

## Email/SMTP

- `SMTP_USER`
- `SMTP_PASSWORD`
- `SMTP_EMAIL`
- `SMTP_HOST`
- `SMTP_PORT`

## Frontend Runtime Variables

- `VITE_API_URL`: API base path or URL (default fallback: `/api`).
- Polling flags (used by `vite.config.ts`):
  - `VITE_FORCE_POLLING=true`
  - `CHOKIDAR_USEPOLLING=1`
  - `WATCHPACK_POLLING=true`

## Worker and Scaling Variables

- `CELERY_BROKER_URL` (defaults to Redis in compose)
- `CELERY_RESULT_BACKEND` (defaults to Redis in compose)
- `WORKER_REPLICAS` (compose deploy replicas, default: 1)

## Scraper Variables

- `SCRAPER_PORT` (default: `3001`)
- `SCRAPE_TEXT_LIMIT` (default: `20000`)
- `PUPPETEER_EXECUTABLE_PATH` (optional custom browser binary path)

## Network and Port Configuration

- `CHATUI_BIND_ADDRESS` (prod compose nginx bind, default: `127.0.0.1`)
- `CHATUI_PORT` (prod compose nginx port, default: `8085`)
- `CHATUI_TAG` (Docker image tag selector for production compose)

## Recommended `.env` Hygiene

- Do not commit `.env`.
- Keep `.env.example` updated when adding/changing variables.
- Rotate provider/API keys periodically.
- Use distinct secrets for development and production.
