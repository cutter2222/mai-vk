# llm

Единственная точка вызова моделей (правило 7 плана): слои не импортируют openai и не знают
адресов провайдера.

| Модуль | Назначение |
| --- | --- |
| `types.py` | `Request`/`Response`, `Message`, `Image`, `Deadline`, ошибки с кодами для контракта `error` |
| `client.py` | `LlmClient.complete()`: роль → провайдер и модель из `config/models.yaml`, кэш, объединение одинаковых запросов, аренда лимитера, вызов, разбор и проверка ответа (JSON с починкой, Pydantic), учёт usage; `build_client()`, `describe_provider()`, `model_refs()` |
| `transport.py` | один HTTP-вызов через openai SDK (`max_retries=0`): сообщения с изображениями (data URL), `json_schema`/`json_object`/`text`, параметры рассуждения по стилю провайдера, usage, streaming для зонда |
| `limiter.py` | одновременность, RPM и TPM на модель: `ValkeyLimiter` общий для всех воркеров (Lua, время сервера, аренды с TTL), `LocalLimiter` с той же логикой для тестов и встроенного исполнителя |
| `retry.py` | повторы 429/сеть/5xx/негодный ответ с `Retry-After`, ростом паузы и разбросом в пределах deadline |
| `cache.py` | ключ из содержимого запроса и картинок, провайдера, модели и ревизии, версии промпта, схемы и параметров; режимы `off`/`read`/`read_write` (`data/llm-cache`) и `record`/`replay` (`tests/fixtures/llm`) |
| `stub.py` | детерминированная заглушка транспорта: правила, минимальный документ по схеме, имитация ошибок |
| `usage.py` | `UsageRecorder` → `GenerationResult.metrics` (`llm_calls`, `totals`, `quota_wait_ms`, `retries`, `cache`) |
| `skills.py` | загрузка `skills/<name>/skill.yaml` и промптов с проверкой версий; `Skill.request()` строит запрос по манифесту |
| `tokens.py` | консервативная оценка токенов для резервирования TPM (без внешнего токенизатора) |
| `probe.py` | зонд провайдера (`probe`), smoke-вызов из контейнера (`smoke`), каталог моделей, проверка лимитера из нескольких процессов (`limiter`) |

Вход: запросы от слоёв (`Skill.request(...)` или `Request`). Выход: `Response` с текстом,
разобранным документом, usage, задержкой, ожиданием квоты, числом попыток и признаком кэша.

Тесты — `tests/llm`, без сети: заглушка транспорта и replay. Реальные вызовы — только зонд,
smoke и приёмочные прогоны. Валидация ответа встроена (Pydantic и повтор с подсказкой);
instructor не подключён — см. `docs/decisions.md`.
