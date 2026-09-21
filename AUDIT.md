# Аудит презентации

Документ собран из кода: `uv run scripts/gen_audit_md.py`. Список проверок, пороги и
серьёзность берутся из реестра `src/presentation_designer/audit/registry.py`, признак
реализации — из исходников проверок, доказательства — из тестов. Правки руками
перезаписываются следующим запуском.

## Как устроен аудит

Проверок в реестре: **36** — 25 считаются по файлу и
11 задаются модели. Все взяты из Приложения 1 ТЗ.

* **По файлу** (`audit/deterministic.py`) — геометрия, шрифты, палитра, макет, плотность,
  целостность пакета. Считаются по ComposedDeck и по самому PPTX, без модели и без
  токенов, поэтому на одном и том же файле всегда дают один и тот же ответ.
* **Моделью** (`audit/contextual.py`) — 11 вопросов о содержании. Модель получает
  картинку слайда, его текст, состав объектов и список фактов из исходных материалов.
  Девять вопросов задаются по каждому слайду одним вызовом, два (язык колоды и связность
  соседей) — одним вызовом по тексту всей колоды.

Отчёт (`audit_report`) перечисляет исход каждой проверки для каждой области:

| исход | что значит |
| --- | --- |
| `passed` | проверка выполнена, нарушения нет |
| `failed` | нарушение найдено, в отчёте есть находка со ссылкой на слайд и объект |
| `not_applicable` | проверять нечего: вопрос про таблицу слайду без таблицы не задаётся |
| `not_checked` | проверку выполнить не удалось: нет входных данных, модель не уверена или вызов не прошёл; причина записана рядом |

`coverage.complete` становится `false`, как только появилась хоть одна непроверенная
область, а `coverage.missing_inputs` называет причину (`vlm_unavailable`,
`slide_render`, `pptx_file`). Неприменимые проверки покрытие не ломают.

Находка несёт стратегию исправления и его цену: правка файла, пересборка или
перегенерация текста моделью. Исправления применяются только по выбору пользователя.

## Проверки

### Вёрстка

| проверка | что ищет | как | область | серьёзность | порог | входы | код | тесты |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `layout.out_of_bounds` | Элемент вышел за границы слайда | по файлу | слайд | ошибка | `tolerance` = 0.005 | composed_deck | `audit/deterministic.py` | `audit/test_repair.py` |
| `layout.overlap` | Два блока наложились друг на друга | по файлу | слайд | ошибка | `min_overlap_ratio` = 0.12 | composed_deck | `audit/deterministic.py` | `audit/test_repair.py` |
| `layout.text_overflow` | Текст не поместился в свою рамку | по файлу | слайд | ошибка | — | composed_deck, font_metrics | `audit/deterministic.py` | `audit/test_repair.py` |
| `layout.clipped` | Текст обрезан краем слайда | по файлу | слайд | ошибка | `tolerance` = 0.005 | composed_deck | `audit/deterministic.py` | — |
| `layout.margins` | Контент заходит в поля у краёв | по файлу | слайд | замечание | `tolerance` = 0.01 | composed_deck | `audit/deterministic.py` | `audit/test_deterministic.py` |
| `layout.image_distorted` | Картинка растянута, пропорции нарушены | по файлу | слайд | замечание | `max_ratio_delta` = 0.08 | composed_deck | `audit/deterministic.py` | `audit/test_deterministic.py` |

### Шаблон

