#!/usr/bin/env bash
# Подготовка среды: --target server ставит Docker и каталоги на сервере из deploy/server.env,
# --target local проверяет Docker на машине разработчика и создаёт локальный .env.
set -euo pipefail
# shellcheck source=deploy/lib.sh
. "$(dirname "$0")/lib.sh"

SERVICE_USER="${SERVICE_USER:-presentation-designer}"

usage() {
  cat <<USAGE
Использование: deploy/install.sh --target server|local

  server  установить Docker Engine и compose plugin, создать пользователя сервиса
          и каталоги \$SERVER_DIR/{repo,data,artifacts,runs,backups,releases} с .env сервера.
          Читает deploy/server.env. Повторный запуск безопасен.
  local   проверить Docker и compose plugin на этой машине, создать .env из config/.env.example.
USAGE
}

parse_target "$@"

case "$TARGET" in
  server)
    load_server_env
    log "сервер $SERVER_SSH, каталог $SERVER_DIR"
    remote_bash "$DEPLOY_DIR/remote/install.sh" "$SERVER_DIR" "$SERVICE_USER"
    log "готово; дальше deploy/deploy.sh --target server"
    ;;
  local)
    command -v docker >/dev/null 2>&1 || die "нет docker: установите Docker Desktop или Docker Engine"
    docker compose version >/dev/null 2>&1 || die "нет docker compose plugin"
    docker info >/dev/null 2>&1 || die "Docker не запущен"
    if [ ! -f "$REPO_DIR/.env" ]; then
      cp "$REPO_DIR/config/.env.example" "$REPO_DIR/.env"
      log "создан .env из config/.env.example"
    fi
    mkdir -p "$REPO_DIR/data" "$REPO_DIR/artifacts" "$REPO_DIR/runs"
    log "docker $(docker --version | sed 's/Docker version //'), compose $(docker compose version --short)"
    log "готово; дальше deploy/deploy.sh --target local (или make up)"
    ;;
esac
