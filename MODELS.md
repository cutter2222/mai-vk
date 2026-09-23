# Модели

Документ собран из кода: `uv run scripts/gen_models_md.py` по `config/models.yaml` и
манифестам скиллов. Правки руками перезаписываются следующим запуском.

Сервис не хостит веса: он ходит в OpenAI-совместимый endpoint провайдера, а роль
решает, какая модель отвечает за какой этап. Ключи и адреса живут только в
окружении — в конфиге записаны имена переменных.

## Роли

| роль | модель | веса | лицензия | параметров | рассуждение | температура | проверено |
| --- | --- | --- | --- | --- | --- | --- | --- |
| текст (llm) | `qwen3.8-27b` | [Qwen3.8-27B](https://huggingface.co/Qwen/Qwen3.8-27B) | Apache-2.0 | 27 млрд | выключено, до 12000 токенов ответа | 0.2 | probe, 2026-09-20 |
| текст и изображения (vlm) | `qwen3.8-27b` | [Qwen3.8-27B](https://huggingface.co/Qwen/Qwen3.8-27B) | Apache-2.0 | 27 млрд | выключено, до 6000 токенов ответа | 0.0 | probe, 2026-09-20 |

Выключенные роли: `text_to_image`. Они не вызываются и в результате не появляются.

## Системные требования

| роль | модель | память под веса |
| --- | --- | --- |
| текст (llm) | `qwen3.8-27b` | ≈54 ГБ в bf16, ≈16 ГБ при 4-битном квантовании (+ память под контекст) |
| текст и изображения (vlm) | `qwen3.8-27b` | ≈54 ГБ в bf16, ≈16 ГБ при 4-битном квантовании (+ память под контекст) |

Это расчёт по числу параметров (2 байта на параметр в bf16), а не замер: сервису
хватает доступа к endpoint, и своей видеокарты он не требует. Машина, на которой
работает сам сервис, считает вёрстку и рендер на CPU. ONLYOFFICE — отдельный сервис
с бюджетом от 4 ГБ; для полного стека ориентир от 8 ГБ с нагрузочной проверкой.

## Провайдеры

### `qwen-api` (активный)

* Вид: openai_compatible; адрес и ключ — переменные `PD_QWEN_BASE_URL`, `PD_QWEN_API_KEY`.
* Рассуждение: стиль `vllm_chat_template`.
* Умеет: json_schema — да, json_object — да, images — да, multi_image — да, usage — да.
* Квоты: провайдером не сообщены, лимитер работает по умолчаниям `llm.*` из `config/app.yaml`.

### `vk-inference`

* Вид: openai_compatible; адрес и ключ — переменные `PD_VK_BASE_URL`, `PD_VK_API_KEY`.
* Рассуждение: стиль `none`.
* Умеет: json_schema — не проверено, json_object — не проверено, images — не проверено, multi_image — не проверено, usage — не проверено.
* Квоты: провайдером не сообщены, лимитер работает по умолчаниям `llm.*` из `config/app.yaml`.
* Обязателен для команд топ-10. Доступ и model ID подтверждаются организаторами.

## Кто из скиллов какую роль зовёт

| скилл | этап | роль | промпты | рассуждение | формат ответа |
| --- | --- | --- | --- | --- | --- |
| `auditor` 0.2.0 | audit | vlm | `audit.content_questions` 0.2.0 | выключено, до 4000 | json_schema |
| `brief_extractor` 0.2.0 | import | llm | `brief.extract` 0.2.0 | выключено, до 4000 | json_schema |
| `chart_extractor` 0.1.0 | import | vlm | `import.chart_image` 0.1.0 | выключено, до 9000 | json_schema |
| `chart_reader` 0.1.0 | analyze | vlm | `rebuild.chart_image` 0.1.0 | выключено, до 6000 | json_schema |
| `content_importer` 0.1.0 | import | llm | `import.fact_context` 0.1.0 | выключено, до 6000 | json_schema |
| `project_assistant` 0.1.0 | import | llm | `chat.reply` 0.1.0 | выключено, до 1000 | json_schema |
| `repairer` 0.1.0 | repair | llm | `repair.rewrite_block` 0.1.0 | короткое, до 10000 | json_schema |
| `slide_editor` 0.1.0 | plan | llm | `edit.slide` 0.1.0 | выключено, до 8000 | json_schema |
| `slot_filler` 0.1.0 | plan | llm | `fill.slots` 0.1.0 | выключено, до 6000 | json_object |
| `story_planner` 0.2.0 | story | llm | `story.outline` 0.2.0 | выключено, до 14000 | json_schema |
| `template_analyzer` 0.2.0 | analyze | vlm | `analyze.classify_samples` 0.3.0, `analyze.tag_assets` 0.1.0 | выключено, до 6000 | json_schema |
| `variant_planner` 0.4.1 | plan | llm | `plan.slides` 0.4.1 | выключено, до 14000 | json_schema |

Версия скилла и версия промпта входят в ключ кэша ответов и в `GenerationResult`:
по результату видно, каким промптом он получен.

## Рассуждение

У шлюза vLLM рассуждение выключается только `chat_template_kwargs.enable_thinking`
(стиль `vllm_chat_template`); `reasoning_effort` в теле запроса принимается, но не
действует. Там, где выключить его нечем, бюджет ответа поднят с запасом: рассуждение
тратит те же `max_completion_tokens`, и при тесном лимите ответ приходит пустым с
`finish_reason: length`. Подробности зондов — в `docs/llm-capabilities.md`.
