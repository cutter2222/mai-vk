#!/bin/sh
set -eu
# Never reuse stale V8 code cache after a container restart. The converter can
# regenerate it from the verified JS; binary snapshots are forbidden, not rebuilt.
python3 /opt/mai-precision/patch.py \
    --verify-install /var/www/onlyoffice/documentserver/sdkjs/slide --clear-cache
exec /app/ds/run-document-server.sh "$@"