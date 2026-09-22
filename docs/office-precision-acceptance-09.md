# Изолированная приёмка precision-кандидата ONLYOFFICE

Дата артефактов: 22 сентября 2026 по времени окружения.
Стенд: `http://localhost:8088`, публикация только на `127.0.0.1:8088`.
Доказательства: `./runs/precision-acceptance-09/`.

## Итог и границы

**Оба сценария ручного сохранения и оба повторных открытия в новом браузере
прошли. Три строгих аудита сохранности прошли, immediate-close — после отдельного
повтора конвертации, завершившейся сначала HTTP 502.** Это локальная приёмка
конкретного кандидата на двух свежих копиях одной 55-слайдовой презентации,
не разрешение автоматически переключать рабочий ONLYOFFICE.

Рабочий стек на `localhost:8080` не переключался. Удалённый сервер не обновлялся.
OpenRouter не включался. ИИ/плагины и PowerPoint в этом этапе не проверялись.
Порогов аудита и `validate_facts` для прохождения проверки не меняли.

## Кандидат и изоляция

- Image ID: `sha256:b737fd9fa6620448be9dc133e5ea80aa67c03bb597d19dccd4c864c5f6cfcbf3`.
- SDK SHA-256: `d128187c2e1f41061ed04caad67d3321c8143f238eccb90b727e8d499759098d`.
- Manifest проверен до/после; HTTP identity и распакованный gzip имеют этот же
  хеш. Оба основных UI-отчёта фиксируют его; отдельные fresh-browser тесты также
  проверяют фактически загруженный SDK.
- Браузер: Chrome `153.0.8010.53`, Playwright Chromium project. Каждый fresh reopen
  запущен отдельным процессом Playwright, не просто новой страницей старой сессии.
- Собственные сеть, Valkey, DB/storage volume и JWT. Рабочие data volumes не
  подключались; исходный PPTX, скрипты и `src` подключены read-only. Mount
  `../../src:/app/src:ro` нужен для нового модуля аудита; это не immutable API-релиз.
- `provider-secrets.json`: `qwen-api=false`, `vk-inference=false`.
  Health стенда: provider `configured=false`, host отсутствует. Значений ключей
  в отчёт не включали. JWT стенда — отдельный секрет, не provider key.
- Общий health стенда был **degraded**, а не `ok`: analysis/generation workers
  отсутствовали, `renderer_ok=false`. Это не полная проверка рабочего приложения;
  успешные конвертации подтверждены отдельно тремя аудитами.

Конфигурация и свидетельства изоляции:
`./runs/precision-acceptance-09/compose.yaml`,
`./runs/precision-acceptance-09/isolation.txt`,
`./runs/precision-acceptance-09/settings-sanitized.json`.

## Результаты UI и сохранности

На слайде 7 выполнена единственная замена:
«Пример оформления 4 текстовых блоков» → «Вариант оформления 4 текстовых блоков».

| Сценарий | UI / ревизия | Fresh-browser reopen | Строгий аудит |
|---|---|---|---|
| Без toolbar Save → «Завершить и сохранить» | exit 0, v0 → v1 | exit 0, v1 и SHA-256 неизменны | первый exit 1 (HTTP 502), retry exit 0 |
| Toolbar Save → завершение | exit 0, toolbar v1, конечная v2 | exit 0, v2 и SHA-256 неизменны | exit 0 отдельно для v1 и v2 |

Для каждого из трёх успешных аудитов:

- 55 слайдов до и после;
- `expected_text_and_geometry_only=true`, `unexpected_structure_slides=[]`;
- `numeric_xml_deltas=[]`: **0 обнаруженных числовых отклонений**;
- пиксельные различия только на слайде **7**, остальные **54 пиксельно идентичны**;
- `inputs_unchanged=true`.

Аудиты выполнены скриптом
`./scripts/check_office_roundtrip.py`, одним конвертером
ONLYOFFICE и рендером шириной 1600 px, без допуска на посторонние пиксельные изменения.
Это не доказательство точности PowerPoint, полной семантической эквивалентности
или сохранения каждого OOXML-атрибута. В каждом результате **371 ZIP-часть изменена**:
сохранность измеренных структуры/геометрии/пикселей не означает побайтовое равенство
всех частей исходнику. Toolbar v1 и конечная v2 имеют разные хеши и проверены отдельно.

No-op совпал с исходником. В обеих сессиях `active_key=null`, `error=null`,
`normalizations=[]`; исходные v0 неизменны. Сохранённый файл, same-page reopen
и fresh-browser reopen каждой конечной ревизии совпадают по SHA-256.

| Файл | SHA-256 |
|---|---|
| Исходник / v0 обеих копий | `9ef2323ed5f49f464aee5ae7065f1f57bb92c8be3a635be507d59819e285cfe0` |
| Immediate close v1 | `588c7434263d3fb9f2314f816205e0cab0c46889be8383652bc99d94c8b373d5` |
| Toolbar Save v1 | `37a545460ba317545867e6fa30413909547257b07d96aec28697592cba5dc054` |
| Save → close v2 | `0e76a2df2aa6a9e5e6a5fa457c6bbaeb514c837511c965e97a9d72cd02faf9c1` |

