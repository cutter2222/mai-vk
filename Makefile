# Цели проекта. Запуск из корня репозитория.
.PHONY: setup gen-contracts check test lint typecheck fixtures organizer-data dev dev-real api build build-mock e2e test-server up down deploy rollback backup restore-verify cold-start llm-probe llm-smoke llm-limiter-check llm-test-valkey clean

setup: ## установить зависимости Python и frontend
	uv sync
	pnpm -C frontend install --frozen-lockfile

gen-contracts: ## сгенерировать Pydantic-модели и TypeScript-типы из contracts/schemas
	./scripts/gen_contracts.sh

lint:
	uv run ruff check .
	uv run ruff format --check .
	pnpm -C frontend lint

typecheck:
	uv run mypy
	pnpm -C frontend typecheck

test:
	uv run pytest -q

check: ## полная проверка: контракты, линтеры, типы, тесты
	uv run python contracts/validate.py
	$(MAKE) lint
	$(MAKE) typecheck
	$(MAKE) test

fixtures: ## пересоздать собственные тестовые файлы
	uv run python tests/fixtures/make_fixtures.py

organizer-data: ## скопировать материалы организаторов в data/organizers с манифестом
	uv run python scripts/organizer_data.py

dev: ## интерфейс в режиме заглушек (без backend)
	pnpm -C frontend dev:mock

dev-real: ## интерфейс в рабочем режиме: next dev проксирует /api на локальный API (API_PROXY, по умолчанию localhost:8000)
	pnpm -C frontend dev

api: ## локальный API без Docker: задачи выполняются сразу, Valkey не нужен
	PD_QUEUE_MODE=inline uv run python -m presentation_designer.api --port 8000

build: ## статический экспорт интерфейса для сервера (рабочий режим)
	pnpm -C frontend build

build-mock: ## статический экспорт в режиме заглушек (для e2e и демо без backend)
	pnpm -C frontend build:mock

e2e: build-mock ## сквозные тесты интерфейса в Chromium, Firefox и WebKit
	pnpm -C frontend test:e2e

# Проверки против развёрнутого стека: SERVER_URL по умолчанию из deploy/server.env, иначе локальный стек.
SERVER_URL ?= $(shell sed -n 's/^SERVER_URL=//p' deploy/server.env 2>/dev/null)
TARGET_URL ?= $(if $(SERVER_URL),$(SERVER_URL),http://localhost:8080)

test-server: ## интеграционные тесты API и сквозные тесты интерфейса против TARGET_URL
	API_BASE_URL=$(TARGET_URL) uv run pytest -q tests/integration
	PLAYWRIGHT_BASE_URL=$(TARGET_URL) pnpm -C frontend test:e2e

up: ## собрать и поднять стек Docker Compose на этой машине (http://localhost:8080)
	./deploy/deploy.sh --target local

down: ## остановить локальный стек; тома остаются
	./deploy/deploy.sh --target local --down

deploy: ## выложить на сервер из deploy/server.env (тег выпуска, миграция, готовность)
	./deploy/deploy.sh --target server

rollback: ## вернуть на сервере предыдущий выпуск (образы, compose-файлы, при смене схемы — базу)
	./deploy/deploy.sh --target server --rollback

backup: ## резервная копия сервера в $SERVER_DIR/backups (LABEL=метка, FETCH=1 — скачать в backups/)
	./deploy/backup.sh --target server $(if $(LABEL),--label $(LABEL)) $(if $(FETCH),--fetch)

restore-verify: ## проверить последнюю (или ARCHIVE=имя) копию на сервере в отдельном каталоге
	./deploy/restore.sh --target server --verify-only $(if $(ARCHIVE),--archive $(ARCHIVE))

cold-start: ## замер холодного запуска на сервере (MODE=down-up|restart, RUNS=N)
	./scripts/measure_cold_start.sh --target server --mode $(or $(MODE),down-up) --runs $(or $(RUNS),1)

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache frontend/.next frontend/out

# Адаптер моделей (этап 4). Зонд и smoke ходят в сеть: нужны PD_QWEN_BASE_URL и PD_QWEN_API_KEY в .env.
llm-probe: ## зонд провайдера с отчётом в docs/llm-capabilities.md
	uv run scripts/probe_llm.py probe --report docs/llm-capabilities.md

llm-smoke: ## один реальный вызов модели с этой машины
	uv run python -m presentation_designer.llm.probe smoke

llm-limiter-check: ## лимитер из нескольких процессов против Valkey (VALKEY_URL, по умолчанию отдельный контейнер на 6399)
	uv run python -m presentation_designer.llm.probe limiter --valkey-url $(or $(VALKEY_URL),redis://localhost:6399/0) --processes 4 --requests 6 --concurrency 2

llm-test-valkey: ## отдельный Valkey для тестов адаптера на порту 6399 (tests/llm пропускают его вариант без него)
	docker run -d --rm --name pd-test-valkey -p 6399:6379 valkey/valkey:8.1-alpine
