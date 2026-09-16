# syntax=docker/dockerfile:1
# Статический экспорт интерфейса Next.js, который отдаёт Caddy внутри стека.
# Контекст сборки — каталог frontend/. Режим API фиксируется при сборке (ARG API_MODE).

FROM node:22.23.2-bookworm-slim AS build
WORKDIR /app
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
# Версия pnpm берётся из поля packageManager, чтобы совпадать с lock-файлом.
RUN npm install -g "pnpm@$(node -p "require('./package.json').packageManager.split('@')[1]")" \
    && pnpm install --frozen-lockfile
COPY . .
ARG API_MODE=mock
ENV NEXT_PUBLIC_API_MODE=$API_MODE NEXT_TELEMETRY_DISABLED=1
RUN pnpm build

FROM caddy:2.11.4-alpine
COPY --from=build /app/out /srv
# Страницы экспорта лежат как project.html, templates.html: адрес /project?id=… ведёт на project.html.
COPY <<'CADDYFILE' /etc/caddy/Caddyfile
:8080
root * /srv
encode zstd gzip
@immutable path /_next/static/*
header @immutable Cache-Control "public, max-age=31536000, immutable"
@revalidate not path /_next/static/*
header @revalidate Cache-Control "no-cache"
try_files {path} {path}.html
handle_errors 404 {
	rewrite * /404.html
	file_server
}
file_server
CADDYFILE
RUN caddy validate --config /etc/caddy/Caddyfile
EXPOSE 8080
