# Контракты между слоями

Версия набора 1.5 от 16 сентября 2026 года: `composed_deck` имеет `schema_version` 1.2 (этап 8: `composer`, `created_at`, `fonts` с подменой рендерера, `stats`; у слайда `title`, `notes`, `source_slide_part`, `removed_object_ids`; у объекта `content_source` — plan/sample/template/generated, `slot_kind`, `block_kind`, `fit` из плана; у картинки `fit`, `origin`, `recolored`; у таблицы `row_offset`, `truncated`; у диаграммы `categories_count`, `built`; `diagram` у группы фигур), `slide_plan` — 1.2 (этап 7: `fit` у блоков — измерение текста по метрикам шрифта и действие лестницы ёмкости, `row_offset` у таблиц, `target` у числа слайдов, блок chart в слоте image паттерна роли chart), `content_package` и `project` — 1.3 (этап 6: метаданные импорта, места блоков в источнике, источник брифа в карточке), `story_plan` — 1.2 (эффективный бриф и покрытие обязательного содержания), `project_file` и `brief_extract` — 1.2, остальные остаются 1.1 до изменений своих полей. Схемы в формате JSON Schema 2020-12 лежат в [schemas/](schemas/), примеры документов в [examples/](examples/), проверка примеров по схемам в [validate.py](validate.py). Схемы общие для Python-ядра и интерфейса на TypeScript: Pydantic-модели (`src/presentation_designer/contracts/models.py`) и TS-типы (`frontend/lib/api/types.ts`) генерируются командой `make gen-contracts`, руками не правятся. Проверки связей, которые схема выразить не может, живут в `src/presentation_designer/contracts/validators.py`. Схема меняется только вместе с примером и с повышением `schema_version`; изменение проверяет второй участник.

ТЗ требует прозрачное разделение слоёв: парсинг, генерация, вёрстка, аудит, экспорт. Каждый слой принимает и отдаёт документы по этим схемам, поэтому слои разрабатываются независимо и тестируются на примерах, не дожидаясь друг друга.

## Глоссарий

| Термин | Значение |
| --- | --- |
| Template (шаблон) | Исходный PPTX, загруженный пользователем |
| Master (мастер) | Мастер-слайд с темой: цвета, шрифты темы |
| Layout (макет) | `slideLayout` мастера. В шаблонах датасета макеты почти пустые: один заголовок |
| Sample slide (образцовый слайд) | Готовый слайд шаблона с примерным содержанием. Главный источник паттернов |
| Pattern (паттерн) | Наша абстракция композиции: источник (образцовый слайд или макет), роль, слоты, ограничения |
| Slot (слот) | Область паттерна под содержание с шрифтом и ёмкостью в символах и строках |
| Design tokens (токены) | Палитра, шрифты, шкала кеглей, поля, сетка |
| Fixed element | Логотип, колонтитул, номер, навигационные точки, фон: остаются на месте |
| Asset (ресурс) | Картинка, иконка, логотип из шаблона или из содержания |
| Content package (контент-пакет) | Импортированное содержание: блоки, факты, наборы данных, картинки |
| Brief (бриф) | Краткое описание и назначение (фича, продукт, проект, инициатива). В режиме `brief` сервис сам генерирует структуру и текст |
| Fact (факт) | Число, дата, имя с идентификатором. Подставляется в текст по `{fact:<id>}`, модель значения не переписывает |
| Dataset (набор данных) | Таблица для таблиц и графиков |
| Slide plan (план) | Порядок слайдов, паттерны и содержание слотов одного варианта |
| Variant (вариант) | Один из трёх вариантов вёрстки одного контента, отличается по заявленной оси |
| Audit check / issue | Проверка из реестра и её находка на конкретном слайде |
| Artifact (артефакт) | Файл результата, разрешённый манифестом задания |
| Skill / agent (скилл) | Версионируемый компонент с промптами и параметрами: `skills/<name>/skill.yaml` |

## Принципы

