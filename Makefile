.PHONY: test test-backend test-backend-unit

test: test-backend

test-backend:
	cd backend && uv run pytest

test-backend-unit:
	cd backend && uv run pytest -k "config or openai_compat_helpers or langchain_runtime"
