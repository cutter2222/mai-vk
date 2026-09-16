# export

Слой экспорта: PDF через LibreOffice, миниатюры через pypdfium2, автономный HTML из ComposedDeck, кэш рендера, слоты рендера.

Вход: PPTX, ComposedDeck. Выход: PDF, PNG, HTML, манифест артефактов.

Есть с этапа 0A:

- `pdf.py` — `convert_to_pdf()`: отдельный процесс LibreOffice на конвертацию, временный профиль (копия подготовленного `/opt/lo-profile` из образа воркера, если есть), тайм-аут с завершением дерева процессов, очистка; `find_soffice()`, `soffice_version()`. Проверка рендерера при старте воркера (`pipeline/jobs.py: renderer_check`) использует эти же функции.
- `thumbnails.py` — `render_thumbnails()`: страницы PDF в PNG заданной ширины; все вызовы PDFium под одной блокировкой процесса, параллельность только процессами.

- `deck.py` — `export_revision()`: экспорт ревизии варианта в каталог staging: `deck.pdf` через LibreOffice под слотом рендера, `thumbs/slide-NN.png` через PDFium (ширина `render.thumbnail_width_px`), `deck.html` — промежуточный автономный файл: картинки страниц data-URI с текстом слайдов из ComposedDeck под каждой. Подключён в `pipeline/real.py` (`export: real` там, где есть LibreOffice; иначе заглушка с отметкой `stub`).

Полный слой (детерминированный HTML из ComposedDeck, кэш рендера, визуальное сравнение) — этап 9. Замеры и ограничения: `docs/pptx-capabilities.md`.
