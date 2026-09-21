#!/usr/bin/env bash
# Выкладка стека: синхронизация репозитория, сборка образов с тегом выпуска, миграция состояния,
# docker compose up и ожидание готовности; --rollback возвращает предыдущий выпуск одной командой.
# --target server работает по deploy/server.env; --target local поднимает тот же compose на этой машине.
set -euo pipefail
# shellcheck source=deploy/lib.sh
. "$(dirname "$0")/lib.sh"

usage() {
  cat <<USAGE
Использование: deploy/deploy.sh --target server|local [--down] [--build-here] [--rollback]

  server        отправить репозиторий (только файлы по .gitignore, без .git, планов и секретов)
                в \$SERVER_DIR/repo, собрать образы с тегом <commit>-<время>, применить миграции
                (перед изменением схемы — резервная копия), поднять стек с базовым и прод-файлами
                Compose и дождаться status ok в /api/health по SERVER_URL.
  local         собрать и поднять стек на этой машине по адресу $LOCAL_URL (переменная LOCAL_URL).
  --down        остановить контейнеры выбранной цели; тома и данные остаются.
  --build-here  собрать образы на этой машине под платформу сервера и передать их через
                docker save/load — для серверов, которым не хватает памяти на сборку.
  --rollback    только server: вернуть предыдущий выпуск (образы и compose-файлы из releases/,
                при изменённой схеме — базу из копии перед миграцией) и дождаться готовности.
USAGE
}

parse_target "$@"
ACTION="up"
BUILD_HERE="no"
for arg in "${REST_ARGS[@]+"${REST_ARGS[@]}"}"; do
  case "$arg" in
    --down) ACTION="down" ;;
    --rollback) ACTION="rollback" ;;
    --build-here) BUILD_HERE="yes" ;;
    *) usage >&2; die "неизвестный аргумент: $arg" ;;
  esac
done

