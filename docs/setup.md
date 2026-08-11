# Setup Guide

This guide covers local development and production deployment.

## Prerequisites

- Docker + Docker Compose plugin
- Git
- (Optional) `uv` and `pnpm` for running backend/frontend outside containers

## 1) Configure Environment

From repository root:

```bash
cp .env.example .env
```

Set at minimum:

- `JWT_SECRET`
- database variables (see note below)
- one provider key (`OPENAI_API_KEY`, `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, etc.)

Database variable note:

- `docker-compose.prod.yml` uses `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`.
- `docker-compose.yml` currently maps `DATABASE_USER`, `DATABASE_PASSWORD`, `DATABASE_NAME` into Postgres.
- To avoid startup issues in local compose, set both sets in `.env`.

## 2) Start Local Development

```bash
docker compose up --build
```

Expected endpoints:

- App (nginx): `http://127.0.0.1:8085`
- Frontend dev server (override): `http://localhost:5173`

Services started: `postgres`, `redis`, `migrate`, `backend`, `worker`, `beat`, `scraper`, `dind`, `executor-bootstrap`, `nginx`, and `frontend` (via override).

## 3) Verify Health

```bash
docker compose ps
curl -fsS http://127.0.0.1:8085/healthz
```

If health fails, inspect logs:

```bash
docker compose logs backend migrate postgres worker scraper
```

## 4) Production Deploy (Docker Hub images)

```bash
cp .env.example .env
# edit .env
docker compose -f docker-compose.prod.yml up -d
```

Optional release tag:

```bash
export CHATUI_TAG=latest
docker compose -f docker-compose.prod.yml up -d
```

## 5) Common Dev Commands

- Run backend tests: `make test`
- Rebuild execution image inside dind: `make build-executor`
- Stop stack: `docker compose down`
- Stop + remove volumes: `docker compose down -v`
