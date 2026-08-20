# Maintenance Runbook

## Daily / Routine Checks

- Verify service health:
  - `docker compose ps`
  - `curl -fsS http://127.0.0.1:8085/healthz`
- Review error logs:
  - `docker compose logs --since=1h backend worker beat scraper`
- Monitor disk growth:
  - `data/files`
  - Postgres volume
  - dind volume

## Database and Migrations

- Migrations run automatically via `migrate` service.
- Run manually if needed:

```bash
docker compose run --rm migrate
```

- Verify migration status in backend container:

```bash
docker compose exec backend uv run alembic current
```

## Updating the Stack

### Local dev update

```bash
git pull
docker compose up --build -d
```

Frontend / nginx config only (fast path — does not rebuild backend or recreate the stack):

```bash
make rebuild-nginx
# or: docker compose up -d --build --force-recreate --no-deps nginx
```

Nginx re-resolves `backend` via Docker DNS, so recreating backend alone no longer requires recreating nginx.

### Production update

```bash
git pull
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

If deploying specific release images:

```bash
export CHATUI_TAG=<release-tag>
docker compose -f docker-compose.prod.yml up -d
```

## Execution Runtime Maintenance

Code execution depends on image `chatui-python-exec:latest` in the dind daemon.

- Rebuild/pull runtime image:

```bash
make build-executor
```

- If execution failures appear, validate dind health and image presence:

```bash
docker compose exec dind docker images
docker compose logs dind executor-bootstrap backend worker
```

## Backup and Restore Basics

- Backup:
  - Postgres volume (`postgres-data` in prod, `./postgres_data` in local compose)
  - Files volume (`files-data` in prod, `./data/files` in local compose)
- Restore order:
  1. Restore DB
  2. Restore file storage
  3. Run migrations (`migrate`)
  4. Start app services

## Troubleshooting Shortlist

- App unavailable on port:
  - Check `CHATUI_BIND_ADDRESS` and `CHATUI_PORT`.
- Backend boot loop:
  - Check DB credentials and migration logs.
- No model responses:
  - Verify provider keys and org provider/model settings.
- Web search/scraping failures:
  - Check `SCRAPER_URL`, `SCRAPER_PORT`, and scraper logs.
- Code execution errors:
  - Check dind health and executor image availability.

## Release Hygiene

- Keep `.env.example` synchronized with actual required vars.
- Keep `docs/configuration.md` aligned with `backend/app/core/config.py`.
- Run backend tests before release:
  - `make test`
