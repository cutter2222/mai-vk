# CLAUDE.md — рабочие правила и состояние проекта

Цифровой дизайнер презентаций: по шаблону PPTX и материалам (или одной теме) строит презентацию
в трёх вариантах (compact, balanced, detailed), проверяет аудитом, открывает в ONLYOFFICE, правится
из чата. Обзор — `README.md`, устройство — `ARCHITECTURE.md`, модели — `MODELS.md`, проверки —
`AUDIT.md` (оба генерируются, руками не править).

## Правила работы

- Отвечать и писать коммиты по-русски, коротко и понятно.
- Коммиты — сразу в `main` (без веток и PR), автор git — Vasilisa-IT; после законченной задачи
  пользователь ждёт коммит и пуш. Перед пушем `git fetch` и проверка, что `main` не отстал.
- Docker: контейнеры `cheapai-*` не трогать; `lct-deckgen-*` и `mai-valkey` можно гасить.
- Node на машине нет: фронт собирается и e2e гоняются в контейнерах
  (`node:22-alpine`, `mcr.microsoft.com/playwright:v1.63.0-noble`); стенд обновляется
  `docker compose -f docker/compose.yaml --env-file .env build frontend && … up -d frontend`.
- Тесты не ходят в сеть: поиск в интернете и вызовы моделей в тестах подменяются или идут
  по записанным ответам (`tests/fixtures/llm`, режим replay).
- Ключи из `.env` не выводить (при `set -a; . ./.env` — вывод в `/dev/null`); после
  такой загрузки не запускать pytest в той же оболочке — переменные стенда ломают тесты.

## Команды

```bash
uv run pytest -q -m "not organizer_data"      # основной набор, ~2 мин (≈1180 тестов)
uv run pytest -q -m organizer_data            # шаблоны организаторов, ~2–4 мин
uv run ruff check . && uv run ruff format --check . && uv run mypy
uv run scripts/gen_models_md.py --check && uv run scripts/gen_audit_md.py --check
```

- mypy сейчас падает в `parsing/content/research.py` и `generation/story.py` (строки ~790):
  ошибки пришли с чужими коммитами, свои правки проверять по своим файлам.
- Смена промпта скилла: новый файл `skills/<skill>/prompts/<id>.vX.Y.Z.md`, версия и changelog
  в `skill.yaml`, `uv run scripts/gen_models_md.py`, версии в тестах; ответы модели для тестов
  перезаписать: `set -a && . ./.env && set +a && export PD_QUEUE_MODE=inline &&
  uv run python scripts/record_llm_fixtures.py --only story` (затем `--only plan`).

## Стенд

- `make up` — сборка и запуск на http://localhost:8080. Код в образе; чтобы правки `src`,
  `config`, `skills` подхватывались перезапуском, поднимать с отладочным файлом:
  `docker compose -f docker/compose.yaml -f docker/compose.dev.yaml --env-file .env up -d api worker-generation worker-analysis`,
  дальше `docker restart presentation-designer-api-1 presentation-designer-worker-generation-{1,2,3} presentation-designer-worker-analysis-1`.
- После смены `ANALYZER_VERSION`: `docker exec presentation-designer-api-1 python -m presentation_designer.cli.maintenance reanalyze-templates --wait 900`.
- Модели: основной шлюз `qwen-api` (openlux, квота исчерпана 25.09.2026). Локально роли
  переключены на OpenRouter строками `PD_MODELS__ROLES__{LLM,VLM}__{PROVIDER,MODEL}` в `.env`
  (`openrouter`, `qwen/qwen3.8-27b`); убрать строки — вернуться к `qwen-api`. Баланс
  OpenRouter ≈ $0,7; бесплатная линия `:free` перегружена.
