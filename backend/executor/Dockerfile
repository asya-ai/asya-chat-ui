FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /workspace

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gfortran \
    libjpeg62-turbo \
    libpng16-16 \
    zlib1g \
    libopenblas0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

RUN useradd -m -u 10001 runner
USER runner

CMD ["python", "/workspace/main.py"]
