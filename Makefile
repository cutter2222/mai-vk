# Цели проекта. Запуск из корня репозитория.
.PHONY: setup gen-contracts check test lint typecheck fixtures organizer-data dev dev-real build build-mock e2e up down deploy clean

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

dev-real: ## интерфейс в рабочем режиме против локального API (этап 2)
	pnpm -C frontend dev

build: ## статический экспорт интерфейса для сервера (рабочий режим)
	pnpm -C frontend build

build-mock: ## статический экспорт в режиме заглушек (для e2e и демо без backend)
	pnpm -C frontend build:mock

e2e: build-mock ## сквозные тесты интерфейса в Chromium, Firefox и WebKit
	pnpm -C frontend test:e2e

up: ## собрать и поднять стек Docker Compose на этой машине (http://localhost:8080)
	./deploy/deploy.sh --target local

down: ## остановить локальный стек; тома остаются
	./deploy/deploy.sh --target local --down

deploy: ## выложить на сервер из deploy/server.env
	./deploy/deploy.sh --target server

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache frontend/.next frontend/out
