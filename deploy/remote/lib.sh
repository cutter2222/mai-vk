#!/usr/bin/env bash
# Общие функции удалённых скриптов deploy/remote/*.sh. deploy/lib.sh (remote_bash) отправляет
# этот файл на сервер вместе со скриптом, поэтому отдельно он не запускается и не source'ится.

set -euo pipefail

rlog() { printf '\033[1m[server]\033[0m %s\n' "$*"; }
rdie() { printf 'ошибка: %s\n' "$*" >&2; exit 1; }

# Записывает KEY=VALUE в env-файл: заменяет существующую строку или добавляет новую.
set_env_key() {
  local file="$1" key="$2" value="$3" tmp
  tmp="$(mktemp)"
  grep -v "^${key}=" "$file" > "$tmp" || true
  printf '%s=%s\n' "$key" "$value" >> "$tmp"
  cat "$tmp" > "$file"
  rm -f "$tmp"
}

env_value() {
  local file="$1" key="$2"
  sed -n "s/^${key}=//p" "$file" | tail -1
}

# Значение поля key=value из строки журнала выкладок (поля разделены табуляцией).
field() {
  local line="$1" key="$2"
  printf '%s' "$line" | tr '\t' '\n' | sed -n "s/^${key}=//p" | head -1
}

# docker compose с базовым и прод-файлами каталога репозитория $1 и .env сервера $2.
# У старых выкладок прод-файла может не быть — тогда только базовый.
compose_in() {
  local repo="$1" env_file="$2"
  shift 2
  local files=(-f "$repo/docker/compose.yaml")
  [ -f "$repo/docker/compose.prod.yaml" ] && files+=(-f "$repo/docker/compose.prod.yaml")
  docker compose "${files[@]}" --env-file "$env_file" "$@"
}

# Ждёт, пока /api/health не ответит status ok: все воркеры зарегистрированы, рендерер проверен.
# $1 — базовый адрес, $2 — попыток по 3 секунды.
wait_health_ok() {
  local url="$1" attempts="${2:-60}" i body
  for ((i = 1; i <= attempts; i++)); do
    body="$(curl -fs --max-time 10 "$url/api/health" 2>/dev/null || true)"
    case "$body" in
      *'"status":"ok"'*) printf '%s\n' "$body"; return 0 ;;
    esac
    printf '.'
    sleep 3
  done
  echo
  printf '%s\n' "$body"
  return 1
}

# Обслуживание в контейнере API текущего (или заданного через PD_IMAGE_TAG) образа:
# компоненты не поднимаются (--no-deps), stdin закрыт (-T), контейнер удаляется.
maintenance() {
  local repo="$1" env_file="$2"
  shift 2
  compose_in "$repo" "$env_file" run --rm --no-deps -T api \
    python -m presentation_designer.cli.maintenance "$@"
}
