# Исправления save/close и XML ONLYOFFICE 9.3.1

## Реализовано

- `frontend/lib/editor/officeSave.ts`: изолированный адаптер SDK для same-origin
  `/onlyoffice`. Перед закрытием вызывается `asc_Save(true)` и проверяются завершение
  save action и реальное состояние SDK. Родительский `onDocumentStateChange` больше
  не является единственным барьером. `requestClose` вызывается только после flush:
  штатная реализация 9.3.1 сама не сохраняет и может предложить отменить правки.
- Ошибка, таймаут или недоступный адаптер оставляют iframe открытым. Размонтирование
  отменяет ожидание и снимает callbacks. Одинаковый барьер используется перед AI edit.
- Callback сначала проверяет безопасность ZIP, затем **все** XML/relationships
  строгим парсером без recovery. Повреждённые ревизии не подтверждаются.
- Узкий compatibility workaround `office_ooxml.py` исправляет только сырое значение
  корневого `matchingName`, точно совпадающее с именем из валидного session source.
  После исправления часть должна целиком успешно парситься. Неизвестная ошибка,
  неоднозначность или повреждение другой части отклоняются.
- Это **не исправление C++ сериализатора ONLYOFFICE**. Нормализация явно отражается
  в API `normalizations` и предупреждении редактора. Сырой PPTX, SHA-256 и список частей
  сохраняются атомарно в SQLite `save_normalizations`; таблица включается в обычный
  backup OfficeStore. Исторические файлы и v0 не переписываются.
- Presence/no-change callback больше не стирает предыдущую ошибку сохранения.

## Проверка на реальном шаблоне (54 слайда)

Артефакты находятся в `./runs/`:

| Каталог | Сценарий | Результат |
| --- | --- | --- |
| `sdk-brand-fix-01` | Немедленное закрытие после Replace All, **без toolbar Save** | 1 passed; v0 → v1, reopen v1 |
| `sdk-brand-fix-02` | Toolbar Save, затем закрытие | 1 passed; v0 → v2, reopen v2 |

Для каждого запуска: `browser.log`, `browser.exit`, `browser/*/report.json`,
`saved.pptx`, `reopened.pptx`, `original-after.pptx`, `audit/report.json`,
`audit.log`, `audit.exit`. В обоих закрыты только новые тестовые документы,
`active_key=null`, `error=null`; saved/reopened проходят строгую проверку всех XML.
Исходный шаблон и v0 сохранили SHA-256
`cbbe3aa6a21d23cebc4d1383b93dd07a9cea460d683de1903567b616c839485d`.

В обоих случаях зарегистрирована нормализация пяти макетов 3, 5, 23, 24, 25.
Старые артефакты `sdk-brand-return-05` остались невалидными и не изменялись.

## Оставшееся ограничение оформления

**Полный строгий аудит не пройден (exit 1).** В обоих новых прогонах 54 → 54 слайда,
но есть отличия геометрии на 36 слайдах и пиксельные отличия на 9 нетронутых слайдах.
В проверенных примерах геометрия отличается на 1 EMU. В первом прогоне максимальная
доля изменённых пикселей на нетронутом слайде — примерно 0,092% (слайд 31).
Порог сравнения не ослаблен. Эти результаты не доказывают полную сохранность
оформления PowerPoint; сравнение выполнено одним и тем же конвертером ONLYOFFICE.
Автоматического «восстановления» геометрии из исходника нет — это могло бы отменить
намеренные ручные правки пользователя.

### Локализация потерь точности

Продолжение диагностики: `./runs/sdk-brand-precision-01/`.
В первом исходном round-trip обнаружены **1023** изменения явных числовых свойств:
326 геометрических и 697 типографических (включая макеты и мастера).
Это больше, чем 282 изменения координат/размеров из прежнего snapshot слайдов.
Примеры: размер шрифта 14,06 → 14 pt; интервал 14,06 → 14,05 pt.

Контролируемый эксперимент на отдельных, **не пользовательских** копиях:

| Возвращённые исходные свойства | Нетронутые слайды с отличающимися пикселями |
| --- | --- |
| Только геометрические | 31, 53 |
| Только типографические | 6, 8, 12, 22, 33, 37, 47 |
| Обе группы | Нет; отличается только редактируемый слайд 3 |