| проверка | что ищет | как | область | серьёзность | порог | входы | код | тесты |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `template.font_not_in_template` | Шрифт не из шаблона | по файлу | слайд | ошибка | — | composed_deck, template_profile | `audit/deterministic.py` | `audit/test_repair.py` |
| `template.font_families` | Больше двух гарнитур на слайде | по файлу | слайд | замечание | `max_families` = 2 | composed_deck, template_profile | `audit/deterministic.py` | — |
| `template.size_not_in_scale` | Кегль не из типографической шкалы шаблона | по файлу | слайд | замечание | `tolerance_pt` = 0.6 | composed_deck, template_profile | `audit/deterministic.py` | `audit/test_repair.py` |
| `template.color_not_in_palette` | Цвет не из палитры шаблона | по файлу | слайд | замечание | `max_distance` = 24 | composed_deck, template_profile | `audit/deterministic.py` | `audit/test_repair.py` |
| `template.layout_not_from_template` | Слайд собран не на макете из шаблона | по файлу | слайд | блокирующая | — | composed_deck, template_profile | `audit/deterministic.py` | — |
| `template.fixed_element_moved` | Логотип или колонтитул сдвинуты с положенного места | по файлу | слайд | ошибка | `max_shift` = 0.01 | composed_deck, template_profile | `audit/deterministic.py` | `audit/test_deterministic.py`, `audit/test_repair.py` |
| `template.contrast` | Контраст текста к фону ниже 4,5:1 | по файлу | слайд | ошибка | `min_ratio` = 4.5, `min_ratio_large_text` = 3.0 | render, composed_deck | `audit/deterministic.py` | `audit/test_deterministic.py`, `audit/test_pixels.py` |
| `template.font_substituted` | Рендерер подменил гарнитуру: в файле одна, на странице другая | по файлу | слайд | замечание | — | render, composed_deck | `audit/deterministic.py` | `audit/test_pixels.py` |

### Плотность

| проверка | что ищет | как | область | серьёзность | порог | входы | код | тесты |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `density.bullets` | Больше шести буллетов на слайде | по файлу | слайд | замечание | `max_bullets` = 6 | composed_deck | `audit/deterministic.py` | — |
| `density.bullet_length` | Буллет длиннее пятнадцати слов | по файлу | слайд | замечание | `max_words` = 15 | composed_deck | `audit/deterministic.py` | — |
| `density.table_size` | Таблица больше семи строк или пяти колонок | по файлу | слайд | замечание | `max_rows` = 7, `max_cols` = 5 | composed_deck | `audit/deterministic.py` | — |
| `density.chart_series` | Больше пяти серий на диаграмме | по файлу | слайд | замечание | `max_series` = 5 | composed_deck | `audit/deterministic.py` | — |
| `density.fill_ratio` | Слайд заполнен меньше четверти или больше трёх четвертей | по файлу | слайд | замечание | `min_ratio` = 0.25, `max_ratio` = 0.75 | composed_deck | `audit/deterministic.py` | `audit/test_repair.py` |

### Целостность

| проверка | что ищет | как | область | серьёзность | порог | входы | код | тесты |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `integrity.package` | Файл открывается и связи целы | по файлу | колода | блокирующая | — | xml | `audit/deterministic.py` | `audit/test_deterministic.py`, `audit/test_package.py` |
| `integrity.placeholder_text` | Остался текст-заглушка | по файлу | слайд | ошибка | `markers` = ['lorem ipsum', 'todo', 'вставьте текст', 'xxx', 'ххх'] | composed_deck | `audit/deterministic.py` | — |
| `integrity.empty_slide` | Пустой слайд или слайд с одним заголовком | по файлу | слайд | ошибка | — | composed_deck | `audit/deterministic.py` | — |
| `integrity.raster_slide` | Слайд оказался картинкой, а не редактируемыми объектами | по файлу | слайд | блокирующая | — | composed_deck | `audit/deterministic.py` | — |
| `integrity.chart_labels` | У диаграммы нет подписей осей, единиц или легенды | по файлу | слайд | замечание | — | composed_deck | `audit/deterministic.py` | `audit/test_deterministic.py` |
| `integrity.duplicate_slides` | Два слайда дублируют друг друга | по файлу | колода | ошибка | — | composed_deck, whole_deck_text | `audit/deterministic.py` | `audit/test_deterministic.py` |

