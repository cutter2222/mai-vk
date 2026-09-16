#!/usr/bin/env bash
# Выкладка стека: синхронизация репозитория, сборка образов, docker compose up и ожидание готовности.
# --target server работает по deploy/server.env; --target local поднимает тот же compose на этой машине.
set -euo pipefail
# shellcheck source=deploy/lib.sh
. "$(dirname "$0")/lib.sh"

usage() {
  cat <<USAGE
Использование: deploy/deploy.sh --target server|local [--down] [--build-here]

  server        отправить репозиторий (только файлы по .gitignore, без .git, планов и секретов)
                в \$SERVER_DIR/repo, собрать образы на сервере, поднять стек и дождаться SERVER_URL.
  local         собрать и поднять стек на этой машине по адресу $LOCAL_URL (переменная LOCAL_URL).
  --down        остановить контейнеры выбранной цели; тома и данные остаются.
  --build-here  собрать образы на этой машине под платформу сервера и передать их через
                docker save/load — для серверов, которым не хватает памяти на сборку.
USAGE
}

parse_target "$@"
ACTION="up"
BUILD_HERE="no"
for arg in "${REST_ARGS[@]+"${REST_ARGS[@]}"}"; do
  case "$arg" in
    --down) ACTION="down" ;;
    --build-here) BUILD_HERE="yes" ;;
    *) usage >&2; die "неизвестный аргумент: $arg" ;;
  esac
done

git_state

