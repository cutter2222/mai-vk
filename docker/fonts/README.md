# Шрифты образа воркера

Устанавливаются в `/usr/local/share/fonts/project` при сборке `docker/app.Dockerfile` (цель `worker`); системные шрифты ставятся пакетами Debian. Runtime шрифты не скачивает.

| Шрифт | Откуда | Лицензия | Зачем |
| --- | --- | --- | --- |
| Play Regular, Play Bold 2.101 | [Google Fonts](https://fonts.google.com/specimen/Play), каталог `play/` | SIL OFL 1.1 (`play/OFL.txt`) | основной шрифт шаблонов VK; встроен в PPTX, но LibreOffice берёт установленный файл |
| Montserrat Regular, Medium, SemiBold, Bold | [Google Fonts](https://fonts.google.com/specimen/Montserrat) (репозиторий `google/fonts`, `ofl/montserrat`), статические начертания получены из вариативного `Montserrat[wght].ttf` через `fontTools.varLib.instancer` (wght 400/500/600/700); каталог `montserrat/` | SIL OFL 1.1 (`montserrat/OFL.txt`) | основной шрифт шаблона ЛЦТ 2026 (104 употребления в образцах); без него LibreOffice подставлял Noto Sans |
| Poppins Light, Regular, Bold | [Google Fonts](https://fonts.google.com/specimen/Poppins) (`ofl/poppins`), каталог `poppins/` | SIL OFL 1.1 (`poppins/OFL.txt`) | «Poppins Light» в шаблоне ЛЦТ (семейство в PPTX названо так же, как в таблице `name` файла) |
| Liberation Sans/Serif/Mono | пакет `fonts-liberation` | SIL OFL 1.1 | метрическая замена Arial, Times New Roman, Courier New |
| Carlito | пакет `fonts-crosextra-carlito` | SIL OFL 1.1 | метрическая замена Calibri |
| Noto Sans и др. | пакет `fonts-noto-core` | SIL OFL 1.1 | кириллица и символы, запасной шрифт |
| DejaVu Sans | пакет `fonts-dejavu-core` | Bitstream Vera / public domain дополнения | запасной шрифт LibreOffice |

Consolas (VK Tech, VK Education) в образе отсутствует: шрифт проприетарный, LibreOffice заменяет его DejaVu Sans Mono (строки кода переносятся иначе, чем в PowerPoint); подмена записывается в профиль шаблона (`design_tokens.typography.fonts[].fallback`) и в ComposedDeck (`fonts[]`). Lato из ЛЦТ в образцах не употребляется и не добавлен. Факты подмены — в отчёте рендера (`docs/pptx-capabilities.md`).
