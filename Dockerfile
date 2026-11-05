# syntax=docker/dockerfile:1
FROM node:22-bookworm-slim AS node
FROM ghcr.io/astral-sh/uv:0.13.4 AS uv
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PATH="/app/.venv/bin:$PATH"
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=uv /uv /usr/local/bin/uv
# Optional build secret keeps managed-cloud proxy CA trust out of image layers.
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then \
      apt-get -o Acquire::https::CaInfo=/run/secrets/proxy_ca update; \
    else apt-get update; fi \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home app \
    && mkdir /data && chown app:app /data
WORKDIR /app
COPY --chown=app:app pyproject.toml uv.lock README.md ./
COPY --chown=app:app src ./src
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then \
      SSL_CERT_FILE=/run/secrets/proxy_ca uv sync --locked --no-dev; \
    else uv sync --locked --no-dev; fi
USER app
CMD ["python", "-m", "lovi", "bot"]
