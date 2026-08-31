# Publishing to Docker Hub

This guide covers building and pushing the four Asya Chat UI images to Docker Hub under the `asyaai` namespace, then deploying them with `docker-compose.prod.yml`.

## Images


| Image                          | Dockerfile                    | Context                                     | Purpose                       |
| ------------------------------ | ----------------------------- | ------------------------------------------- | ----------------------------- |
| `asyaai/asya-chat-ui-backend`  | `backend/Dockerfile`          | `backend/`                                  | API, migrate, worker, beat    |
| `asyaai/asya-chat-ui-web`      | `nginx/Dockerfile`            | `frontend/` (+ `nginx/` additional context) | nginx + production frontend   |
| `asyaai/asya-chat-ui-scraper`  | `scraper/Dockerfile`          | `scraper/`                                  | Puppeteer scrape service      |
| `asyaai/asya-chat-ui-executor` | `backend/executor/Dockerfile` | `backend/executor/`                         | Python code-execution sandbox |


Publish tags:

- `vX.Y.Z` — immutable release tag
- `latest` — moving pointer to the newest release

All release images should include `linux/amd64` and `linux/arm64`.

## Prerequisites

1. Docker Desktop (or equivalent) with Buildx.
2. Logged in as a user with push access to `asyaai/*`:

```bash
docker login -u asyaai
```

1. Create these public Hub repositories if they do not exist yet:
  - `asya-chat-ui-backend`
  - `asya-chat-ui-web`
  - `asya-chat-ui-scraper`
  - `asya-chat-ui-executor`
2. A Buildx builder that supports multi-platform builds and attestations:

```bash
docker buildx inspect chatui-publisher >/dev/null 2>&1 ||
  docker buildx create \
    --name chatui-publisher \
    --driver docker-container \
    --use \
    --bootstrap

docker buildx use chatui-publisher
```



## Secret safety

Do **not** bake secrets into images.

- Runtime secrets stay in `.env` and are mounted only at deploy time by Compose.
- `.dockerignore` files exclude `.env`, credentials, keys, caches, and local data from build contexts.
- Never pass API keys, passwords, or JWT secrets through `--build-arg`.
- The only intentional backend build arg is `AGENT_EMBEDDING_MODEL` (model name, not a secret).
- Frontend builds must not introduce `VITE_*` credentials; only public values such as `VITE_API_URL` belong in the web bundle.
- Keep `frontend/package.json` pinned with `"packageManager": "pnpm@..."` so Corepack does not pull a different pnpm major and break `--frozen-lockfile`.

Before publishing, confirm Alembic is healthy:

```bash
cd backend
uv run alembic heads
uv run alembic history
```

There must be a single head and no missing revision IDs.

## Publish a version

Run from the repository root. Replace the version as needed.

```bash
cd /path/to/chatUI
export VERSION=v0.2.8
```



### 1) Web, scraper, executor

These images are small enough to build both platforms in one command:

```bash
docker buildx build \
  --builder chatui-publisher \
  --platform linux/amd64,linux/arm64 \
  -f nginx/Dockerfile \
  --build-context nginx_cfg=nginx \
  -t asyaai/asya-chat-ui-web:$VERSION \
  -t asyaai/asya-chat-ui-web:latest \
  --sbom=true --provenance=mode=max --push frontend

docker buildx build \
  --builder chatui-publisher \
  --platform linux/amd64,linux/arm64 \
  -f scraper/Dockerfile \
  -t asyaai/asya-chat-ui-scraper:$VERSION \
  -t asyaai/asya-chat-ui-scraper:latest \
  --sbom=true --provenance=mode=max --push scraper

docker buildx build \
  --builder chatui-publisher \
  --platform linux/amd64,linux/arm64 \
  -f backend/executor/Dockerfile \
  -t asyaai/asya-chat-ui-executor:$VERSION \
  -t asyaai/asya-chat-ui-executor:latest \
  --sbom=true --provenance=mode=max --push backend/executor
```



### 2) Backend (build architectures separately)

