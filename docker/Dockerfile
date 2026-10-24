# «Джарвис» в Docker. Требуется Docker с поддержкой linux/amd64 (или arm64).
FROM python:3.12-slim

# uv — быстрый менеджер окружений; ставим в системный PATH.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Сначала только манифесты — кеш слоёв не инвалидируется при правке кода.
COPY pyproject.toml uv.lock .python-version README.md ./
RUN uv sync --frozen --no-dev

COPY src ./src
COPY config ./config
COPY .env.example ./

# data/ монтируется томом (БД, сессии, журнал) — не копируем.
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

# По умолчанию — CLI-справка; в compose переопределяется на нужную команду.
ENTRYPOINT ["jarvis"]
CMD ["--help"]