Входные файлы не изменились; `report.json` и exit 0 сохранены. Это локализация
причин, **не исправление сохранения** и не разрешение применять обратную подстановку
к произвольным ручным правкам.

Диагностика `src/presentation_designer/audit/office_roundtrip.py` теперь включена
в `scripts/check_office_roundtrip.py`: числовой дрейф отклоняется даже при одинаковых
пикселях. Сравниваются общие структурные XML-адреса, не постоянные ID объектов.
Добавления/удаления свойств, наследование, шрифты, цвета и отношения этим числовым
отчётом не покрываются. Он не доказывает полной семантической идентичности.

В SDK `v9.3.1.10`, commit `72d49bfa2b59dd328efcc2c3b20f5b1e5e4bc5a3`, найдены
соответствующие операции в `common/Shapes/Serialize.js` и `SerializeWriter.js`:
округление размера шрифта до 0,5 pt на чтении; `>> 0` при записи преобразованных
координат и интервалов. Их наличие подтверждено в установленном минифицированном SDK.

`scripts/patch_onlyoffice_precision.py` создаёт только **отдельную экспериментальную
копию** SDK: проверяет SHA-256 сборки и уникальность всех anchors, отказывается
перезаписывать файлы. В Dockerfile/production он не подключён.
Браузерный патч сам по себе не помог: `sdk-brand-precision-live-01` — UI test exit 0,
но аудит exit 1 и те же 1023 числовых отклонения. SHA-256 загруженного патча проверен
в live-тесте. Серверный конвертер также использует SDK; его путь проверяется отдельно.

Замена `sdk-all.js`/gzip с удалением `sdk-all.cache` в отдельном тестовом
DocumentServer также не дала исправления: в `sdk-brand-precision-live-03/saved.pptx`
(файл внутри `browser/`) остались те же 1023 отклонения, аудит exit 1 и те же
9 нетронутых слайдов с отличающимися пикселями. **Live acceptance не пройден**:
прогоны `sdk-brand-precision-live-02` и `03` завершились падением Chromium,
Docker сообщает `OOMKilled=true`; для `03` сохранение произошло, завершённый reopen
не подтверждён. Увеличение `/dev/shm` до 1 GiB проблему не устранило.
Следующий шаг — исследовать фактически исполняемый серверный путь (включая
скомпилированные SDK-артефакты), а не выкладывать неподтверждённый JS-патч.

После эксперимента подтверждена остановка `mai-office-precision-ds`,
`mai-office-precision-proxy`, браузерных контейнеров и старого `mai-vk-sdk-proxy`.
Временный API :8001 не слушает, `/tmp/mai-precision-api.pid` удалён.
Основной SDK сохранил SHA-256
`5ad873396344deedf33b03fb81a1b0421108ac4d7160dd58b6ca6d77c7ee90e7`.

### Серверный snapshot: причина неэффективности предыдущего эксперимента

В `core/v9.3.1.10/DesktopEditor/doctrenderer/editors.cpp` функции
`CreateEditorContext` / `RunEditorWithSnapshot` сначала загружают
`sdkjs/slide/sdk-all.bin`. Если snapshot использован, `RunEditor` исполняет только
footer и **не читает JS**. Удалить только `.cache` недостаточно.

Изолированный эксперимент без сети и без полного DocumentServer:
`./runs/sdk-precision-native-01/`.
Вход — неизменённая копия шаблона из `sdk-brand-precision-live-03/before.pptx`.
Через `docbuilder` намеренно перемещён первый shape слайда 3:

```javascript
builder.OpenFile("/tmp/input.pptx");
var slide = Api.GetPresentation().GetSlideByIndex(2);
slide.GetAllShapes()[0].SetPosition(123456, -210685);
builder.SaveFile("pptx", "/experiment/patched-edit.pptx");
builder.CloseFile();
```

| Серверный путь | Числовые изменения |
| --- | --- |
| Штатный `.bin` snapshot | 1025: 2 намеренных + 1023 побочных |
| Исходный JS, без `.bin`/`.cache` | 1025 |
| Precision JS, без `.bin`/`.cache` | **2, только заданные координаты** |