git_state
TAG="$(release_tag)"

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
  local arch platform image root
  arch="$(ssh -o BatchMode=yes "$SERVER_SSH" uname -m)"
  case "$arch" in
    x86_64) platform="linux/amd64" ;;
    aarch64|arm64) platform="linux/arm64" ;;
    *) die "неизвестная архитектура сервера: $arch" ;;
  esac
  log "сборка образов $TAG под $platform на этой машине"
  root="$(compose_root)"
  DOCKER_DEFAULT_PLATFORM="$platform" PD_IMAGE_TAG="$TAG" docker compose -f "$root/docker/compose.yaml" build
  for image in $(PD_IMAGE_TAG="$TAG" docker compose -f "$root/docker/compose.yaml" config --images); do
    case "$image" in
      presentation-designer/*)
        log "передача $image"
        docker save "$image" | gzip | ssh -o BatchMode=yes "$SERVER_SSH" 'gunzip | docker load'
        ;;
    esac
  done
}

show_failure() {
  log "адрес не ответил; последние строки логов:"
  "$@" logs --tail=30 caddy api worker-analysis worker-generation || true
  exit 1
}

server_compose() {
  ssh -o BatchMode=yes "$SERVER_SSH" "docker compose -f $(printf '%q' "$SERVER_DIR")/repo/docker/compose.yaml -f $(printf '%q' "$SERVER_DIR")/repo/docker/compose.prod.yaml --env-file $(printf '%q' "$SERVER_DIR")/.env $*"
}

finish_server() {
  log "ожидание $SERVER_URL (сертификат может занять до минуты)"
  wait_for_url "$SERVER_URL/" 60 || show_failure server_compose
  log "готовность API: $SERVER_URL/api/health → status ok (воркеры регистрируются и проверяют рендерер)"
  wait_for_health_ok "$SERVER_URL" 60 || { log "health: $HEALTH_BODY"; show_failure server_compose; }
  log "health: $HEALTH_BODY"
  if ! curl -fs --max-time 10 -o /dev/null "$SERVER_URL/"; then
    log "внимание: с этой машины $SERVER_URL не отвечает, хотя с сервера доступен — проверьте VPN, прокси или DNS-кэш"
  fi
}

case "$TARGET" in
  server)
    load_server_env
    ssh -o BatchMode=yes -o ConnectTimeout=15 "$SERVER_SSH" "test -d $(printf '%q' "$SERVER_DIR")/repo" \
      || die "на $SERVER_SSH нет $SERVER_DIR/repo: сначала deploy/install.sh --target server"
    case "$ACTION" in
      down)
        remote_bash "$DEPLOY_DIR/remote/up.sh" "$SERVER_DIR" "$SERVER_URL" "$SITE_ADDRESS" down
        log "стек остановлен"
        exit 0
        ;;
      rollback)
        [ "$BUILD_HERE" = no ] || die "--rollback не сочетается с --build-here"
        remote_bash "$DEPLOY_DIR/remote/up.sh" "$SERVER_DIR" "$SERVER_URL" "$SITE_ADDRESS" rollback
        finish_server
        log "откат выполнен: $SERVER_URL"
        exit 0
        ;;
    esac
    sync_repo
    build_mode="build"
    if [ "$BUILD_HERE" = yes ]; then
      build_here_and_push
      build_mode="no-build"
    fi
    remote_bash "$DEPLOY_DIR/remote/up.sh" "$SERVER_DIR" "$SERVER_URL" "$SITE_ADDRESS" up "$build_mode" "$COMMIT" "$DIRTY" "$BRANCH" "$TAG"
    finish_server
    log "выложено: $SERVER_URL (выпуск $TAG, commit $COMMIT, незакоммиченные изменения: $DIRTY); откат: deploy/deploy.sh --target server --rollback"
    ;;
  local)
    if [ "$ACTION" = down ]; then
      [ -f "$REPO_DIR/.env" ] || die "нет .env: стек ещё не поднимался"
      compose_local down --remove-orphans
      log "стек остановлен"
      exit 0
    fi
    [ "$ACTION" = up ] || die "--rollback имеет смысл только для --target server"
    [ "$BUILD_HERE" = no ] || die "--build-here имеет смысл только для --target server"
    docker info >/dev/null 2>&1 || die "Docker не запущен"
    [ -f "$REPO_DIR/.env" ] || cp "$REPO_DIR/config/.env.example" "$REPO_DIR/.env"
    if ! grep -qE '^PD_ONLYOFFICE__JWT_SECRET=.{32,}$' "$REPO_DIR/.env"; then
      set_env_key "$REPO_DIR/.env" PD_ONLYOFFICE__JWT_SECRET "$(openssl rand -hex 32)"
    fi
    host_port="${LOCAL_URL#http://}"
    local_host="${host_port%%:*}"
    local_port="${host_port#*:}"
    [ "$local_port" != "$host_port" ] || local_port=80
    set_env_key "$REPO_DIR/.env" PD_PUBLIC_URL "$LOCAL_URL"
    set_env_key "$REPO_DIR/.env" PD_SITE_ADDRESS "http://$local_host"
    set_env_key "$REPO_DIR/.env" PD_HTTP_PORT "127.0.0.1:$local_port"
    set_env_key "$REPO_DIR/.env" PD_HTTPS_PORT "127.0.0.1:8443"
    set_env_key "$REPO_DIR/.env" PD_BUILD_COMMIT "$COMMIT"
    mkdir -p "$REPO_DIR/backups"
    log "сборка и запуск (commit $COMMIT, незакоммиченные изменения: $DIRTY)"
    compose_local build
    compose_local up -d --remove-orphans
    wait_for_url "$LOCAL_URL/" 20 || show_failure compose_local
    wait_for_health_ok "$LOCAL_URL" 40 || { log "health: $HEALTH_BODY"; show_failure compose_local; }
    log "health: $HEALTH_BODY"
    compose_local ps
    log "поднято: $LOCAL_URL"
    ;;
esac
