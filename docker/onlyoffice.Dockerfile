# One document engine for editing and conversion. Include the same project fonts as workers.
FROM onlyoffice/documentserver:9.3.1
RUN apt-get update && apt-get install -y --no-install-recommends \
        fonts-dejavu-core fonts-liberation fonts-crosextra-carlito fonts-crosextra-caladea \
        fonts-noto-core fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*
COPY docker/fonts /usr/local/share/fonts/project
# allfontsgen 9.3 does not discover /usr/local/share/fonts via --use-system.
# Put the same files in its explicit input; startup regeneration uses this directory too.
COPY docker/fonts /var/www/onlyoffice/documentserver/core-fonts/project
RUN fc-cache -f && /usr/bin/documentserver-generate-allfonts.sh \
    && for index in \
        /var/www/onlyoffice/documentserver/sdkjs/common/AllFonts.js \
        /var/www/onlyoffice/documentserver/server/FileConverter/bin/AllFonts.js; do \
        for family in Play Montserrat Poppins; do grep -q "\"$family\"" "$index" || exit 1; done; \
    done
# Патч интервала отрисовки (scripts/patch_onlyoffice_rendering.py) при сборке не применяется:
# web-apps/apps/api/documents/api.js создаётся entrypoint из api.js.tpl только при старте
# контейнера, и fail-closed проверка скрипта на этапе сборки заведомо не проходит.
# Интервал остаётся штатным (40 мс); скрипт годится для живого Document Server.