1. Координаты в долях ширины и высоты слайда, начало в левом верхнем углу. Пересчёт в EMU делает слой вёрстки. Кегли в пунктах и всегда вместе с `slide_size` профиля: в датасете слайды 12192000 × 6858000 и 9144000 × 5143500 EMU.
2. Каждый документ несёт `schema_version`. Идентификаторы стабильны, без пробелов и путей.
3. Происхождение: блок плана ссылается на `block_id` и `fact_id`, находка аудита на `check_id` и `slide_id`, результат на версии скиллов, промптов и моделей.
4. В модель уходит только `llm_digest` профиля и выдержки по выбранным паттернам. Полный профиль и XML слайдов в промпты не попадают.
5. Аудит не правит файл. Он возвращает находки со стратегией исправления, а исправление запускается отдельным запросом после выбора пользователя.
6. Три варианта делят анализ шаблона и импорт содержания; различаются только план и вёрстка.

## Документы и слои

| Схема | Создаёт | Читает |
| --- | --- | --- |
| [template_profile](schemas/template_profile.schema.json) | parsing | generation, layout, audit, интерфейс (превью паттернов) |
| [content_package](schemas/content_package.schema.json) | parsing (импорт) | generation, audit (реестр фактов) |
| [story_plan](schemas/story_plan.schema.json) | generation, один раз на пакет | generation (варианты), audit (покрытие тезисов) |
| [slide_plan](schemas/slide_plan.schema.json) | generation, по одному на вариант | layout, audit, repair |
| [composed_deck](schemas/composed_deck.schema.json) | layout, по сохранённому PPTX | audit, export (HTML), интерфейс (подсветка) |
| [audit_report](schemas/audit_report.schema.json) | audit | интерфейс, repair, AUDIT.md |
| [generation_request](schemas/generation_request.schema.json) | интерфейс, CLI | api, pipeline |
| [generation_result](schemas/generation_result.schema.json) | pipeline | api, интерфейс, CLI, отчёты |
| [job_status](schemas/job_status.schema.json) | pipeline, для заданий любого вида | api, интерфейс |
| [skill_manifest](schemas/skill_manifest.schema.json) | `skills/<name>/skill.yaml` | pipeline, MODELS.md, ARCHITECTURE.md |
| [project](schemas/project.schema.json) | api (SQLite) | интерфейс: проект, бриф, настройки, файлы, лента чата |
| [project_file](schemas/project_file.schema.json) | api при загрузке | интерфейс, templates и content по `file_id` |
| [brief_extract](schemas/brief_extract.schema.json) | parsing/content (`brief`) через api | интерфейс: карточка «понял задачу так» |

Общие определения в [common](schemas/common.schema.json): идентификаторы, ревизия, координаты, размер слайда, стили с источником, параметры абзаца, состояния и этапы задания, режим исполнения слоёв, ошибка.

## Поток данных

| Этап | Вход | Выход | Замечания |
| --- | --- | --- | --- |
| `analyze` | PPTX | TemplateProfile | кэш по `template_hash` и `analyzer.version`; миниатюры образцов для превью и VLM |
| `import` | файлы или бриф | ContentPackage | факты и наборы данных извлекаются детерминированно |
| `story` | ContentPackage, явные настройки запроса | StoryPlan | кэш по `content_hash` (нормализованный пакет, эффективный бриф, модель, промпт, схема); не зависит от шаблона, идёт параллельно `analyze`; покрытие обязательных фактов и пунктов брифа проверяется кодом |
| `plan` | StoryPlan, `llm_digest`, кандидаты-паттерны с ёмкостью | SlidePlan × число вариантов | проверка схемы, связей и ёмкости до вёрстки; отклонённые блоки возвращаются модели с причиной |
| `compose` | SlidePlan, TemplateProfile, исходный PPTX, ContentPackage (ресурсы по `assets[].path`) | PPTX, ComposedDeck | исходный пакет как основа, клонирование образцов по `element_ref`, подстановка фактов, явный кегль из `fit`, нативные графики и таблицы, схемы из фигур, иконки; незаполненные карточки убираются, статика остаётся; ComposedDeck строится по сохранённому файлу (`content_source` у объектов: `sample` — намеренно оставленный текст образца, аудит не считает его заглушкой) |
| `export` | PPTX, ComposedDeck | PDF, HTML, миниатюры | PDF и миниатюры из сохранённого файла, HTML из ComposedDeck |
| `audit` | ComposedDeck, PPTX, миниатюры, StoryPlan, ContentPackage | AuditReport | детерминированные проверки без токенов сразу после вёрстки; контекстные по уровням: StoryPlan один раз, каждый вариант по картинке, тексту и источникам |
| `repair` | выбранные issues, `base_revision` | новая ревизия: SlidePlan, PPTX, ComposedDeck, AuditReport | проверка исходной ревизии; повтор `compose`, `export`, `audit` для затронутых слайдов и зависимостей |

