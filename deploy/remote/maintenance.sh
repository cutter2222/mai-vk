#!/usr/bin/env bash
# Выполняется на сервере через deploy/backup.sh и deploy/restore.sh (после deploy/remote/lib.sh).
# Действия:
#   backup [метка]                 резервная копия в $SERVER_DIR/backups (база снимком, загрузки,
#                                  артефакты, Valkey) плюс копия .env сервера рядом с архивом;
#   verify <архив>                 распаковать в backups/.verify и сверить ссылки и манифесты;
#   restore <архив> [--only-db] [--valkey restore|flush|keep]
#                                  остановить API и воркеры, восстановить, поднять стек;
#   gc [--dry-run] [--grace-hours H]  сборка мусора вручную;
#   list                           список копий.
# Аргументы: SERVER_DIR SERVER_URL действие [параметры действия]. Архив — имя файла в backups/.
# Скрипт приходит по stdin (bash -s): тело в main, stdin команд закрыт.

container_path() {
  # Путь архива внутри контейнера API: backups/ смонтирован как /app/backups.
  printf '/app/backups/%s' "$(basename "$1")"
}

main() {
  local server_dir="${1:?SERVER_DIR}"
  local server_url="${2:?SERVER_URL}"
  local action="${3:?действие}"
  shift 3
  local repo="$server_dir/repo" env_file="$server_dir/.env" out archive name

  [ -d "$repo/docker" ] || rdie "в $repo нет репозитория, сначала deploy/deploy.sh --target server"
  case "$action" in
    backup)
      out="$(maintenance "$repo" "$env_file" backup ${1:+--label "$1"})"
      printf '%s\n' "$out"
      archive="$(printf '%s\n' "$out" | sed -n 's/^archive=//p')"
      name="$(basename "$archive" .tar)"
      # .env сервера (адреса, ключи провайдера) хранится рядом с архивом, а не внутри него.
      install -m 0600 "$env_file" "$server_dir/backups/$name.env"
      rlog "копия: $server_dir/backups/$name.tar, окружение: $name.env"
      ;;
    verify)
      maintenance "$repo" "$env_file" verify "$(container_path "${1:?архив}")"
      ;;
    restore)
      archive="$(container_path "${1:?архив}")"
      shift
      [ -f "$server_dir/backups/$(basename "$archive")" ] || rdie "нет архива $(basename "$archive") в $server_dir/backups"
      rlog "остановка API и воркеров"
      compose_in "$repo" "$env_file" stop api worker-analysis worker-generation
      maintenance "$repo" "$env_file" restore "$archive" "$@"
      rlog "запуск стека"
      compose_in "$repo" "$env_file" up -d --remove-orphans --no-build
      wait_health_ok "$server_url" 60 >/dev/null || rdie "после восстановления /api/health не стал ok"
      rlog "восстановлено, health ok"
      ;;
    gc)
      maintenance "$repo" "$env_file" gc "$@"
      ;;
    list)
      maintenance "$repo" "$env_file" list-backups
      ;;
    *) rdie "неизвестное действие $action" ;;
  esac
}

main "$@" </dev/null
