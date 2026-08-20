# Maintenance

Primary runbook: `docs/maintenance.md`

## Quick Checks

```bash
docker compose ps
curl -fsS http://127.0.0.1:8085/healthz
docker compose logs --since=1h backend worker beat scraper
```

## Upgrades

- Local dev: `docker compose up --build -d`
- Frontend/nginx only: `make rebuild-nginx`
- Production: `docker compose -f docker-compose.prod.yml pull && docker compose -f docker-compose.prod.yml up -d`

For backup/restore and troubleshooting details, see `docs/maintenance.md`.
