# syntax=docker/dockerfile:1
# Build context: ./frontend (see docker-compose.yml). nginx/ is an additional context.
FROM node:22-alpine AS frontend-build

WORKDIR /app
RUN corepack enable
ENV CI=true

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN --mount=type=cache,id=chatui-pnpm-store,target=/root/.local/share/pnpm/store \
    pnpm install --frozen-lockfile

COPY . .
RUN pnpm build

FROM nginx:1.27-alpine

COPY --from=nginx_cfg http.conf /etc/nginx/conf.d/limit.conf
COPY --from=nginx_cfg nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=frontend-build /app/dist /usr/share/nginx/html
