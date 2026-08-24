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
COPY app ./app
ARG AGENT_EMBEDDING_MODEL=BAAI/bge-m3
ENV AGENT_EMBEDDING_MODEL=${AGENT_EMBEDDING_MODEL}
ENV PYTHONPATH=/app
RUN uv run python -c "from app.services.agents.onnx_embedder import warmup_onnx_embedder; warmup_onnx_embedder('${AGENT_EMBEDDING_MODEL}')"

COPY executor ./executor
COPY alembic ./alembic
COPY alembic.ini .

CMD ["sh", "-c", "uv run python app/db/wait_for_db.py && uv run uvicorn app.main:app --host 0.0.0.0 --port 8000"]
