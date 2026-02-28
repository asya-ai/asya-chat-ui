FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    UV_SYSTEM_PYTHON=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen

COPY app ./app
COPY executor ./executor
COPY alembic ./alembic
COPY alembic.ini .

ENV PYTHONPATH=/app

CMD ["sh", "-c", "uv run python app/db/wait_for_db.py && uv run alembic upgrade head && uv run uvicorn app.main:app --host 0.0.0.0 --port 8000"]
