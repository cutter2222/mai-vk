# Цели проекта. Запуск из корня репозитория.
.PHONY: setup gen-contracts check test lint typecheck fixtures organizer-data dev dev-real build build-mock e2e up down clean

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

# Ниже цели, которые подключаются на этапах 1–3. До этого они сообщают о статусе.
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

up:
	@echo "make up: Docker Compose подключается на этапе 2."; exit 1

down:
	@echo "make down: Docker Compose подключается на этапе 2."; exit 1

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache frontend/.next frontend/out