Три отчёта аудита:

- `./runs/precision-acceptance-09/manual-close-saved-audit-retry/report.json`;
- `./runs/precision-acceptance-09/manual-save-saved-audit/report.json`;
- `./runs/precision-acceptance-09/manual-save-toolbar-saved-audit/report.json`.

## Неуспешные попытки сохранены

1. Этап 08: аудит immediate-close не импортировал
   `presentation_designer.audit.office_roundtrip` (`ModuleNotFoundError`).
   Toolbar-сценарий прерван с exit 130 при ожидании скрытого
   `#id-toolbar-btn-save`. Это не успешная приёмка.
2. Для этапа 09 добавлен read-only mount исходного кода. В
   `./frontend/tests/onlyoffice-brand-live.spec.ts`
   Save выбирается через `getByRole("button", { name: /^Сохранить(?:\s*\(|$)/ })`
   с timeout 30 секунд. После этого сценарии выполнены на новых копиях в новом стенде.
3. Первый аудит 09 завершился HTTP 502 от `/converter` до формирования итогового
   отчёта. Лог старта также фиксирует остановку/запуск `docservice`/`converter`.
   Это инфраструктурная ошибка, не pass и не выявленное нарушение сохранности;
   точная причинная связь с перезапуском по сохранённому traceback не доказана.
4. Отдельный retry использует сохранённые PPTX, хеши которых сверены с UI-артефактами,
   и прошёл без изменения порогов. Исходные лог и exit 1 не переписаны.
   `./runs/precision-acceptance-09/results.json`
   **не содержит внешнего retry** и правильно оставляет исходный exit 1.
   Сводная сверка учитывает оба результата отдельно.

## Финальная сверка и проверки кода

При продолжении работы стенд повторно не запускался. Локально пересчитаны хеши
PPTX, числовые XML-отклонения и сверены сохранённые UI/audit отчёты. Повторного
live-rendering в этом продолжении не было.

- Исторические **29 passed**, exit 0 подтверждены файлами
  `./runs/precision-acceptance-08/unit.log` и
  `./runs/precision-acceptance-08/unit.exit`.
- Новый целевой запуск: **24 passed**, одно deprecation warning Starlette/AnyIO.
  Набор: precision patch, precision image harness, numeric roundtrip audit.
  Это другой набор, не повтор полного набора из 29 или полного pytest проекта.
- TypeScript `--noEmit`, ESLint двух live-тестов, целевые Ruff check/format,
  Compose config и `git diff --check` **пройдены, все exit codes 0**;
  отдельные логи и коды сохранены.
- Первый запуск нового скрипта сверки выявил только E501 в самом скрипте.
  Строка исправлена; исходный exit 1 сохранён, повтор записан отдельно.
- Скрипт сверки:
  `./runs/precision-acceptance-09/verify_final.py`.
  Результаты повтора:
  `./runs/precision-acceptance-09/verification-final-2/`.
  Он не запускает стенд; повтор требует нового `--out`, существующие каталоги отклоняет.

## Teardown и рабочий стек

- `data-archive.exit=0`, `down.exit=0`. Архив читается, содержит 19 tar entries:
  `./runs/precision-acceptance-09/sandbox-data.tgz`.
  SHA-256: `ce2e1082e88a1b1f91f3f807b3a09138637186c674b1ac08ba7130ea11bbd2f1`.
  Это архив данных стенда, не проверка восстановления полного Docker-стека.
- Контейнеров, именованных volumes и сетей Compose 08/09 нет; также отсутствуют
  volumes, перечисленные в сохранённом inspect стенда 09. Порт 8088 свободен.
- ID и `StartedAt` рабочих API, ONLYOFFICE, Caddy совпадают с baseline этапа 08
  и состоянием после 09; эти три контейнера не пересозданы и не перезапущены.
  Это не побайтовый аудит всех рабочих данных или всех контейнеров.
- Свежий рабочий health: `status=ok`, analysis=1, generation=3, OpenLux настроен.
- Артефакты и секрет JWT стенда оставлены локально; их нельзя публиковать целиком.
  Одноразовый `./runs/precision-acceptance-09/run.py`
  в использованном каталоге повторно не запускать.

## Следующий шаг перед выкладкой

Изолированная приёмка конкретного ручного round-trip закрыта. Перед переключением
рабочего ONLYOFFICE всё ещё нужны согласованные browser/server SDK, новый
версионный URL для immutable browser cache, draining активных сессий и проверяемый
rollback по `./docs/office-save-fixes.md`.
Требуется также устранить риск ранней конвертации до полной готовности сервисов;
успешный retry не доказывает надёжность холодного старта.