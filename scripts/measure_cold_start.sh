#!/usr/bin/env bash
# Замер холодного запуска стека и первого запроса после рестарта (docs/evaluation.md).
#   scripts/measure_cold_start.sh --target server|local [--mode down-up|restart] [--runs N]
# down-up: контейнеры удаляются и создаются заново (docker compose down + up -d);
# restart: docker compose restart — образы и контейнеры на месте, процессы стартуют заново.
# Отсчёт идёт с команды compose; готовность — по этапам: интерфейс отдаёт /, API отвечает
# на /api/health, health переходит в status ok (все воркеры зарегистрированы, рендерер проверен).
# Потом измеряется первый и повторный GET /api/projects и первый GET /. Запросы выполняются
# с самой машины стека (на сервере — по SSH), чтобы не смешивать запуск с сетью до сервера.
# Результат — строка JSON в runs/cold-start/<время>.jsonl и строка таблицы для документа.
set -euo pipefail
# shellcheck source=deploy/lib.sh
. "$(dirname "$0")/../deploy/lib.sh"

usage() {
  cat <<USAGE
Использование: scripts/measure_cold_start.sh --target server|local [--mode down-up|restart] [--runs N]
USAGE
}

parse_target "$@"
MODE="down-up"
RUNS=1
set -- "${REST_ARGS[@]+"${REST_ARGS[@]}"}"
while [ $# -gt 0 ]; do
  case "$1" in
    --mode) MODE="$2"; shift 2 ;;
    --runs) RUNS="$2"; shift 2 ;;
    *) usage >&2; die "неизвестный аргумент: $1" ;;
  esac
done
case "$MODE" in down-up|restart) ;; *) die "--mode: down-up или restart" ;; esac

# Тело замера: выполняется на машине стека (bash -s). Аргументы: compose-команда, адрес, режим.
# shellcheck disable=SC2016
MEASURE='
set -euo pipefail
compose_cmd="$1"; url="$2"; mode="$3"
now() { date +%s.%N; }
elapsed() { awk -v a="$1" -v b="$2" "BEGIN { printf \"%.1f\", b - a }"; }
t0="$(now)"
case "$mode" in
  down-up) $compose_cmd down >/dev/null 2>&1; $compose_cmd up -d >/dev/null 2>&1 ;;
  restart) $compose_cmd restart >/dev/null 2>&1 ;;
esac
t_compose="$(now)"
until curl -fs -o /dev/null --max-time 5 "$url/"; do sleep 0.5; done
t_front="$(now)"
until curl -fs -o /dev/null --max-time 5 "$url/api/health"; do sleep 0.5; done
t_api="$(now)"
until curl -fs --max-time 5 "$url/api/health" | grep -q "\"status\":\"ok\""; do sleep 1; done
t_ok="$(now)"
first_projects="$(curl -s -o /dev/null -w "%{time_total}" "$url/api/projects")"
second_projects="$(curl -s -o /dev/null -w "%{time_total}" "$url/api/projects")"
first_page="$(curl -s -o /dev/null -w "%{time_total}" "$url/")"
printf "{\"mode\":\"%s\",\"compose_s\":%s,\"frontend_s\":%s,\"api_s\":%s,\"health_ok_s\":%s,\"first_projects_s\":%s,\"second_projects_s\":%s,\"first_page_s\":%s}\n" \
  "$mode" "$(elapsed "$t0" "$t_compose")" "$(elapsed "$t0" "$t_front")" "$(elapsed "$t0" "$t_api")" \
  "$(elapsed "$t0" "$t_ok")" "$first_projects" "$second_projects" "$first_page"
'

run_once() {
  case "$TARGET" in
    server)
      ssh -o BatchMode=yes "$SERVER_SSH" "bash -s -- $(printf '%q ' "docker compose -f $SERVER_DIR/repo/docker/compose.yaml -f $SERVER_DIR/repo/docker/compose.prod.yaml --env-file $SERVER_DIR/.env" "$SERVER_URL" "$MODE")" <<< "$MEASURE"
      ;;
    local)
      root="$(compose_root)"
      bash -s -- "docker compose -f $root/docker/compose.yaml --env-file $root/.env" "$LOCAL_URL" "$MODE" <<< "$MEASURE"
      ;;
  esac
}

if [ "$TARGET" = server ]; then load_server_env; where="$SERVER_URL"; else where="$LOCAL_URL"; fi
mkdir -p "$REPO_DIR/runs/cold-start"
out="$REPO_DIR/runs/cold-start/$(date -u +%Y%m%d-%H%M%S)-$TARGET-$MODE.jsonl"
log "цель $where, режим $MODE, прогонов $RUNS"
for ((i = 1; i <= RUNS; i++)); do
  line="$(run_once)"
  printf '%s\n' "$line" >> "$out"
  log "прогон $i: $line"
done
log "результаты: $out"
log "строка для docs/evaluation.md (mode | compose | интерфейс | API | health ok | первый /api/projects | повторный | первый /):"
python3 - "$out" <<'EOF'
import json, sys
for line in open(sys.argv[1]):
    r = json.loads(line)
    print(f"| {r['mode']} | {r['compose_s']} | {r['frontend_s']} | {r['api_s']} | {r['health_ok_s']} | {float(r['first_projects_s']):.2f} | {float(r['second_projects_s']):.2f} | {float(r['first_page_s']):.2f} |")
EOF
