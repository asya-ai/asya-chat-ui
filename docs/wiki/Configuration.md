# Configuration

Primary reference: `docs/configuration.md`

## Core Variables

- `APP_ENV`
- `JWT_SECRET`
- `DATABASE_URL`
- `FILES_BASE_DIR`
- provider keys (`OPENAI_API_KEY`, `GEMINI_API_KEY`, etc.)

## MCP data sources

- Catalog file (fixed path): `/config/mcp_servers.yaml` ← host `config/mcp_servers.yaml`
- Optional env: `MCP_CACHE_TTL_SECONDS`, `MCP_CALL_TIMEOUT_SECONDS`, `MCP_MAX_RESULT_CHARS`
- System diagnosis probes each enabled server’s availability
- See **MCP Servers** in `docs/configuration.md` for YAML schema, auth headers, and tool naming

## Frontend Variables

- `VITE_API_URL`
- polling toggles (`VITE_FORCE_POLLING`, `CHOKIDAR_USEPOLLING`, `WATCHPACK_POLLING`)

For full variable list, defaults, and service-specific settings, see `docs/configuration.md`.