## HTTP API

Все ответы JSON, кроме файлов. Пути файлов от клиента не принимаются: только загрузка тел и идентификаторы. Идентификаторы заданий это UUID. Ошибка отдаётся объектом `error` из common: `{code, message, stage?, retryable?, details?}`.

| Операция | Назначение | Ответ |
| --- | --- | --- |
| `GET /api/health` | готовность api, воркеров и рендерера; `checks` по частям: база, хранилище, Valkey, воркеры, пробный рендер на воркере | `{status: ok|degraded|down, workers: {analysis, generation}, valkey_ok, renderer_ok, version, execution_mode, checks}` |
| `GET /api/capabilities` | возможности сервиса и режим слоёв, включая слой `brief` | `{contracts_version, execution_mode, features: {generate_images, contextual_audit, html_export}, limits: {max_upload_mb, max_content_files, slide_count_max, max_project_files, max_project_mb}}` |
| `GET /api/projects` | список проектов с состоянием для карточек | `[{project_id, title, created_at, updated_at, template_id, package_id, job_id, chosen_variant, files_count, template_name, job_status, thumbnail_url, slide_count}]` |
| `POST /api/projects` | новый проект: `{title?, job_id?}` | `201` Project |
| `GET /api/projects/{id}` | проект с файлами и лентой; открывается по ссылке из любого браузера | Project |
| `PATCH /api/projects/{id}` | название, бриф, настройки, `template_id`, `package_id`, `job_id`, `chosen_variant` | Project |
| `DELETE /api/projects/{id}` | удаляет проект и ленту, снимает ссылки на файлы | `204` |
| `GET /api/projects/{id}/events` | лента чата | `[Event]` (см. `project.schema.json#/$defs/event`) |
| `POST /api/projects/{id}/events` | дозапись сообщения или карточки: `{role, kind, …}` | `201` Event |
| `PATCH /api/projects/{id}/events/{event_id}` | правка события, например ответ на вопрос «шаблон или материал» | Event |
| `POST /api/projects/{id}/files` | multipart `files`: потоковая загрузка с проверкой типа, дедупликацией по sha256 и квотами проекта | `201 [ProjectFile]` |
| `PATCH /api/projects/{id}/files/{file_id}` | вид файла: `template`, `material`, `other` | ProjectFile |
| `DELETE /api/projects/{id}/files/{file_id}` | снять ссылку проекта на файл; байты без ссылок удаляет сборка мусора | `204` |
| `POST /api/brief` | бриф из фразы: `{text, brief?, settings?}`; скилл `brief_extractor` на модели без рассуждения с тайм-аутом `timeouts.brief_s`, при недоступности модели — детерминированная эвристика с `source: heuristic` | BriefExtract |
| `POST /api/templates` | JSON `{file_id}` из файлов проекта или multipart `file` (CLI); идемпотентна по sha256 | `202 {template_id, job_id, cached}` |
| `GET /api/templates` | список шаблонов библиотеки с миниатюрой первого образца | `[{template_id, name, status, slide_count?, pattern_count?, preview?, created_at}]` |
| `GET /api/templates/{id}` | статус анализа, профиль, миниатюры образцов | `{status, job_id, profile?: TemplateProfile, previews: [name]}` |
| `GET /api/templates/{id}/assets/{name}` | миниатюра образца или ресурс по манифесту профиля | файл |
| `DELETE /api/templates/{id}` | убрать шаблон из библиотеки: строка и миниатюры удаляются сразу, проекты и файлы теряют ссылку на него, генерации остаются; задание анализа и байты файла подбирает сборка мусора | `204` |
| `POST /api/content` | JSON `{file_ids, brief?}` из файлов проекта или multipart `files` + `brief` (CLI), до `max_content_files`; идемпотентна по sha256 файлов в их порядке, брифу и версии импортёра; разбор каждого файла кэшируется отдельно, смена брифа файлы не перечитывает | `202 {package_id, job_id, cached}` |
| `GET /api/content/{id}` | результат импорта | `{status, job_id, package?: ContentPackage}` |
| `GET /api/content/{id}/assets/{name}` | изображение из пакета по манифесту | файл |
| `POST /api/generations` | GenerationRequest; `idempotency_key` возвращает то же задание | `202 {job_id}` |
| `GET /api/generations/{job_id}` | GenerationResult на любом этапе | GenerationResult |
| `GET /api/generations/{job_id}/story` | общий смысловой план | StoryPlan |
| `GET /api/generations/{job_id}/variants/{variant_id}/audit?revision=` | отчёт аудита ревизии (по умолчанию текущей) | AuditReport |
| `POST /api/generations/{job_id}/variants/{variant_id}/repairs` | `{base_revision, issue_ids: []}` | `202 {repair_job_id}`; `409 {error.code: "revision_stale", current_revision}` если ревизия устарела |
| `GET /api/generations/{job_id}/artifacts/{name}` | только имена из `artifacts_manifest` | файл с `Content-Disposition` |
| `GET /api/jobs/{id}` | состояние задания любого вида | JobStatus |
| `POST /api/jobs/{id}/cancel` | отмена; дочерние задания отменяются | `202` |
| `POST /api/jobs/{id}/retry` | повтор упавшего задания с переиспользованием завершённых этапов | `202 {job_id}` |

