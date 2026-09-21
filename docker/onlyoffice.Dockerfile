# One document engine for editing and conversion. Include the same project fonts as workers.
FROM onlyoffice/documentserver:9.3.1
RUN apt-get update && apt-get install -y --no-install-recommends \
        fonts-dejavu-core fonts-liberation fonts-crosextra-carlito fonts-crosextra-caladea \
        fonts-noto-core fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*
COPY docker/fonts /usr/local/share/fonts/project
RUN fc-cache -f && /usr/bin/documentserver-generate-allfonts.sh
COPY scripts/patch_onlyoffice_rendering.py /usr/local/bin/patch_onlyoffice_rendering.py
RUN python3 /usr/local/bin/patch_onlyoffice_rendering.py