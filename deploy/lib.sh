#!/usr/bin/env bash
# Общие функции скриптов deploy/. Подключается через source, отдельно не запускается.

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$DEPLOY_DIR/.." && pwd)"
# shellcheck disable=SC2034  # используется deploy.sh
COMPOSE_FILE="$REPO_DIR/docker/compose.yaml"
SERVER_ENV_FILE="$DEPLOY_DIR/server.env"

# Локальный запуск: адрес без TLS, localhost для браузера считается защищённым контекстом.
LOCAL_URL="${LOCAL_URL:-http://localhost:8080}"

log() { printf '\033[1m[%s]\033[0m %s\n' "$(basename "$0" .sh)" "$*"; }
die() { printf 'ошибка: %s\n' "$*" >&2; exit 1; }

# --target server|local из аргументов; остальные аргументы возвращаются в REST_ARGS.
parse_target() {
  TARGET=""
  REST_ARGS=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --target) [ $# -ge 2 ] || die "--target требует значение server или local"; TARGET="$2"; shift 2 ;;
      --target=*) TARGET="${1#--target=}"; shift ;;
      -h|--help) usage; exit 0 ;;
      *) REST_ARGS+=("$1"); shift ;;
    esac
  done
  case "$TARGET" in
    server|local) ;;
    "") usage >&2; die "укажите --target server или --target local" ;;
    *) die "неизвестная цель '$TARGET': допустимы server и local" ;;
  esac
}

# Читает deploy/server.env и проверяет обязательные переменные.
load_server_env() {
  [ -f "$SERVER_ENV_FILE" ] || die "нет $SERVER_ENV_FILE: скопируйте deploy/server.env.example и заполните"
  set -a
  # shellcheck disable=SC1090
  . "$SERVER_ENV_FILE"
  set +a
  local name
  for name in SERVER_SSH SERVER_DIR SERVER_URL; do
    [ -n "${!name:-}" ] || die "в deploy/server.env не задана переменная $name"
  done
  case "$SERVER_URL" in
    https://*) ;;
    *) die "SERVER_URL должен начинаться с https://, иначе браузер не зарегистрирует service worker" ;;
  esac
  SERVER_URL="${SERVER_URL%/}"
  # Адрес сайта для Caddy: основной и запасные через запятую.
  SITE_ADDRESS="$SERVER_URL"
  local alias
  for alias in ${SERVER_ALIAS_URLS//,/ }; do
    case "$alias" in
      https://*) SITE_ADDRESS="$SITE_ADDRESS, ${alias%/}" ;;
      *) die "в SERVER_ALIAS_URLS допустимы только адреса https://…: $alias" ;;
    esac
  done
  case "$SERVER_DIR" in
    /*) ;;
    *) die "SERVER_DIR должен быть абсолютным путём" ;;
  esac
}

# Выполняет локальный скрипт на сервере: bash читает его со stdin, аргументы экранированы.
# Перед скриптом отправляются общие функции deploy/remote/lib.sh.
remote_bash() {
  local script="$1"; shift
  local quoted=""
  [ $# -gt 0 ] && quoted="$(printf ' %q' "$@")"
  cat "$DEPLOY_DIR/remote/lib.sh" "$script" \
    | ssh -o BatchMode=yes -o ConnectTimeout=15 "$SERVER_SSH" "bash -s --$quoted"
}

# Тег выпуска для образов: commit, признак незакоммиченных изменений и время сборки.
release_tag() {
  local stamp
  stamp="$(date -u +%Y%m%d-%H%M%S)"
  if [ "$DIRTY" = yes ]; then
    printf '%s-dirty-%s' "$COMMIT" "$stamp"
  else
    printf '%s-%s' "$COMMIT" "$stamp"
  fi
}

# Состояние репозитория для отчёта выкладки: COMMIT, BRANCH и DIRTY (есть незакоммиченные изменения).
# shellcheck disable=SC2034  # переменные читают вызывающие скрипты
git_state() {
  COMMIT="$(git -C "$REPO_DIR" rev-parse --short=12 HEAD 2>/dev/null || echo unknown)"
  BRANCH="$(git -C "$REPO_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
  if [ -n "$(git -C "$REPO_DIR" status --porcelain 2>/dev/null)" ]; then
    DIRTY="yes"
  else
    DIRTY="no"
  fi
}

# Записывает KEY=VALUE в env-файл: заменяет существующую строку или добавляет новую.
set_env_key() {
  local file="$1" key="$2" value="$3" tmp
  tmp="$(mktemp)"
  if [ -f "$file" ]; then
    grep -v "^${key}=" "$file" > "$tmp" || true
  fi
  printf '%s=%s\n' "$key" "$value" >> "$tmp"
  cat "$tmp" > "$file"
  rm -f "$tmp"
}

# Ждёт, пока адрес не ответит 2xx/3xx; печатает точки, чтобы было видно ожидание.
# Для сервера запрос выполняется с него самого: так проверка не зависит от VPN и DNS-кэша
# машины разработчика; внешнюю доступность подтверждает выданный сертификат.
wait_for_url() {
  local url="$1" attempts="${2:-60}" i
  for ((i = 1; i <= attempts; i++)); do
    if probe_url "$url"; then
      echo
      return 0
    fi
    printf '.'
    sleep 3
  done
  echo
  return 1
}

probe_url() {
  if [ "${TARGET:-}" = server ]; then
    ssh -o BatchMode=yes "$SERVER_SSH" "curl -fs --max-time 10 -o /dev/null $(printf '%q' "$1")"
  else
    curl -fs --max-time 10 -o /dev/null "$1"
  fi
}

fetch_url() {
  if [ "${TARGET:-}" = server ]; then
    ssh -o BatchMode=yes "$SERVER_SSH" "curl -fs --max-time 10 $(printf '%q' "$1")"
  else
    curl -fs --max-time 10 "$1"
  fi
}

# Готовность после обновления: /api/health отвечает status ok — база, хранилище, Valkey,
# все роли воркеров и проверка рендерера. HTTP 200 с degraded готовностью не считается.
# Последний ответ остаётся в HEALTH_BODY для отчёта вызывающего скрипта.
# shellcheck disable=SC2034
wait_for_health_ok() {
  local url="$1" attempts="${2:-60}" i body=""
  for ((i = 1; i <= attempts; i++)); do
    body="$(fetch_url "$url/api/health" 2>/dev/null || true)"
    case "$body" in
      *'"status":"ok"'*) echo; HEALTH_BODY="$body"; return 0 ;;
    esac
    printf '.'
    sleep 3
  done
  echo
  HEALTH_BODY="$body"
  return 1
}

# Docker 29 не собирает образы, если в пути к контексту есть символы вне ASCII (ошибка BuildKit
# «header key ... contains value with non-printable ASCII characters»); локально работаем через ссылку.
compose_root() {
  if LC_ALL=C printf '%s' "$REPO_DIR" | grep -q '[^ -~]'; then
    local link="${TMPDIR:-/tmp}/presentation-designer-repo"
    ln -sfn "$REPO_DIR" "$link"
    printf '%s' "$link"
  else
    printf '%s' "$REPO_DIR"
  fi
}

compose_local() {
  local root
  root="$(compose_root)"
  docker compose -f "$root/docker/compose.yaml" --env-file "$root/.env" "$@"
}