Файлы загружаются на сервер один раз через `POST /api/projects/{id}/files`; анализ шаблона и импорт содержания принимают `file_id`/`file_ids`, поэтому уточнение брифа или удаление одного материала не пересылает байты. Multipart в `templates` и `content` сохраняется для CLI и внешних клиентов. Один арендатор без авторизации: все проекты видны всем, кто открыл адрес сервиса.

Состояния задания: `queued`, `running`, `succeeded`, `needs_review`, `failed`, `canceled`. Этапы: `queued`, `analyze`, `import`, `story`, `plan`, `compose`, `export`, `audit`, `repair`, `finalize`, `done`. Клиент опрашивает `GET /api/jobs/{id}` с ограниченной частотой и останавливается на терминальном состоянии; готовность каждого варианта и полнота аудита видны в GenerationResult до завершения всего задания.

CLI вызывает то же ядро: `analyze`, `import`, `story`, `plan`, `compose`, `export`, `audit`, `generate` принимают и отдают те же документы в файлах рабочего каталога и пишут манифест запуска.

## Реестр проверок аудита

Идентификаторы `check_id` фиксированы: на них ссылаются AuditReport, тесты и AUDIT.md. Приложение 1 ТЗ покрыто целиком; свои проверки помечаются `origin: own`.