Простое OpenFile/SaveFile **без вызова редактирования** дало ноль отклонений во всех
трёх вариантах: такой no-op недостаточен для проверки JS-сериализации.
Сырые файлы сохранены отдельно от `*-validated.pptx`; в последних применена только
аудируемая нормализация пяти `matchingName`, не подстановка числовых свойств.
Итог: `edit-report.json`, все три converter exit 0.

В отдельном DocumentServer для следующего опыта `.bin` переименован, `.cache`
убран, JS имеет SHA-256
`59a12980323c12e539c3d513c9e3802a685d8e9f967d8d09c818dd03712f8770`.
`sdk-brand-precision-live-04`: сохранение обычной текстовой правки через UI прошло,
**строгий аудит exit 0** — 54 слайда, ноль числовых отклонений, все 53 нетронутых
слайда пиксельно идентичны, входные файлы неизменны. Однако Chromium упал по OOM
на reopen, поэтому это не успешный полный live acceptance. Firefox в `live-05`
также столкнулся с OOM уже при первом открытии.

`frontend/tests/onlyoffice-precision-math.spec.ts` исполняет фрагменты реального
патченного SDK с проверкой SHA-256: знаковые координаты (включая отрицательные),
сотые доли pt и интервалы в pt/процентах. Chromium: 1 passed; TypeScript и ESLint
exit 0. Python-регрессии диагностики/патчера: 10 passed.
Изменения production Dockerfile по-прежнему отсутствуют.

Продолжение с ограничением JS heap Chromium до 512 MiB (`live-06`) не устранило
OOM на третьем открытии той же страницы. Сохранённый файл повторно прошёл строгий
аудит (exit 0). `live-07` использовал toolbar Save: v1 записалась, но браузер упал
до штатного close. Файл получен отдельным GET после падения, сохранён как
`saved-after-browser-crash.pptx`, строгий аудит также exit 0. Эти прогоны нельзя
засчитывать как полный end-to-end acceptance.

Отдельный тест `frontend/tests/onlyoffice-precision-reopen.spec.ts` проверяет именно
**reopen в новом браузерном процессе**, не заменяет исходный same-page сценарий:

| Артефакты | Проверенная сохранённая копия | Результат |
| --- | --- | --- |
| `sdk-brand-precision-reopen-01` | `live-04`, immediate close | 1 passed, 7,8 с |
| `sdk-brand-precision-reopen-02` | `live-07`, explicit Save | 1 passed, 8,6 с |

В обоих случаях конфигурация указывает v1, SHA-256 реально загруженного SDK
проверен, редактор готов, штатное закрытие без изменений успешно; v1 и v0
не изменились, `active_key=null`, `error=null`. Callback-аудит сохраняет raw SHA-256
и пять нормализованных `matchingName`; workaround всё ещё нужен.
Все 53 нетронутых слайда в аудитах `live-04`, `06`, `07` пиксельно совпадают,
числовых отклонений нет. Намеренная замена текста не потеряна.

Итог продолжения: **патч точности подтверждён на этом шаблоне** через серверный
эксперимент, UI-сохранения, строгие аудиты и отдельные reopen. Длинный same-page
сценарий заблокирован OOM на стенде с 8 GiB RAM; причина потребления памяти не
локализована. До production-выкладки нужны оба полных live-сценария без OOM,
проверка ресурсоёмкости запуска JS без snapshot и более широкий набор документов.
Это не доказательство полной идентичности с PowerPoint.

Полный backend повторён: **619 passed, 16 skipped**, exit 0, 110,21 с
(`/tmp/mai-precision-followup-pytest.log`). TypeScript, ESLint всех трёх precision/live
тестов, Ruff/format патчера, mypy модуля диагностики и `git diff --check` прошли.
После продолжения все временные native/precision/browser контейнеры остановлены,
API :8001 не слушает, PID-файл удалён. Четыре новые sandbox-копии не имеют активных
сессий/ошибок; сводка сохранена в `sdk-precision-native-01/final-sessions.json`.
Основной SDK сохранил прежний SHA-256; основной стек не перезапускался.

### Same-page acceptance вне Docker VM

