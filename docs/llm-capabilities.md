# Возможности провайдера моделей

Зонд `presentation_designer.llm.probe` 0.1.0, запуск 2026-09-18T11:32:52Z, commit `6185ce8c0cfb`. Провайдер `qwen-api` (openai_compatible); хост берётся из `PD_QWEN_BASE_URL`, ключи и адреса в отчёт не входят.

## Роли и model ID

| Роль | Model ID | В каталоге /models |
| --- | --- | --- |
| llm | `qwen3.8-27b` | да |
| vlm | `qwen3.8-27b` | да |

## Проверки

| Проверка | Итог | Задержка, мс | Токены (вход/выход/рассуждение) | Подробности |
| --- | --- | --- | --- | --- |
| models_list | да | — | — | в каталоге 419 моделей; настроенные: qwen3.8-27b — есть |
| text | нет | 2685 | 42/200/None | finish=length, попыток 1; без рассуждения; проверка: ответ '' |
| usage | да | — | — | usage в ответе провайдера |
| provider_cache_first | да | 1510 | 81/117/None | finish=stop, попыток 1; без рассуждения |
| provider_cache_second | да | 2345 | 43/200/None | finish=length, попыток 1; без рассуждения |
| provider_cache | нет | — | — | повтор того же текста считался заново |
| json_object | да | 2151 | 180/197/None | finish=stop, попыток 3; без рассуждения |
| json_schema | нет | 180017 | — | ProviderError: тайм-аут вызова модели qwen3.8-27b |
| brief_extract | да | 3756 | 182/191/None | finish=stop, попыток 2; без рассуждения |
| image | да | 2041 | 346/116/None | finish=stop, попыток 1; без рассуждения |
| multi_image | да | 2442 | 537/169/None | finish=stop, попыток 1; без рассуждения |
| reasoning_off | да | 10912 | 117/1299/None | finish=stop, попыток 1; без рассуждения; стиль none |
| reasoning_low | да | 7752 | 239/659/None | finish=stop, попыток 4; без рассуждения; стиль none |
| param_reasoning_effort | есть | 1328 | 41/85/None | принят; рассуждения нет |
| param_enable_thinking | есть | 1023 | 78/43/None | принят; рассуждения нет |
| param_chat_template_kwargs | нет | 2145 | 38/200/None | finish=length, попыток 1; без рассуждения; проверка: ответ '\n\nР' |
| streaming | да | 1758 | 90/117/None | первый токен через 1428 мс, usage в потоке: есть |

## Одновременность

| Уровень | Успешно | Ошибок (429) | Общее время, мс | p50, мс | max, мс | Ожидание квоты, мс | Токены | Наблюдаемый RPM |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 0/1 | 1 (0) | 31403 | — | — | 0 | 0 | — |
| 2 | 1/2 | 1 (0) | 24408 | 4392 | 4392 | 14 | 584 | 2.5 |
| 4 | 2/4 | 2 (0) | 34738 | 5125 | 5296 | 23 | 1207 | 3.5 |

## Квоты

Документированные квоты провайдера (из настроек или аргументов зонда): одновременность —, RPM —, TPM —. Короткий зонд их не доказывает: наблюдаемая пропускная способность выше — это несколько запросов за секунды, а не минута под нагрузкой. В `config/models.yaml` записываются документированные значения, лимитер работает по ним.

## Фрагмент для config/models.yaml

```yaml
providers:
  qwen-api:
    supports:
      json_schema: false
      json_object: true
      images: true
      multi_image: true
      usage: true
    limits:  # документированные квоты, не результат зонда
      concurrency: null
      rpm: null
      tpm: null
roles:
  llm:
    model: qwen3.8-27b
    verified: { by: probe, date: '2026-09-18', source: 'docs/llm-capabilities.md' }
  vlm:
    model: qwen3.8-27b
    verified: { by: probe, date: '2026-09-18', source: 'docs/llm-capabilities.md' }
```

Завершено 2026-09-18T11:58:23Z. Полный JSON — в `runs/probe/` (не коммитится).
