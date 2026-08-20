.PHONY: test test-backend test-backend-unit build-executor rebuild-nginx

test: test-backend

build-executor:
	docker compose run --rm executor-bootstrap

# Frontend / nginx config only — skips backend/scraper rebuild and full stack recreate.
rebuild-nginx:
	docker compose up -d --build --force-recreate --no-deps nginx

test-backend:
	cd backend && uv run pytest

test-backend-unit:
	cd backend && uv run pytest -k "config or openai_compat_helpers or langchain_runtime"
