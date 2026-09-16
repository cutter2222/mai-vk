#!/usr/bin/env bash
# Резервная копия стека: база снимком, загрузки, артефакты, Valkey — одним архивом
# в $SERVER_DIR/backups (сервер) или backups/ репозитория (локальный стек); рядом копия .env.
# Старые копии сверх backup.keep из config/app.yaml удаляются.
set -euo pipefail
# shellcheck source=deploy/lib.sh
. "$(dirname "$0")/lib.sh"

usage() {
  cat <<USAGE
Использование: deploy/backup.sh --target server|local [--label МЕТКА] [--fetch] [--list]

  server   создать копию на сервере из deploy/server.env: \$SERVER_DIR/backups/backup-<время>[-метка].tar
  local    то же для локального стека (make up): backups/ в корне репозитория
  --label  метка в имени копии (например, before-demo)
  --fetch  скачать созданную копию и её .env в backups/ на этой машине (для переезда)
  --list   только показать имеющиеся копии
USAGE
}

parse_target "$@"
LABEL=""
FETCH="no"
LIST="no"
set -- "${REST_ARGS[@]+"${REST_ARGS[@]}"}"
while [ $# -gt 0 ]; do
  case "$1" in
    --label) [ $# -ge 2 ] || die "--label требует значение"; LABEL="$2"; shift 2 ;;
    --label=*) LABEL="${1#--label=}"; shift ;;
    --fetch) FETCH="yes"; shift ;;
    --list) LIST="yes"; shift ;;
    *) usage >&2; die "неизвестный аргумент: $1" ;;
  esac
done

case "$TARGET" in
  server)
    load_server_env
    if [ "$LIST" = yes ]; then
      remote_bash "$DEPLOY_DIR/remote/maintenance.sh" "$SERVER_DIR" "$SERVER_URL" list
      exit 0
    fi
    out="$(remote_bash "$DEPLOY_DIR/remote/maintenance.sh" "$SERVER_DIR" "$SERVER_URL" backup "$LABEL" | tee /dev/stderr)"
    archive="$(printf '%s\n' "$out" | sed -n 's/^archive=//p')"
    name="$(basename "$archive" .tar)"
    if [ "$FETCH" = yes ]; then
      mkdir -p "$REPO_DIR/backups"
      log "скачивание $name.tar и $name.env в backups/"
      scp -q "$SERVER_SSH:$SERVER_DIR/backups/$name.tar" "$SERVER_SSH:$SERVER_DIR/backups/$name.env" "$REPO_DIR/backups/"
      chmod 0600 "$REPO_DIR/backups/$name.env"
    fi
    log "готово: $SERVER_DIR/backups/$name.tar"
    ;;
  local)
    [ -f "$REPO_DIR/.env" ] || die "нет .env: стек ещё не поднимался"
    if [ "$LIST" = yes ]; then
      compose_local run --rm --no-deps -T api python -m presentation_designer.cli.maintenance list-backups
      exit 0
    fi
    mkdir -p "$REPO_DIR/backups"
    compose_local run --rm --no-deps -T api python -m presentation_designer.cli.maintenance backup ${LABEL:+--label "$LABEL"}
    log "готово: backups/ в корне репозитория"
    ;;
esac