### Содержание

| проверка | что ищет | как | область | серьёзность | порог | входы | код | тесты |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `content.title_is_takeaway` | Заголовок содержит вывод, а не просто называет тему? | моделью | слайд | замечание | `question_id` = 1 | render, composed_deck | `audit/contextual.py (вопрос 1)` | `audit/test_contextual.py` |
| `content.title_matches_body` | Содержимое слайда соответствует заголовку? | моделью | слайд | замечание | `question_id` = 2 | render, composed_deck | `audit/contextual.py (вопрос 2)` | — |
| `content.one_sentence` | Слайд пересказывается одним предложением? | моделью | слайд | замечание | `question_id` = 3 | render, composed_deck | `audit/contextual.py (вопрос 3)` | `audit/test_contextual.py` |
| `content.facts_grounded` | Все цифры и факты со слайда есть в исходных материалах? | моделью | слайд | ошибка | `question_id` = 4 | render, composed_deck, content_package | `audit/contextual.py (вопрос 4)` | `audit/test_contextual.py`, `audit/test_repair.py` |
| `content.has_content` | На слайде есть содержание, а не только заголовок? | моделью | слайд | ошибка | `question_id` = 5 | render, composed_deck | `audit/contextual.py (вопрос 5)` | — |
| `content.visuals_relevant` | Картинки и иконки относятся к теме слайда? | моделью | слайд | замечание | `question_id` = 6 | render, composed_deck | `audit/contextual.py (вопрос 6)` | `audit/test_contextual.py` |
| `content.no_garbage` | Нет служебного мусора: реплик спикера, кусков промпта? | моделью | слайд | ошибка | `question_id` = 7 | render, composed_deck | `audit/contextual.py (вопрос 7)` | `audit/test_contextual.py` |
| `content.no_typos` | Текст без опечаток? | моделью | слайд | замечание | `question_id` = 8 | render, composed_deck | `audit/contextual.py (вопрос 8)` | `audit/test_contextual.py` |
| `content.one_language` | Вся колода на одном языке? | моделью | колода | замечание | `question_id` = 9 | whole_deck_text | `audit/contextual.py (вопрос 9)` | — |
| `content.table_works` | Все строки таблицы и элементы легенды работают на мысль слайда? | моделью | слайд | замечание | `question_id` = 10 | render, composed_deck, content_package | `audit/contextual.py (вопрос 10)` | `audit/test_contextual.py` |
| `content.slides_connected` | Соседние слайды связаны между собой по логике? | моделью | колода | замечание | `question_id` = 11 | render, neighbor_slides | `audit/contextual.py (вопрос 11)` | `audit/test_contextual.py` |

## Чего аудит не делает

* **Исправления по находкам** (`repair`) пока заглушка: новая ревизия повторяет файлы
  предыдущей. Стратегия и цена у находок уже проставлены, применение — следующий шаг.
* **Ответ модели недетерминирован.** Контекстные проверки на повторном запуске могут
  ответить иначе; поэтому их серьёзность — замечание, кроме вопросов о фактах, мусоре
  и пустом слайде.
* **Без модели контекстной части нет.** Отчёт в этом случае помечает все 11 проверок
  `not_checked` с причиной, а не выдаёт их за пройденные.
* **Без рендера** (миниатюр слайдов) вопрос о картинках остаётся непроверенным:
  остальные вопросы модель отвечает по тексту и составу объектов.

Версия реестра проверок: 1.0.

Проверки без отдельного теста: `layout.clipped`, `template.font_families`, `template.layout_not_from_template`, `density.bullets`, `density.bullet_length`, `density.table_size`, `density.chart_series`, `integrity.placeholder_text`, `integrity.empty_slide`, `integrity.raster_slide`, `content.title_matches_body`, `content.has_content`, `content.one_language`. Они выполняются и попадают в отчёт, но доказательства в виде дефектной
фикстуры у них пока нет.
