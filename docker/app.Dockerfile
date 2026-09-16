# syntax=docker/dockerfile:1
# Образы API и воркера из одного Dockerfile: общая база с зависимостями Python,
# цель api без LibreOffice, цель worker с LibreOffice, шрифтами и проверкой рендерера.
# Контекст сборки — корень репозитория (см. .dockerignore). Сборка: docker compose build.

FROM python:3.12.14-slim-bookworm AS base
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
RUN uv sync --frozen --no-dev \
    && rm -rf /root/.cache/uv \
    && mkdir -p /app/data /app/artifacts /app/runs \
    && chmod -R a+rwX /app/data /app/artifacts /app/runs

FROM base AS api
EXPOSE 8000
CMD ["python", "-m", "presentation_designer.api", "--host", "0.0.0.0", "--port", "8000"]

FROM base AS worker
# LibreOffice для конвертации в PDF и шрифты с открытыми лицензиями; профиль создаётся на каждую конвертацию.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libreoffice-impress libreoffice-calc \
        fonts-liberation fonts-dejavu-core fonts-crosextra-carlito fonts-noto-core \
    && rm -rf /var/lib/apt/lists/*
COPY docker/fonts /usr/local/share/fonts/project
RUN fc-cache -f
CMD ["python", "-m", "presentation_designer.cli.worker", "--queues", "generation"]