- Для частых прогонов без qwen: мост к Claude Haiku через CLI Claude Code на хосте —
  `uv run python scripts/claude_bridge.py --port 8765` (фоном) и в `.env` провайдер
  `claude-bridge`, модель `claude-haiku-4-5` (+ `PD_CLAUDE_BRIDGE_URL`, `PD_CLAUDE_BRIDGE_KEY`).
  Замер 25.09.2026: три варианта ≈8,5 мин (qwen на OpenRouter — 2–4 мин), зато бесплатно.
  Итоговая проверка качества и времени — только на qwen.
- Оформление без модели: `uv run python scripts/rerender.py <job…> --label <метка>` —
  пересборка готовых заданий по сохранённым планам текущим кодом вёрстки (≈4 с на колоду);
  набор заданий на разных шаблонах — `runs/rerender-set.txt`.
- ONLYOFFICE иногда отдаёт PDF с перепутанными картинками макета сразу после перезапуска
  API — повторный экспорт того же PPTX чистый; сначала перерендерить, потом искать в коде.

## Проверка качества

- `test_pptx/` (в `.gitignore`) — 23 чужих шаблона (SlidesCarnival, PPTAgent, пустой python-pptx).
- `uv run python scripts/live_quality.py zoo --dir <папка> --label <метка> --variants balanced`
  — шаблоны папки на свои темы (`--topic-order runs/zoo-all` для сравнимых подмножеств),
  `--job сценарий=job_…` — отчёт по готовому заданию; сценарии организаторов — `all`.
- `uv run python scripts/vision_score.py <метка>` — доля слайдов с дефектом глазами модели
  (главная цифра качества; база `runs/quality/base-1`: 42 % слайдов, больше всего пустых).
- Замер открытия редактора в браузере: `runs/qa/office_timing.mjs` в контейнере Playwright.

## Как устроено качество генерации (итоги сентября 2026)

Подробный журнал — `docs/generation-quality-overhaul.md`. Принципы вместо частных правок:
1. Кегль — по роли текста; пределы (`capacity.min_pt_for`) растут с шириной слайда (холсты
   Canva/Google Slides 20 дюймов); заголовок при переполнении уменьшается до предела роли.
2. Остатки образцов убираются общими правилами: служебные слайды сервисов (`matching.vendor_meta`),
   колонтитулы-заготовки (`placeholder_markers`), QR, аватары.
3. Проверка вёрстки по картинке (скилл `visual_reviewer`, `audit/visual.py`,
   `pipeline/run._review_and_fix`): дефектные слайды перестраиваются на других композициях
   (`avoid_patterns`), остаётся вариант с меньшим числом дефектов.
4. Разделы смыслового плана — по полю `part` тезисов (приём PPTAgent).
Время: три варианта одной презентации — 2–4 мин (цель ≤ 5 мин); основной вариант собирается
первым, два других — после него параллельно (`jobs.ordered_variants`).

Правка из чата (`generation/edit.py`, `slide_editor` 0.2.1, `office_edit`, `office_object_edit`,
`project_assistant` 0.1.3): делает максимум сразу, не хватает данных — ищет в интернете
(`research.evidence`, DuckDuckGo) и ставит с источником; отказ — только если менять нечего.

## Открытые задачи

1. Заставка загрузки ONLYOFFICE в стиле проекта вместо стандартного загрузчика
   (`frontend/components/office/OfficeEditor.tsx`: сейчас `.office-loading` с «Загружается
   редактор…» поверх iframe до `onDocumentReady`; этапы: копия на сервере → программа
   (`onAppReady`) → слайды (`onDocumentReady`)).
2. Ускорить открытие редактора (замер 25.09.2026: повторное ≈8 с, первое ≈14 с): подготовка
   копии на сервере 1,5–3 с; каждое открытие — новый ключ сессии, ONLYOFFICE заново переводит
   PPTX (≈3 с); прогрев (`warmUpOffice`) только внутри проекта и тянет все четыре редактора.
3. Пустые слайды — главный вид дефекта (композиция больше содержания; колода McKinsey
   «кибергигиена» — пять разделителей подряд и два содержательных слайда).
4. При повторной правке слайда модель иногда переписывает текст прошлой правки.