Повтор `sdk-brand-precision-live-08` с записью RSS процессов воспроизвёл OOM
на третьем открытии. Перед падением браузерный cgroup занимал около 1,5 GB,
renderer — около 787 MB RSS. При этом `dockerd` внутри VM занимал около 2,9 GB
RSS, из 1 GiB swap почти ничего не оставалось. Эти данные не доказывают утечку
ONLYOFFICE: память занята не только редактором. Диагностика сохранена в
`runs/sdk-brand-precision-live-08/{memory.log,container-state.json,vm-memory-after.txt}`;
последний снимок VM сделан уже после падения, не в момент пикового потребления.

Для разделения причин тот же тест выполнен в headless Chrome на macOS, вне VM.
DocumentServer и API остались на изолированном стенде. Используется тот же
статический frontend; `OfficeEditor.tsx` и `officeSave.ts` побайтово совпадают
с рабочей копией. Приложение, save-barrier и порядок действий не менялись.
Нет `newPage`, reload, принудительного GC, перезапуска браузера внутри сценария
или ограничения JS heap: no-op close → reopen → правка → close → reopen → close
выполняются в одной странице штатной кнопкой «Открыть снова».

`frontend/playwright.config.ts` теперь допускает
`PLAYWRIGHT_CHROMIUM_CHANNEL=chrome`; без переменной остаётся bundled Chromium.
Это выбор установленного браузера, а не обход acceptance. В live-отчёте записаны
версия/канал браузера и `reopen_mode=same-page`. Для explicit Save дополнительно
сохраняются `toolbar-saved.pptx` и номер/хеш промежуточной ревизии: callback toolbar
Save и итоговый callback close могут создать разные версии. Проверять только
последний файл недостаточно.

Для воспроизведения нужны свежий `seed_office_sdk.py --source`, тестовый API,
одинаковый precision JS на browser/server и отключённый серверный `.bin` snapshot.
Прокси должен быть опубликован **только на loopback**; в этом опыте —
`127.0.0.1:18080`, public URL API — `http://localhost:18080/onlyoffice`, storage URL —
внутренний `http://api:8001`. Основные сервисы перезапускать не требуется.
Из каталога `./frontend` при установленном Node/pnpm:

```sh
PLAYWRIGHT_BASE_URL=http://localhost:18080 PLAYWRIGHT_CHROMIUM_CHANNEL=chrome \
  pnpm exec playwright test tests/onlyoffice-brand-live.spec.ts \
  --grep 'immediate close' --project=chromium --workers=1 --retries=0
```

Перед запуском экспортируются `ONLYOFFICE_BRAND_DOCUMENT_ID` из нового `seed.json`,
`ONLYOFFICE_BRAND_SHA256` и `ONLYOFFICE_SDK_SHA256`. Explicit Save запускается
отдельно с `--grep 'explicit save'` и **другим новым seed**. Все `saved.pptx` и
`toolbar-saved.pptx` проверяются неизменённым `scripts/check_office_roundtrip.py`;
числовые свойства из оригинала не подставляются, допуски не расширены.

Проверенные результаты (Chrome **153.0.8010.53**, macOS, Playwright 1.63.0):

| Прогон | Same-page UI | Строгий аудит |
| --- | --- | --- |
| `live-09`, immediate close | 1 passed, 30,6 с | exit 0 |
| `live-10`, explicit Save | 1 passed, 28,4 с | exit 0, `audit-corrected-path.exit` |
| `live-11`, immediate close, расширенный отчёт | 1 passed, 23,7 с | exit 0 |
| `live-12`, explicit Save, расширенный отчёт | 1 passed, 29,1 с | exit 0 для v2 и отдельно v1 (`toolbar-audit`) |

Все каталоги имеют префикс `runs/sdk-brand-precision-`. В `live-10/audit.log`
сохранена ошибка первого запуска с неверным путём к артефакту; он не дошёл до
аудита. Успешный запуск записан отдельно в `audit-corrected-path.log`, без
перезаписи истории ошибки. Во всех пяти успешных аудитах 54 слайда, **ноль числовых
отклонений**, 53 нетронутых слайда пиксельно идентичны, единственная заданная
текстовая правка сохранена. Исходник, no-op и v0 после reopen имеют одинаковый
SHA-256; сохранённая ревизия после reopen не изменилась. `active_key` и `error`
равны null. Нормализация пяти `matchingName` по-прежнему нужна и аудируется.

