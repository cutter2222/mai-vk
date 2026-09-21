# One document engine for editing and conversion. Include the same project fonts as workers.
FROM onlyoffice/documentserver:9.3.1
RUN apt-get update && apt-get install -y --no-install-recommends \
        fonts-dejavu-core fonts-liberation fonts-crosextra-carlito fonts-crosextra-caladea \
        fonts-noto-core fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*
COPY docker/fonts /usr/local/share/fonts/project
RUN fc-cache -f && /usr/bin/documentserver-generate-allfonts.sh
# Патч интервала отрисовки (scripts/patch_onlyoffice_rendering.py) при сборке не применяется:
# web-apps/apps/api/documents/api.js создаётся entrypoint из api.js.tpl только при старте
# контейнера, и fail-closed проверка скрипта на этапе сборки заведомо не проходит.
# Интервал остаётся штатным (40 мс); скрипт годится для живого Document Server.