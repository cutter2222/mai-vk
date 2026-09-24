#!/usr/bin/env bash
# Веса GigaAM для голосового ввода — на сервер, в $SERVER_DIR/models (том сервиса asr).
# Веса не идут ни в Git, ни в образ: их готовит scripts/export_gigaam_onnx.py на машине
# разработчика, а этот скрипт переносит рабочий граф из manifest.json и мелкие файлы рядом
# (yaml, словарь, буферы признаков, манифест). Второй граф (fp32 при рабочем int8) не везётся.
#   ./deploy/asr-model.sh [каталог модели]      (по умолчанию models/gigaam/v3_e2e_ctc)
set -euo pipefail
# shellcheck source=deploy/lib.sh
. "$(dirname "$0")/lib.sh"

usage() { sed -n '2,6p' "$0" | sed 's/^# \{0,1\}//'; }
case "${1:-}" in -h|--help) usage; exit 0 ;; esac

MODEL_DIR="${1:-$REPO_DIR/models/gigaam/v3_e2e_ctc}"
MANIFEST="$MODEL_DIR/manifest.json"
[ -f "$MANIFEST" ] || die "нет $MANIFEST: сначала uv run scripts/export_gigaam_onnx.py"
load_server_env

GRAPH="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["graph"])' "$MANIFEST")"
[ -f "$MODEL_DIR/$GRAPH" ] || die "в манифесте рабочий граф $GRAPH, а файла нет"
NAME="$(basename "$MODEL_DIR")"
TARGET="$SERVER_DIR/models/gigaam/$NAME"

FILES=("$GRAPH" manifest.json)
for extra in "$NAME.yaml" tokenizer.model preprocessor.npz; do
  [ -f "$MODEL_DIR/$extra" ] && FILES+=("$extra")
done
log "веса $NAME → $SERVER_SSH:$TARGET ($GRAPH, $(du -h "$MODEL_DIR/$GRAPH" | cut -f1))"
ssh -o BatchMode=yes -o ConnectTimeout=15 "$SERVER_SSH" "mkdir -p '$TARGET'"
# Файлы читает контейнер от пользователя сервиса: права на чтение всем, запись — владельцу.
(cd "$MODEL_DIR" && rsync -a --no-owner --no-group --chmod=Du=rwx,Dgo=rx,Fu=rw,Fgo=r --delete-after \
  --include-from=<(printf '%s\n' "${FILES[@]}") --exclude='*' \
  ./ "$SERVER_SSH:$TARGET/")
ssh -o BatchMode=yes "$SERVER_SSH" "chmod a+rx '$SERVER_DIR/models' '$SERVER_DIR/models/gigaam' && ls -la '$TARGET'"
# Работающий сервис перечитает манифест со следующей загрузки; перезапуск сбрасывает модель в памяти.
ssh -o BatchMode=yes "$SERVER_SSH" "docker restart presentation-designer-asr-1 >/dev/null 2>&1 && echo 'сервис asr перезапущен' || echo 'сервиса asr ещё нет: веса подхватит выкладка'"
log "готово: веса на месте, проверка — curl \$SERVER_URL/api/capabilities (features.speech)"
