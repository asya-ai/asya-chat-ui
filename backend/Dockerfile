FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    UV_SYSTEM_PYTHON=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/opt/hf-cache \
    TRANSFORMERS_CACHE=/opt/hf-cache \
    SENTENCE_TRANSFORMERS_HOME=/opt/hf-cache

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
ARG AGENT_EMBEDDING_MODEL=BAAI/bge-m3
ENV AGENT_EMBEDDING_MODEL=${AGENT_EMBEDDING_MODEL}
RUN uv run python -c "from fastembed import TextEmbedding; list(TextEmbedding(model_name='${AGENT_EMBEDDING_MODEL}').passage_embed(['warmup']))"

COPY app ./app
COPY executor ./executor
COPY alembic ./alembic
COPY alembic.ini .

ENV PYTHONPATH=/app

CMD ["sh", "-c", "uv run python app/db/wait_for_db.py && uv run uvicorn app.main:app --host 0.0.0.0 --port 8000"]
