FROM node:22-bookworm

WORKDIR /app

COPY package.json pnpm-lock.yaml* ./
RUN corepack enable && pnpm install

COPY server.js ./

EXPOSE 3001

CMD ["pnpm", "start"]
