#!/usr/bin/env bash
# Выполняется на сервере через deploy/deploy.sh --target server после синхронизации репозитория.
# Обновляет управляемые строки .env сервера, собирает образы и поднимает стек Compose;
# действие down останавливает контейнеры, тома остаются.
# Аргументы: SERVER_DIR SERVER_URL адрес_сайта_Caddy действие(up|down) сборка(build|no-build) commit dirty branch
# Скрипт приходит по stdin (bash -s): тело в main, stdin команд закрыт.
set -euo pipefail

set_env_key() {
  local file="$1" key="$2" value="$3" tmp
  tmp="$(mktemp)"
  grep -v "^${key}=" "$file" > "$tmp" || true
  printf '%s=%s\n' "$key" "$value" >> "$tmp"
  cat "$tmp" > "$file"
  rm -f "$tmp"
}

main() {
  local server_dir="${1:?SERVER_DIR}"
  local server_url="${2:?SERVER_URL}"
  local site_address="${3:?адрес сайта Caddy}"
  local action="${4:-up}"
  local build="${5:-build}"
  local commit="${6:-unknown}"
  local dirty="${7:-unknown}"
  local branch="${8:-unknown}"
  local repo="$server_dir/repo"
  local env_file="$server_dir/.env"

  [ -d "$repo/docker" ] || { echo "ошибка: в $repo нет репозитория, сначала deploy/install.sh и синхронизация" >&2; exit 1; }
  [ -f "$env_file" ] || cp "$repo/config/.env.example" "$env_file"

  compose() { docker compose -f "$repo/docker/compose.yaml" --env-file "$env_file" "$@"; }

  if [ "$action" = down ]; then
    compose down --remove-orphans
    return 0
  fi

  # Адрес не зашит в образы: Caddy читает его отсюда, интерфейс ходит в API того же origin.
  set_env_key "$env_file" PD_PUBLIC_URL "$server_url"
  set_env_key "$env_file" PD_SITE_ADDRESS "$site_address"
  set_env_key "$env_file" PD_HTTP_PORT 80
  set_env_key "$env_file" PD_HTTPS_PORT 443

  if [ "$build" = build ]; then
    compose build --pull
    compose up -d --remove-orphans
  else
    # Образы уже загружены с машины разработчика через docker load.
    compose up -d --remove-orphans --no-build --force-recreate
  fi

  printf '%s\tcommit=%s\tdirty=%s\tbranch=%s\turl=%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$commit" "$dirty" "$branch" "$server_url" >> "$server_dir/deploys.log"
  compose ps
}

main "$@" </dev/null