Блокировка **same-page acceptance на этом документе снята**. Это не исправление
OOM внутри Docker VM и не доказательство отсутствия утечек при длительной работе;
сменились и размещение браузера, и его сборка. Нельзя приписать весь эффект одному
фактору. Основной SDK/Dockerfile не менялись. Следующий production-барьер —
воспроизводимая согласованная поставка browser/server SDK, проверка затрат без
snapshot и регрессии на других документах, а не восстановление свойств из v0.

Повторные проверки: TypeScript и ESLint конфигурации/тестов — exit 0; реальная
арифметика precision SDK в host Chrome — 1 passed; Ruff/format трёх Python-модулей,
mypy числового аудита и `git diff --check` — exit 0. Полный pytest из корня
репозитория: **619 passed, 16 skipped**, 123,72 с, exit 0
(`/tmp/mai-precision-host-pytest.{log,exit}`); четыре предупреждения Pillow
не связаны с этими изменениями.
После проверки стенд остановлен, API :8001 и loopback :18080 не слушают,
PID-файл удалён. `final-metadata.json` сохранён для каждого `live-08`…`12`:
все пять sandbox-документов без активных сессий и ошибок. Основной SDK по-прежнему
имеет SHA-256 `5ad873396344deedf33b03fb81a1b0421108ac4d7160dd58b6ca6d77c7ee90e7`.

## Совместимость и выкладка

### Opt-in образ-кандидат precision SDK

Добавлен отдельный `./docker/onlyoffice-precision.Dockerfile`.
Обычные `onlyoffice.Dockerfile`, Compose и работающий стек **не переключены**.
Базовый проектный образ с установленными шрифтами передаётся только по digest;
дополнительно патчер проверяет полное содержимое SDK. Docker-specific ignore
передаёт в build context только патчер и файлы кандидата, без `.env` и документов.

При сборке одновременно меняются серверный/браузерный `sdk-all.js` и его gzip,
удаляются `sdk-all.bin{,.gz}` и старый `sdk-all.cache{,.gz}`. Сохраняется манифест
исходного/результирующего SHA-256. Перед каждым стартом entrypoint сверяет JS,
распакованный gzip, манифест и отсутствие snapshot с закреплёнными хешами;
несовпадение останавливает запуск. Code cache удаляется перед стартом и может
заново создаваться конвертером. Это не защита от изменения файлов после старта:
runtime-подмены SDK и bind-mount этого каталога запрещены процессом выкладки.

Кандидат сохраняет исходный интервал отрисовки. В штатном базовом образе это
40 мс; его precision SDK имеет SHA-256
`d128187c2e1f41061ed04caad67d3321c8143f238eccb90b727e8d499759098d`.
У ранее проверенного live-стенда была отдельная правка интервала до 16 мс и хеш
`59a12980323c12e539c3d513c9e3802a685d8e9f967d8d09c818dd03712f8770`.
Поддерживаются оба известных входа, но результаты live-09…12 **не являются**
UI acceptance нового образа со штатным интервалом.

Локальная воспроизводимая команда (digest ниже существует в этом Docker Desktop;
для другого хоста нужен доступный там digest проектного образа, не mutable tag):

```sh
docker build --progress=plain \
  --build-arg ONLYOFFICE_BASE=presentation-designer/onlyoffice@sha256:ada3baf45195c29b3a23e2c02f12c7dce9beddca1ae6287529a643a689f16cb1 \
  -f ./docker/onlyoffice-precision.Dockerfile \
  -t mai-onlyoffice-precision:candidate-1 .

./.venv/bin/python \
  ./scripts/check_onlyoffice_precision_image.py \
  --image mai-onlyoffice-precision:candidate-1 \
  --out ./runs/sdk-precision-image-02/smoke
```

Smoke создаёт одноразовый контейнер с собственной случайной JWT secret,
loopback-портом и без production-томов; image ID фиксируется перед запуском.
Проверяет cold start и restart, `/healthcheck`, SHA-256 HTTP JS для identity/gzip
и файлов сервера, сохраняет отчёт/логи, удаляет свой контейнер и анонимные тома
даже при провале проверки. Повторный запуск требует нового `--out`.