| check_id | Проверка | Источник данных | Порог по умолчанию |
| --- | --- | --- | --- |
| `layout.out_of_bounds` | элемент вышел за границы слайда | геометрия XML | bbox вне 0..1 |
| `layout.overlap` | два блока наложились | геометрия и z-order, исключения для фона и подписей на картинках | пересечение больше 2 % площади меньшего |
| `layout.text_overflow` | текст не поместился в рамку | метрики шрифта и перенос по словам, сверка с рендером | строк больше ёмкости |
| `layout.text_clipped` | текст обрезан краем слайда | bbox текста и границы | любая часть за краем |
| `layout.off_guides` | блоки не выровнены по направляющим | направляющие профиля | отклонение больше 0,5 % ширины |
| `layout.in_margins` | контент заходит в поля | поля профиля, кроме fixed elements | любая часть в поле |
| `layout.image_distorted` | картинка растянута | пиксели исходника и bbox | пропорции отличаются больше чем на 3 % |
| `template.font_not_in_template` | шрифт не из шаблона | список шрифтов профиля | семейство вне списка |
| `template.too_many_fonts` | гарнитур больше двух | шрифты слайда | больше `max_font_families` |
| `template.size_not_in_scale` | кегль не из шкалы | типографическая шкала | размер вне шкалы |
| `template.color_not_in_palette` | цвет не из палитры | палитра профиля | цветовое расстояние выше порога |
| `template.not_on_layout` | слайд собран не на макете шаблона | ссылка слайда на макет | макет не из профиля |
| `template.fixed_element_moved` | логотип или колонтитул сдвинуты | fixed elements | смещение больше 0,5 % |
| `template.low_contrast` | контраст текста к фону ниже 4.5:1 | цвета текста и фона по WCAG | меньше 4.5 |
| `density.too_many_bullets` | больше 6 буллетов | текст слайда | больше 6 |
| `density.bullet_too_long` | буллет длиннее 15 слов | текст слайда | больше 15 |
| `density.table_too_big` | таблица больше 7 строк или 5 колонок | таблица | 7 и 5 |
| `density.too_many_series` | больше 5 серий на диаграмме | XML диаграммы | больше 5 |
| `density.fill_ratio` | заполнение меньше четверти или больше трёх четвертей | площадь объектов к площади слайда | 0,25 и 0,75 |
| `integrity.file_not_opening` | файл не открывается | целостность пакета и рендер | ошибка |
| `integrity.placeholder_text` | остался текст-заглушка | маркеры профиля, lorem ipsum, XXX, TODO, «вставьте текст» | найдено |
| `integrity.empty_slide` | пустой слайд или один заголовок | объекты слайда | нет содержания |
| `integrity.raster_slide` | слайд оказался картинкой | одна картинка на весь слайд без текста | найдено |
| `integrity.chart_missing_labels` | у диаграммы нет подписей осей, единиц или легенды | XML диаграммы | отсутствует |
| `integrity.duplicate_slides` | два слайда дублируют друг друга | сходство текста и структуры | выше 0,9 |
| `content.q1` … `content.q11` | 11 вопросов валидации контента из Приложения 1 | картинка слайда, реестр фактов, VLM | ответ «нет» |

Детерминированные проверки работают без токенов и выполняются первыми. Контекстные задаются одним вызовом на слайд со всеми 11 вопросами и ответом по схеме.

## Скиллы, промпты и модели

- `skills/<name>/skill.yaml` по схеме `skill_manifest`; промпты рядом, в `skills/<name>/prompts/<id>.v<version>.md`. Версия промпта входит в ключ кэша, в `llm_calls[]` и в `GenerationResult.versions`.
- `config/models.yaml` описывает роли `llm`, `vlm`, `text_to_image`: провайдер, переменная окружения с адресом и ключом, имя модели, ссылка на Hugging Face, лицензия, число параметров, параметры запроса. Из него собирается MODELS.md.
- `config/app.yaml` задаёт лимиты: размер загрузки, число слайдов, тайм-аут задания, число вариантов, пороги аудита.

## Как проверять

```bash
make gen-contracts        # перегенерировать модели и типы после правки схем
uv run python contracts/validate.py
uv run pytest tests/contracts
```

Тесты ядра загружают примеры из `examples/` как фикстуры: слой вёрстки собирает PPTX из `slide_plan.example.json`, аудит проверяет, что отчёт валиден по схеме.
