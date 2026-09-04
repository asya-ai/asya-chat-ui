# Setup

Primary guide: `docs/setup.md`

## Quick Start

```bash
cp .env.example .env
docker compose up --build
```

Main URL: `http://127.0.0.1:8085`

## Required Inputs

- `JWT_SECRET`
- database credentials
- one provider API key

MCP servers: configure in **Settings → Integrations**. See `docs/configuration.md`.

For complete details and production instructions, see `docs/setup.md`.
