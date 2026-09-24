#!/usr/bin/env bash
# Выполняется на сервере через deploy/deploy.sh --target server (после deploy/remote/lib.sh).
# Действия:
#   up       обновить .env, собрать образы с тегом выпуска, применить миграции (перед изменением
#            схемы — согласованная резервная копия), поднять стек, записать выпуск в deploys.log
#            и сохранить копию репозитория выпуска в releases/<тег> для отката;
#   down     остановить контейнеры, тома и данные остаются;
#   rollback вернуть предыдущий выпуск: его образы и compose-файлы, а если тот выпуск менял
#            схему — базу из копии, снятой перед миграцией (текущая база откладывается).
# Аргументы: SERVER_DIR SERVER_URL адрес_сайта действие сборка(build|no-build) commit dirty branch тег
# Скрипт приходит по stdin (bash -s): тело в main, stdin команд закрыт.

KEEP_RELEASES=3

managed_env() {
  local env_file="$1" server_dir="$2" server_url="$3" site_address="$4"
  if ! grep -qE '^PD_ONLYOFFICE__JWT_SECRET=.{32,}$' "$env_file"; then
    set_env_key "$env_file" PD_ONLYOFFICE__JWT_SECRET "$(openssl rand -hex 32)"
  fi
  # Адрес не зашит в образы: Caddy читает его отсюда, интерфейс ходит в API того же origin.
  set_env_key "$env_file" PD_PUBLIC_URL "$server_url"
  set_env_key "$env_file" PD_SITE_ADDRESS "$site_address"
  set_env_key "$env_file" PD_HTTP_PORT 80
  set_env_key "$env_file" PD_HTTPS_PORT 443
  # Данные живут вне репозитория, чтобы переживать замену каталога repo при выкладке.
  set_env_key "$env_file" PD_DATA_DIR "$server_dir/data"
  set_env_key "$env_file" PD_ARTIFACTS_DIR "$server_dir/artifacts"
  set_env_key "$env_file" PD_RUNS_DIR "$server_dir/runs"
  set_env_key "$env_file" PD_BACKUPS_DIR "$server_dir/backups"
  # Веса моделей, которые сервис держит сам (GigaAM для голосового ввода): make asr-model.
  set_env_key "$env_file" PD_MODELS_DIR "$server_dir/models"
  mkdir -p "$server_dir/models"
  set_env_key "$env_file" PD_ENV_FILE "$env_file"
}

log_release() {
  local server_dir="$1"
  shift
  printf '%s\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(printf '%s\t' "$@" | sed 's/\t$//')" \
    >> "$server_dir/deploys.log"
}

