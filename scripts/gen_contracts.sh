#!/usr/bin/env bash
# Генерирует Pydantic-модели и TypeScript-типы из contracts/schemas.
# Запуск из корня: make gen-contracts или ./scripts/gen_contracts.sh
set -euo pipefail
cd "$(dirname "$0")/.."
uv run python scripts/gen_contracts.py
pnpm -C frontend gen-types
