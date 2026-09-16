#!/usr/bin/env bash
# Восстановление из резервной копии: проверка в отдельный каталог (--verify-only) или
# восстановление на место при остановленных API и воркерах с последующим запуском стека.
set -euo pipefail
# shellcheck source=deploy/lib.sh
. "$(dirname "$0")/lib.sh"

usage() {
  cat <<USAGE
Использование: deploy/restore.sh --target server|local [--archive ИМЯ|ПУТЬ] [--verify-only]
                                 [--only-db] [--valkey restore|flush|keep]

  --archive      имя копии в backups/ цели или путь к локальному файлу .tar: локальный файл
                 сначала загружается на сервер (переезд на чистую машину). По умолчанию — последняя копия.
  --verify-only  распаковать копию в backups/.verify и сверить ссылки файлов и манифесты
                 артефактов, живые данные не трогать.
  --only-db      восстановить только базу (откат схемы); загрузки и артефакты остаются.
  --valkey       очередь: restore — из копии (по умолчанию), flush — очистить, keep — не трогать.

Текущие данные при восстановлении откладываются в data/.previous-<время> и artifacts/.previous-<время>.
USAGE
}

parse_target "$@"
ARCHIVE=""
VERIFY_ONLY="no"
EXTRA=()
set -- "${REST_ARGS[@]+"${REST_ARGS[@]}"}"
while [ $# -gt 0 ]; do
  case "$1" in
    --archive) [ $# -ge 2 ] || die "--archive требует значение"; ARCHIVE="$2"; shift 2 ;;
    --archive=*) ARCHIVE="${1#--archive=}"; shift ;;
    --verify-only) VERIFY_ONLY="yes"; shift ;;
    --only-db) EXTRA+=(--only-db); shift ;;
    --valkey) [ $# -ge 2 ] || die "--valkey требует значение"; EXTRA+=(--valkey "$2"); shift 2 ;;
    *) usage >&2; die "неизвестный аргумент: $1" ;;
  esac
done

latest_remote() {
  ssh -o BatchMode=yes "$SERVER_SSH" "ls -1 $(printf '%q' "$SERVER_DIR")/backups/backup-*.tar 2>/dev/null | sort | tail -1"
}

case "$TARGET" in
  server)
    load_server_env
    if [ -n "$ARCHIVE" ] && [ -f "$ARCHIVE" ]; then
      log "загрузка $(basename "$ARCHIVE") на сервер"
      scp -q "$ARCHIVE" "$SERVER_SSH:$SERVER_DIR/backups/"
      env_copy="${ARCHIVE%.tar}.env"
      [ -f "$env_copy" ] && scp -q "$env_copy" "$SERVER_SSH:$SERVER_DIR/backups/"
      ARCHIVE="$(basename "$ARCHIVE")"
    fi
    [ -n "$ARCHIVE" ] || ARCHIVE="$(basename "$(latest_remote)")"
    [ -n "$ARCHIVE" ] && [ "$ARCHIVE" != "." ] || die "на сервере нет резервных копий"
    if [ "$VERIFY_ONLY" = yes ]; then
      remote_bash "$DEPLOY_DIR/remote/maintenance.sh" "$SERVER_DIR" "$SERVER_URL" verify "$ARCHIVE"
      log "проверка $ARCHIVE завершена без замечаний"
    else
      remote_bash "$DEPLOY_DIR/remote/maintenance.sh" "$SERVER_DIR" "$SERVER_URL" restore "$ARCHIVE" "${EXTRA[@]+"${EXTRA[@]}"}"
      wait_for_health_ok "$SERVER_URL" 60 || die "после восстановления /api/health не стал ok: $HEALTH_BODY"
      log "восстановлено из $ARCHIVE: $SERVER_URL"
    fi
    ;;
  local)
    [ -f "$REPO_DIR/.env" ] || die "нет .env: стек ещё не поднимался"
    if [ -n "$ARCHIVE" ] && [ -f "$ARCHIVE" ] && [ "$(cd "$(dirname "$ARCHIVE")" && pwd)" != "$REPO_DIR/backups" ]; then
      cp "$ARCHIVE" "$REPO_DIR/backups/"
      ARCHIVE="$(basename "$ARCHIVE")"
    fi
    [ -n "$ARCHIVE" ] || ARCHIVE="$(find "$REPO_DIR/backups" -maxdepth 1 -name 'backup-*.tar' 2>/dev/null | sort | tail -1)"
    [ -n "$ARCHIVE" ] || die "в backups/ нет резервных копий"
    ARCHIVE="$(basename "$ARCHIVE")"
    if [ "$VERIFY_ONLY" = yes ]; then
      compose_local run --rm --no-deps -T api python -m presentation_designer.cli.maintenance verify "/app/backups/$ARCHIVE"
    else
      compose_local stop api worker-analysis worker-generation
      compose_local run --rm --no-deps -T api python -m presentation_designer.cli.maintenance restore "/app/backups/$ARCHIVE" "${EXTRA[@]+"${EXTRA[@]}"}"
      compose_local up -d --remove-orphans
      wait_for_health_ok "$LOCAL_URL" 40 || die "после восстановления /api/health не стал ok: $HEALTH_BODY"
      log "восстановлено из $ARCHIVE: $LOCAL_URL"
    fi
    ;;
esac
