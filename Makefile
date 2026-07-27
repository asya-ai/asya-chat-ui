.PHONY: test test-backend test-backend-unit build-executor

test: test-backend

build-executor:
	docker compose run --rm executor-bootstrap

test-backend:
	cd backend && uv run pytest

test-backend-unit:
	cd backend && uv run pytest -k "config or openai_compat_helpers or langchain_runtime"