# Удаляет образы и копии репозитория выпусков старше последних KEEP_RELEASES.
prune_releases() {
  local server_dir="$1" keep_tags tag image
  keep_tags="$(sed -n 's/.*\ttag=\([^\t]*\).*/\1/p' "$server_dir/deploys.log" | awk '!seen[$0]++' | tail -n "$KEEP_RELEASES")"
  for dir in "$server_dir"/releases/*/; do
    [ -d "$dir" ] || continue
    tag="$(basename "$dir")"
    if ! printf '%s\n' "$keep_tags" | grep -qx "$tag"; then
      rm -rf "$dir"
      rlog "копия репозитория выпуска $tag удалена"
    fi
  done
  for image in $(docker images --format '{{.Repository}}:{{.Tag}}' 'presentation-designer/*' 2>/dev/null); do
    tag="${image##*:}"
    [ "$tag" = latest ] && continue
    if ! printf '%s\n' "$keep_tags" | grep -qx "$tag"; then
      docker image rm "$image" >/dev/null 2>&1 && rlog "образ $image удалён" || true
    fi
  done
  docker image prune -f >/dev/null 2>&1 || true
}

do_up() {
  local server_dir="$1" server_url="$2" site_address="$3" build="$4" commit="$5" dirty="$6" branch="$7" tag="$8"
  local repo="$server_dir/repo" env_file="$server_dir/.env"
  local previous schema_before schema_after backup migrate_out

  [ -d "$repo/docker" ] || rdie "в $repo нет репозитория, сначала deploy/install.sh и синхронизация"
  [ -f "$env_file" ] || cp "$repo/config/.env.example" "$env_file"
  managed_env "$env_file" "$server_dir" "$server_url" "$site_address"
  previous="$(env_value "$env_file" PD_IMAGE_TAG)"

  if [ "$build" = build ]; then
    # Базовые образы закреплены точными тегами, поэтому без --pull: принудительная проверка
    # метаданных упирается в лимит анонимных запросов Docker Hub (429), а свежести не добавляет.
    rlog "сборка образов $tag"
    PD_IMAGE_TAG="$tag" compose_in "$repo" "$env_file" build
  fi

  # Миграция состояния новым образом до up -d. При недостающих версиях схемы CLI сначала
  # делает полную резервную копию (база, загрузки, артефакты, Valkey) — к ней возвращает откат.
  rlog "миграция состояния"
  migrate_out="$(PD_IMAGE_TAG="$tag" PD_BUILD_COMMIT="$commit" maintenance "$repo" "$env_file" migrate --label "pre-migrate-$tag")"
  schema_before="$(printf '%s\n' "$migrate_out" | sed -n 's/^schema_before=//p')"
  schema_after="$(printf '%s\n' "$migrate_out" | sed -n 's/^schema_after=//p')"
  backup="$(printf '%s\n' "$migrate_out" | sed -n 's/^backup=//p')"
  rlog "схема базы: ${schema_before:-?} → ${schema_after:-?}${backup:+, копия перед миграцией: $backup}"

  set_env_key "$env_file" PD_IMAGE_TAG "$tag"
  set_env_key "$env_file" PD_BUILD_COMMIT "$commit"
  rlog "запуск стека $tag"
  if [ "$build" = build ]; then
    compose_in "$repo" "$env_file" up -d --remove-orphans
  else
    compose_in "$repo" "$env_file" up -d --remove-orphans --no-build
  fi
  # Caddyfile примонтирован в контейнер файлом, то есть по inode: после замены каталога repo
  # работающий Caddy держит старый файл, а compose его не пересоздаёт (описание сервиса не менялось).
  compose_in "$repo" "$env_file" up -d --force-recreate --no-deps --no-build caddy

  reanalyze_stale_templates "$repo" "$env_file"

  rm -rf "$server_dir/releases/$tag"
  mkdir -p "$server_dir/releases"
  cp -a "$repo" "$server_dir/releases/$tag"
  log_release "$server_dir" "action=deploy" "tag=$tag" "previous=${previous:-}" "commit=$commit" \
    "dirty=$dirty" "branch=$branch" "schema=${schema_before:-?}->${schema_after:-?}" \
    "backup=${backup:-}" "url=$server_url"
  prune_releases "$server_dir"
  compose_in "$repo" "$env_file" ps
}

do_rollback() {
  local server_dir="$1" server_url="$2"
  local repo="$server_dir/repo" env_file="$server_dir/.env"
  local current line target schema backup before after release_repo image

  current="$(env_value "$env_file" PD_IMAGE_TAG)"
  [ -n "$current" ] && [ "$current" != latest ] || rdie "в .env нет тега текущего выпуска, откатывать нечего"
  line="$(grep -F "	tag=$current	" "$server_dir/deploys.log" | tail -1 || true)"
  [ -n "$line" ] || rdie "в deploys.log нет записи о выпуске $current"
  target="$(field "$line" previous)"
  [ -n "$target" ] && [ "$target" != latest ] || rdie "у выпуска $current нет предыдущего: это первая выкладка с тегами"
  release_repo="$server_dir/releases/$target"
  [ -d "$release_repo/docker" ] || rdie "нет копии репозитория выпуска $target в releases/"
  for image in api worker frontend; do
    docker image inspect "presentation-designer/$image:$target" >/dev/null 2>&1 \
      || rdie "нет образа presentation-designer/$image:$target: откат невозможен"
  done

  schema="$(field "$line" schema)"
  backup="$(field "$line" backup)"
  before="${schema%%->*}"
  after="${schema##*->}"
  rlog "откат $current → $target (схема выпуска: $schema)"
  compose_in "$repo" "$env_file" stop api worker-analysis worker-generation
  if [ -n "$backup" ] && [ "$before" != "$after" ]; then
    # Выпуск менял схему: база возвращается к копии, снятой перед его миграцией; загрузки
    # и артефакты остаются (лишние станут сиротами для сборки мусора), очередь очищается.
    [ -f "$backup" ] || rdie "нет копии $backup для отката схемы"
    rlog "схема $before → $after: база восстанавливается из $backup"
    maintenance "$repo" "$env_file" restore "/app/backups/$(basename "$backup")" --only-db --valkey flush
  fi
  set_env_key "$env_file" PD_IMAGE_TAG "$target"
  set_env_key "$env_file" PD_BUILD_COMMIT "$(field "$(grep -F "	tag=$target	" "$server_dir/deploys.log" | head -1)" commit)"
  compose_in "$release_repo" "$env_file" up -d --remove-orphans --no-build
  compose_in "$release_repo" "$env_file" up -d --force-recreate --no-deps --no-build caddy
  reanalyze_stale_templates "$release_repo" "$env_file"
  log_release "$server_dir" "action=rollback" "tag=$target" "previous=$current" \
    "schema=$after->$before" "url=$server_url"
  compose_in "$release_repo" "$env_file" ps
}

# Профили шаблонов библиотеки принадлежат выпуску: ключ профиля включает версии анализатора,
# контрактов, скилла и шрифтов. После выкладки (и после отката) устаревшие профили ставятся
# на повторный анализ в очередь воркеров, иначе генерация на таком шаблоне падает при проверке
# профиля. Ждать здесь нечего: анализ идёт минуты, а результат виден в библиотеке.
reanalyze_stale_templates() {
  local repo="$1" env_file="$2" out
  if out="$(maintenance "$repo" "$env_file" reanalyze-templates 2>&1)"; then
    # Журнал команды идёт в stderr вперемешку с итогом, поэтому берутся только пары ключ=число.
    rlog "шаблоны библиотеки: $(printf '%s\n' "$out" | grep -oE '(templates|queued|up_to_date|failed)=[0-9]+' | tr '\n' ' ')"
  else
    rlog "повторный анализ шаблонов не запущен: $(printf '%s\n' "$out" | tail -3 | tr '\n' ' ')"
  fi
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
  local tag="${9:-latest}"
  local repo="$server_dir/repo" env_file="$server_dir/.env"

  case "$action" in
    down) compose_in "$repo" "$env_file" down --remove-orphans ;;
    up) do_up "$server_dir" "$server_url" "$site_address" "$build" "$commit" "$dirty" "$branch" "$tag" ;;
    rollback) do_rollback "$server_dir" "$server_url" ;;
    *) rdie "неизвестное действие $action" ;;
  esac
}

main "$@" </dev/null
