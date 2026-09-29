# Документация

Путеводитель по документам проекта «Цифровой дизайнер презентаций» (задача 4 от VK Tech, команда «МАИ»). Здесь собраны ссылки на все документы и сказано, что в каком искать.

## С чего начать

| Документ | Что в нём |
| --- | --- |
| [README.md](README.md) | что умеет сервис, запуск за три команды, сдача, качество, ограничения |
| [ARCHITECTURE.md](ARCHITECTURE.md) | сервисы, пять слоёв и контракты между ними, очереди заданий, поток данных одной генерации |
| [MODELS.md](MODELS.md) | модели и их лицензии, роли `llm` и `vlm`, провайдеры, скиллы и версии промптов |
| [AUDIT.md](AUDIT.md) | реестр 36 проверок аудита: что проверяется, пороги, по каким данным |

`MODELS.md` и `AUDIT.md` собираются из кода и конфигурации, поэтому всегда совпадают с тем, что работает.

## Как устроена генерация

| Документ | Что в нём |
| --- | --- |
| [docs/pipeline.md](docs/pipeline.md) | каждый слой по порядку: команда запуска, правила, отладка заданий |
| [contracts/README.md](contracts/README.md) | JSON-схемы документов, которыми обмениваются слои, и их версии |
| [docs/template-analysis.md](docs/template-analysis.md) | разбор шаблона: результаты на четырёх шаблонах организаторов |
| [docs/chart-image-reconstruction.md](docs/chart-image-reconstruction.md) | как картинка графика становится редактируемой диаграммой |
| [docs/embedded-fonts.md](docs/embedded-fonts.md) | шрифты, встроенные в PPTX: извлечение и проверка |
| [docs/pptx-capabilities.md](docs/pptx-capabilities.md) | проверка движка PPTX: клонирование слайдов, таблицы, диаграммы |

## Качество и замеры

| Документ | Что в нём |
| --- | --- |
| [docs/generation-quality-overhaul.md](docs/generation-quality-overhaul.md) | как доводилось качество вёрстки: принципы, замеры до и после на 23 чужих шаблонах |
| [docs/evaluation.md](docs/evaluation.md) | замеры времени генерации, запуска стека и разнообразия колод |
| [docs/decisions.md](docs/decisions.md) | журнал решений по датам: что выбрали, почему и чем проверили |

## Модели

| Документ | Что в нём |
| --- | --- |
| [MODELS.md](MODELS.md) | какая модель какую роль выполняет и как её сменить |
| [docs/llm-capabilities.md](docs/llm-capabilities.md) | что умеет шлюз модели: JSON-схема, изображения, рассуждение, одновременность |
| [src/presentation_designer/llm/README.md](src/presentation_designer/llm/README.md) | единая точка вызова моделей: кэш, лимитер, повторы, учёт токенов |
| [tests/fixtures/llm/README.md](tests/fixtures/llm/README.md) | записанные ответы модели, на которых тесты работают без сети |

## Редактор и чат

| Документ | Что в нём |
| --- | --- |
| [docs/onlyoffice.md](docs/onlyoffice.md) | редактор ONLYOFFICE: запуск, лицензия, сохранение, правки из чата, серверный рендер |
| [docs/onlyoffice-ui.md](docs/onlyoffice-ui.md) | как редактор встроен в экран проекта |
| [docs/onlyoffice-orchestration.md](docs/onlyoffice-orchestration.md) | как чат, редактор и фоновая генерация работают вместе |
| [docs/speech.md](docs/speech.md) | голосовой ввод: модель GigaAM, установка весов |

## Запуск и выкладка

| Документ | Что в нём |
| --- | --- |
| [README.md](README.md#быстрый-старт) | локальный запуск всего стека |
| [docs/deploy.md](docs/deploy.md) | выкладка на сервер, откат, резервные копии, требования к памяти |
| [docker/fonts/README.md](docker/fonts/README.md) | шрифты в образах и их лицензии |

## Код по модулям

Короткое описание лежит рядом с кодом каждого слоя в `src/presentation_designer/`.

| Модуль | Что делает |
| --- | --- |
| [parsing/template](src/presentation_designer/parsing/template/README.md) | разбор шаблона PPTX в профиль |
| [parsing/content](src/presentation_designer/parsing/content/README.md) | импорт материалов в контент-пакет |
| [generation](src/presentation_designer/generation/README.md) | смысловой план и планы трёх вариантов |
| [design](src/presentation_designer/design/README.md) | подгонка композиции под объём содержания |
| [library](src/presentation_designer/library/README.md) | собственные композиции в дизайн-коде шаблона |
| [layout](src/presentation_designer/layout/README.md) | вёрстка PPTX по плану |
| [export](src/presentation_designer/export/README.md) | PDF, миниатюры, HTML |
| [audit](src/presentation_designer/audit/README.md) | проверки готовой колоды |
| [pipeline](src/presentation_designer/pipeline/README.md) | задания, очереди, состояние, артефакты |
| [api](src/presentation_designer/api/README.md) и [cli](src/presentation_designer/cli/README.md) | HTTP-интерфейс и команды |

## Примеры

| Каталог | Что в нём |
| --- | --- |
| [examples/content](examples/content) | контент-пакет сдачи: docx, xlsx, заметки, картинка графика, бриф |
| [examples/quality](examples/quality) | тексты-задания для проверочных прогонов |
