# Образы API, воркера и распознавания речи из одного Dockerfile: общая база с зависимостями
# Python, цель api, цель worker со шрифтами для измерения текста и цель asr (голосовой ввод).
# Рендер — в ONLYOFFICE.
# Контекст сборки — корень репозитория (см. .dockerignore). Сборка: docker compose build.
# Без директивы syntax: встроенный frontend BuildKit покрывает нужное, а лишний образ
# docker/dockerfile с Docker Hub считается в лимит анонимных запросов (10 в час на IP).

FROM python:3.12.14-slim-bookworm AS eot-builder
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates git gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*
# Immutable upstream source; keep its MPL-2.0 source, LICENSE and PATENTS in the image.
RUN git clone https://github.com/umanwizard/libeot.git /src/libeot \
    && cd /src/libeot \
    && git checkout --detach 0407abddc581d32e9871ee41535183ee1d924d85 \
    && test "$(git rev-parse HEAD)" = 0407abddc581d32e9871ee41535183ee1d924d85 \
    && gcc -std=c99 -O2 -DDECOMPRESS_ON -Iinc $(find src -name '*.c') -o /eot2ttf \
    && git archive HEAD | gzip -n > /libeot-source.tar.gz

FROM python:3.12.14-slim-bookworm AS base
COPY --from=eot-builder /eot2ttf /usr/local/bin/eot2ttf
COPY --from=eot-builder /src/libeot/LICENSE /src/libeot/PATENTS /libeot-source.tar.gz /usr/local/share/doc/libeot/
COPY --from=ghcr.io/astral-sh/uv:0.12.14 /uv /bin/uv
ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp
WORKDIR /app
# Сначала зависимости по lock-файлу, чтобы слой переиспользовался при правках кода.
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY config ./config
COPY contracts ./contracts
COPY skills ./skills
COPY tests/fixtures/pptx ./tests/fixtures/pptx
# Собственный контент-пакет для проб импорта и плана из контейнера (64 КБ).
COPY examples/content ./examples/content
RUN uv sync --frozen --no-dev \
    && rm -rf /root/.cache/uv \
    && mkdir -p /app/data /app/artifacts /app/runs \
    && chmod -R a+rwX /app/data /app/artifacts /app/runs

FROM base AS asr
# Распознавание речи для голосового ввода в чате: onnxruntime и sentencepiece из экстры speech,
# без шрифтов, рендера и torch. Веса GigaAM — том /app/models (make asr-model), в образ не входят.
RUN uv sync --frozen --no-dev --extra speech --no-cache
# Меньше арен malloc: после выгрузки модели процесс отдаёт память, а не держит пик.
ENV MALLOC_ARENA_MAX=2
EXPOSE 8010
CMD ["python", "-m", "presentation_designer.cli.asr"]

FROM base AS api
EXPOSE 8000
CMD ["python", "-m", "presentation_designer.api", "--host", "0.0.0.0", "--port", "8000"]

FROM base AS worker
# Шрифты для измерения текста при AI-композиции (docker/fonts/README.md).
# Шрифты Microsoft (Arial, Times New Roman, Courier New, Verdana, Georgia, Trebuchet…) ставятся
# пакетом contrib ttf-mscorefonts-installer: он скачивает оригинальные дистрибутивы Microsoft
# при сборке (лицензия разрешает их распространение в неизменном виде).
RUN sed -i 's/^Components: main$/Components: main contrib/' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && echo 'ttf-mscorefonts-installer msttcorefonts/accepted-mscorefonts-eula select true' \
        | debconf-set-selections \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        fontconfig \
        fonts-liberation fonts-dejavu-core fonts-crosextra-carlito fonts-crosextra-caladea \
        fonts-noto-core cabextract ttf-mscorefonts-installer \
    && rm -rf /var/lib/apt/lists/*
COPY docker/fonts /usr/local/share/fonts/project
RUN fc-cache -f
CMD ["python", "-m", "presentation_designer.cli.worker", "--queues", "interactive", "generation"]