# Отправляет на сервер ровно те файлы, которые видит git (отслеживаемые и новые, кроме .gitignore
# и .git/info/exclude): без .git, локальных планов, материалов организаторов и .env.
sync_repo() {
  local list count tar_flags=()
  list="$(mktemp)"
  git -C "$REPO_DIR" ls-files -z --cached --others --exclude-standard \
    | while IFS= read -r -d '' path; do
        if [ -f "$REPO_DIR/$path" ]; then printf '%s\0' "$path"; fi
      done > "$list"
  count="$(tr -cd '\0' < "$list" | wc -c | tr -d ' ')"
  if tar --version 2>/dev/null | grep -q bsdtar; then
    tar_flags=(--no-xattrs --no-mac-metadata)
  fi
  log "синхронизация $count файлов в $SERVER_SSH:$SERVER_DIR/repo (commit $COMMIT, незакоммиченные изменения: $DIRTY)"
  COPYFILE_DISABLE=1 tar -C "$REPO_DIR" "${tar_flags[@]+"${tar_flags[@]}"}" --null -T "$list" -czf - \
    | ssh -o BatchMode=yes "$SERVER_SSH" "set -e
      d=$(printf '%q' "$SERVER_DIR")
      rm -rf \"\$d/repo.new\" \"\$d/repo.old\"
      mkdir -p \"\$d/repo.new\"
      tar -xzf - -C \"\$d/repo.new\"
      if [ -d \"\$d/repo\" ]; then mv \"\$d/repo\" \"\$d/repo.old\"; fi
      mv \"\$d/repo.new\" \"\$d/repo\"
      rm -rf \"\$d/repo.old\""
  rm -f "$list"
}

# Сборка на машине разработчика под платформу сервера и передача образов через docker load.
build_here_and_push() {
  local arch platform image
  arch="$(ssh -o BatchMode=yes "$SERVER_SSH" uname -m)"
  case "$arch" in
    x86_64) platform="linux/amd64" ;;
    aarch64|arm64) platform="linux/arm64" ;;
    *) die "неизвестная архитектура сервера: $arch" ;;
  esac
  log "сборка образов под $platform на этой машине"
  DOCKER_DEFAULT_PLATFORM="$platform" docker compose -f "$COMPOSE_FILE" build --pull
  for image in $(docker compose -f "$COMPOSE_FILE" config --images); do
    case "$image" in
      presentation-designer/*)
        log "передача $image"
        docker save "$image" | gzip | ssh -o BatchMode=yes "$SERVER_SSH" 'gunzip | docker load'
        ;;
    esac
  done
}

show_failure() {
  log "адрес не ответил; последние строки логов Caddy:"
  "$@" logs --tail=30 caddy || true
  exit 1
}

case "$TARGET" in
  server)
    load_server_env
    ssh -o BatchMode=yes -o ConnectTimeout=15 "$SERVER_SSH" "test -d $(printf '%q' "$SERVER_DIR")/repo" \
      || die "на $SERVER_SSH нет $SERVER_DIR/repo: сначала deploy/install.sh --target server"
    if [ "$ACTION" = down ]; then
      remote_bash "$DEPLOY_DIR/remote/up.sh" "$SERVER_DIR" "$SERVER_URL" "$SITE_ADDRESS" down
      log "стек остановлен"
      exit 0
    fi
    sync_repo
    build_mode="build"
    if [ "$BUILD_HERE" = yes ]; then
      build_here_and_push
      build_mode="no-build"
    fi
    remote_bash "$DEPLOY_DIR/remote/up.sh" "$SERVER_DIR" "$SERVER_URL" "$SITE_ADDRESS" up "$build_mode" "$COMMIT" "$DIRTY" "$BRANCH"
    log "ожидание $SERVER_URL (сертификат может занять до минуты)"
    wait_for_url "$SERVER_URL/" 60 \
      || show_failure ssh -o BatchMode=yes "$SERVER_SSH" "docker compose -f $(printf '%q' "$SERVER_DIR")/repo/docker/compose.yaml --env-file $(printf '%q' "$SERVER_DIR")/.env"
    log "проверка API: $SERVER_URL/api/health"
    wait_for_url "$SERVER_URL/api/health" 30 \
      || show_failure ssh -o BatchMode=yes "$SERVER_SSH" "docker compose -f $(printf '%q' "$SERVER_DIR")/repo/docker/compose.yaml --env-file $(printf '%q' "$SERVER_DIR")/.env"
    health="$(ssh -o BatchMode=yes "$SERVER_SSH" "curl -fs --max-time 10 $(printf '%q' "$SERVER_URL")/api/health")"
    log "health: $health"
    if ! curl -fs --max-time 10 -o /dev/null "$SERVER_URL/"; then
      log "внимание: с этой машины $SERVER_URL не отвечает, хотя с сервера доступен — проверьте VPN, прокси или DNS-кэш"
    fi
    log "выложено: $SERVER_URL (commit $COMMIT, незакоммиченные изменения: $DIRTY)"
    ;;
  local)
    if [ "$ACTION" = down ]; then
      [ -f "$REPO_DIR/.env" ] || die "нет .env: стек ещё не поднимался"
      compose_local down --remove-orphans
      log "стек остановлен"
      exit 0
    fi
    [ "$BUILD_HERE" = no ] || die "--build-here имеет смысл только для --target server"
    docker info >/dev/null 2>&1 || die "Docker не запущен"
    [ -f "$REPO_DIR/.env" ] || cp "$REPO_DIR/config/.env.example" "$REPO_DIR/.env"
    host_port="${LOCAL_URL#http://}"
    local_host="${host_port%%:*}"
    local_port="${host_port#*:}"
    [ "$local_port" != "$host_port" ] || local_port=80
    set_env_key "$REPO_DIR/.env" PD_PUBLIC_URL "$LOCAL_URL"
    set_env_key "$REPO_DIR/.env" PD_SITE_ADDRESS "http://$local_host"
    set_env_key "$REPO_DIR/.env" PD_HTTP_PORT "$local_port"
    set_env_key "$REPO_DIR/.env" PD_HTTPS_PORT 8443
    log "сборка и запуск (commit $COMMIT, незакоммиченные изменения: $DIRTY)"
    compose_local build
    compose_local up -d --remove-orphans
    wait_for_url "$LOCAL_URL/" 20 || show_failure compose_local
    wait_for_url "$LOCAL_URL/api/health" 30 || show_failure compose_local
    log "health: $(curl -fs --max-time 10 "$LOCAL_URL/api/health")"
    compose_local ps
    log "поднято: $LOCAL_URL"
    ;;
esac