Первый smoke: `./runs/sdk-precision-image-01/smoke/report.json`,
exit 0. Readiness: 14,88 с холодный старт, 30,69 с restart **включая остановку**.
Снимки памяти: 1,199 и 1,136 GiB. Это не peak RSS, не длительная memory-регрессия
и не измерение стоимости отказа от snapshot: без открытия/конвертации документа
и сопоставимого контрольного запуска такой вывод делать нельзя.

Финальная пересборка также проверена: `smoke-final/report.json` в том же каталоге,
build и smoke exit 0; image ID
`sha256:b737fd9fa6620448be9dc133e5ea80aa67c03bb597d19dccd4c864c5f6cfcbf3`.
Readiness 12,36/31,56 с; снимки памяти 1,214/1,111 GiB. Обе HTTP-кодировки снова
совпали с серверным SDK после обоих стартов. Контейнеры smoke удалены вместе
с их анонимными томами. Отдельный запуск с намеренно повреждённым JS завершился
exit 1 до старта DocumentServer (`tamper.log`: `Precision SDK hash mismatch`).
Целевые unit-тесты патчера/проверяющего скрипта: **20 passed**; полный backend:
**636 passed, 16 skipped**, 109,61 с (`pytest.log`, `pytest.exit=0`). Ruff/format,
mypy обоих скриптов, shell syntax и `git diff --check` прошли. Основной SDK
сохранил прежний хеш; основной стек не пересоздавался. Расширенный PPTX-набор
и новый same-page acceptance в этом этапе ещё не запускались.

**Дополнительный production-барьер — browser cache.** В текущем ONLYOFFICE nginx
SDK отдаётся с `Cache-Control: public, max-age=31536000, immutable`. Новый контейнер
с тем же URL не гарантирует новую JS-копию в уже использовавшем сервис браузере.
До выкладки нужен новый версионный URL ресурсов/отдельный URL сервиса и проверка
перехода из прогретого browser cache; `no-cache` на сервере не очистит уже
сохранённый immutable ответ. Не менять public URL только для API.js, оставляя
прежние URL SDK, и не смешивать новые/старые converter workers за одним endpoint.
Нужны draining активных сессий, переключение согласованного набора сервисов и
проверяемый rollback. Автоматическая production-выкладка пока намеренно отсутствует.

Следующий acceptance: оба same-page сценария на **этом image ID**; затем VK Tech,
VK Education, VK Workspace и ЛЦТ (документы доступны локально), включая no-op,
текст/геометрию и строгий аудит без восстановления чисел из оригинала. Отдельно —
сравнение cold conversion с/без snapshot и длительный save/reopen memory soak.
Ни smoke, ни unit-тесты не заменяют эти проверки.

Адаптер использует версионно-зависимые методы SDK и `PE.getController("Viewport")`.
Это не публичный DocsAPI save method и не Automation API. На cross-origin
развёртывании или несовместимой версии он отказывает в закрытии, а не теряет правки.
Перед обновлением ONLYOFFICE обязательны оба live-сценария на новых seed.
Запускать один live-тест на один seed (`--grep "immediate close"` либо
`--grep "explicit save"`, `--retries=0`). Полный аудит всегда получает новый `--out`.

Проверены production build, TypeScript, ESLint, Ruff/format, mypy; полный backend
до последнего дополнительного теста: 610 passed, 16 skipped. Последний целевой набор:
66 passed. Chromium-регрессии: **14 passed**, exit 0, 43,4 с; включают clean-parent/pending-SDK, ошибку и повтор,
таймаут, недоступный адаптер и ожидание серверного callback после закрытия.

После добавления числового аудита: **619 passed, 16 skipped**
(`/tmp/mai-precision-full-tests.log`, 153,39 с). Целевые тесты диагностики и
экспериментального патчера: 10 passed. TypeScript и ESLint изменённого live-теста,
Ruff/format изменённых Python-файлов, mypy нового модуля и `git diff --check`
прошли. Production build в этом продолжении не повторялся.

Рабочие контейнеры не пересобирались и не перезапускались. Для live использовались
отдельный frontend в `/tmp/mai-vk-sdk-frontend`, тестовый proxy и временный API :8001
с отключённым reconciliation. Коммит и production-выкладка не выполнялись.