The backend image is still the heaviest because it preloads ONNX embedding weights and Python runtime dependencies. Build amd64 and arm64 separately, prune between them, then create the multi-arch manifests:

```bash
docker buildx build \
  --builder chatui-publisher \
  --platform linux/amd64 \
  -f backend/Dockerfile \
  -t asyaai/asya-chat-ui-backend:$VERSION-amd64 \
  -t asyaai/asya-chat-ui-backend:latest-amd64 \
  --sbom=true --provenance=mode=max --push backend

docker buildx prune --builder chatui-publisher --all --force

docker buildx build \
  --builder chatui-publisher \
  --platform linux/arm64 \
  -f backend/Dockerfile \
  -t asyaai/asya-chat-ui-backend:$VERSION-arm64 \
  -t asyaai/asya-chat-ui-backend:latest-arm64 \
  --sbom=true --provenance=mode=max --push backend

docker buildx imagetools create \
  -t asyaai/asya-chat-ui-backend:$VERSION \
  asyaai/asya-chat-ui-backend:$VERSION-amd64 \
  asyaai/asya-chat-ui-backend:$VERSION-arm64

docker buildx imagetools create \
  -t asyaai/asya-chat-ui-backend:latest \
  asyaai/asya-chat-ui-backend:latest-amd64 \
  asyaai/asya-chat-ui-backend:latest-arm64
```



### 3) Verify

```bash
for image in backend web scraper executor; do
  docker buildx imagetools inspect "asyaai/asya-chat-ui-$image:$VERSION"
done
```

Each inspect output should list both `linux/amd64` and `linux/arm64`.

## Deploy the published images

```bash
cp .env.example .env
# set JWT_SECRET, POSTGRES_PASSWORD, and at least one provider key

CHATUI_TAG=v0.2.3 docker compose -f docker-compose.prod.yml pull
CHATUI_TAG=v0.2.3 docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml ps
```

Defaults:

- bind address/port: `127.0.0.1:8085` (`CHATUI_BIND_ADDRESS`, `CHATUI_PORT`)
- image tag: `latest` when `CHATUI_TAG` is unset

`executor-bootstrap` pulls `asyaai/asya-chat-ui-executor` into DinD and tags it as `chatui-python-exec:latest` so code execution works without a local executor build.

## Disk space notes

Backend builds need substantial free space in Docker Desktop (often 15 GB+ free inside the VM). If BuildKit fails with `no space left on device`:

```bash
docker buildx prune --builder chatui-publisher --all --force
docker image prune -f
docker builder prune --all --force
```

Then retry the failed architecture. Avoid pruning volumes that hold production data.

## Troubleshooting


| Symptom                                              | Likely cause                           | Fix                                                       |
| ---------------------------------------------------- | -------------------------------------- | --------------------------------------------------------- |
| `push access denied` / `insufficient_scope`          | Not logged in or Hub repo missing      | `docker login -u asyaai`; create the Hub repositories     |
| `Attestation is not supported for the docker driver` | Using the default Docker driver        | Use the `chatui-publisher` `docker-container` builder     |
| `ERR_PNPM_LOCKFILE_CONFIG_MISMATCH`                  | Corepack pulled a different pnpm major | Pin `packageManager` in `frontend/package.json`           |
| Alembic `KeyError` / missing revision                | Broken migration graph                 | Fix `down_revision` links; confirm `uv run alembic heads` |
| Backend export fails with disk full                  | Docker VM out of space                 | Prune BuildKit/cache; rebuild one architecture at a time  |




## Checklist

- [ ] Logged into Docker Hub as `asyaai`
- [ ] `chatui-publisher` builder exists and is selected
- [ ] Alembic has a single valid head
- [ ] No secrets passed as build args
- [ ] Web / scraper / executor pushed for amd64+arm64
- [ ] Backend amd64 and arm64 pushed, then multi-arch manifests created
- [ ] `imagetools inspect` confirms both platforms for all four images
- [ ] Optional: smoke-test with `CHATUI_TAG=$VERSION docker compose -f docker-compose.prod.yml up -d`