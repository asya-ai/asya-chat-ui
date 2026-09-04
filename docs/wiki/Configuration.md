# Configuration

Primary reference: `docs/configuration.md`

## Core Variables

- `APP_ENV`
- `JWT_SECRET`
- `DATABASE_URL`
- `FILES_BASE_DIR`
- provider keys (`OPENAI_API_KEY`, `GEMINI_API_KEY`, etc.)

## MCP data sources

- Configure in **Settings → Integrations** (instance, org, user scopes)
- Optional env: `MCP_CACHE_TTL_SECONDS`, `MCP_CONNECT_TIMEOUT_SECONDS`, `MCP_CALL_TIMEOUT_SECONDS`, `MCP_MAX_RESULT_CHARS`, `MCP_SPILL_THRESHOLD_CHARS`, `MCP_SPILL_SAMPLE_CHARS`, `MCP_SPILL_GET_MAX_CHARS`
- System diagnosis probes instance MCP servers; user-provided servers require user connection
- See **MCP Servers** in `docs/configuration.md`

## Frontend Variables

- `VITE_API_URL`
- polling toggles (`VITE_FORCE_POLLING`, `CHOKIDAR_USEPOLLING`, `WATCHPACK_POLLING`)

For full variable list, defaults, and service-specific settings, see `docs/configuration.md`.
