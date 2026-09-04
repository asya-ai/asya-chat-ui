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
  - `AGENT_EMBEDDING_MODEL` (default: `BAAI/bge-m3`; Docker build arg + runtime env must match — ONNX weights are downloaded at image build)
  - `AGENT_EMBEDDING_BATCH_SIZE` (default: 16)
  - Compose-injected runtime vars (shown in System diagnosis): `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`, `DOCKER_HOST`, `EXEC_HOST_FILES_DIR`, `HF_HOME`
- MCP data sources (configured in **Settings → Integrations**):
  - `MCP_CACHE_TTL_SECONDS` (default: 300)
  - `MCP_CONNECT_TIMEOUT_SECONDS` (default: 15)
  - `MCP_CALL_TIMEOUT_SECONDS` (default: 60)
  - `MCP_MAX_RESULT_CHARS` (default: 50000) — soft per-string cap / fallback when spill unavailable
  - `MCP_SPILL_THRESHOLD_CHARS` (default: 8000) — larger MCP results are written to `chats/{chat_id}/mcp/*.json` and the model gets shape/sample + `artifact_id`
  - `MCP_SPILL_SAMPLE_CHARS` (default: 800) — sample size in spill stubs
  - `MCP_SPILL_GET_MAX_CHARS` (default: 12000) — max payload returned by `mcp_data_get`
- Public URL:
  - `PUBLIC_API_BASE_URL` (used for links/externally visible API references)

## Provider Configuration

- OpenAI:
  - `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_PROMPT_CACHE_RETENTION`
- Azure OpenAI:
  - `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_VERSION`
- Gemini / Vertex:
  - `GEMINI_API_KEY`, `GEMINI_BASE_URL`, `GEMINI_VERTEX_JSON`
  - `GEMINI_CACHED_CONTENT_ENABLED`, `GEMINI_CACHED_CONTENT_TTL_SECONDS`, `GEMINI_CACHED_CONTENT_MAX_ITEMS`
  - `GOOGLE_VERTEX_PROJECT`, `GOOGLE_VERTEX_LOCATION` (defaults to `global`)
- Groq:
  - `GROQ_API_KEY`, `GROQ_BASE_URL`
- Anthropic:
  - `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`
- OpenRouter:
  - `OPENROUTER_API_KEY`
- Perplexity:
  - `PERPLEXITY_API_KEY`, `PERPLEXITY_MODEL`

Model IDs are configured in the Models settings UI, not via env vars.

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
- `WORKER_CONCURRENCY` (Celery worker `--concurrency`, default: 2)
- Workers consume queues in order `generation,celery,embedding` so chat answers preempt project reindex/embedding work.

## Scraper Variables

- `SCRAPER_PORT` (default: `3001`)
- `SCRAPE_TEXT_LIMIT` (default: `20000`)
- `PUPPETEER_EXECUTABLE_PATH` (optional custom browser binary path)

## MCP Servers (model data sources)

MCP servers are configured in the UI at **Settings → Integrations** (super-admin instance servers, org servers/bindings, user personal servers and connections). There is no YAML catalog.

Runtime tuning:

- `MCP_CACHE_TTL_SECONDS` (default: 300) — discovery cache per server/auth fingerprint
- `MCP_CONNECT_TIMEOUT_SECONDS` (default: 15) — connect + capability discovery
- `MCP_CALL_TIMEOUT_SECONDS` (default: 60) — tool/resource/prompt calls
- `MCP_MAX_RESULT_CHARS` (default: 50000) — soft string truncate / spill fallback
- `MCP_SPILL_THRESHOLD_CHARS` (default: 8000) — spill large results to disk
- `MCP_SPILL_SAMPLE_CHARS` (default: 800)
- `MCP_SPILL_GET_MAX_CHARS` (default: 12000)

Auth modes: none, shared bearer/API token, or user-provided (bearer or API token per user).

Large tool/resource/prompt responses are written under `chats/{chat_id}/mcp/` and staged into code execution at `/workspace/data/`. The model receives a stub (`artifact_id`, shape, sample, path) and can use `mcp_data_list` / `mcp_data_get` for slices.

### How capabilities map to model tools

| MCP feature | Tool names |
|---|---|
| Tools | `{server_id}__{tool_name}` |
| Resources | `{server_id}__list_resources`, `{server_id}__read_resource` |
| Prompts | `{server_id}__list_prompts`, `{server_id}__get_prompt` |
| Spilled results | `mcp_data_list`, `mcp_data_get` (+ `/workspace/data/` in `code_execution`) |

### System diagnosis

Super-admin **System diagnosis** probes enabled **instance** MCP servers. User-provided servers show as configured and require a user connection before tools appear in chat.

## Network and Port Configuration

- `CHATUI_BIND_ADDRESS` (prod compose nginx bind, default: `127.0.0.1`)
- `CHATUI_PORT` (prod compose nginx port, default: `8085`)
- `CHATUI_TAG` (Docker image tag selector for production compose)

## Recommended `.env` Hygiene

- Do not commit `.env`.
- Keep `.env.example` updated when adding/changing variables.
- Rotate provider/API keys periodically.
- Use distinct secrets for development and production.
