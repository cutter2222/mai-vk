# Цели проекта. Запуск из корня репозитория.
.PHONY: setup gen-contracts check test lint typecheck fixtures organizer-data dev build up down clean

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
dev:
	@echo "make dev: режим разработки подключается на этапе 1 (frontend) и этапе 2 (api)."; exit 1

build:
	pnpm -C frontend build

up:
	@echo "make up: Docker Compose подключается на этапе 2."; exit 1

down:
	@echo "make down: Docker Compose подключается на этапе 2."; exit 1

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache frontend/.next frontend/out
