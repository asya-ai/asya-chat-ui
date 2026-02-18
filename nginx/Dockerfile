FROM node:22-alpine AS frontend-build

WORKDIR /app
RUN corepack enable

COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY frontend/ ./
RUN pnpm build

FROM nginx:1.27-alpine

COPY nginx/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=frontend-build /app/dist /usr/share/nginx/html
