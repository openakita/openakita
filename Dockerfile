# ── Stage 1: Build web frontend ──
FROM node:20-slim AS frontend

WORKDIR /app/apps/setup-center
COPY apps/setup-center/package.json apps/setup-center/package-lock.json ./
RUN npm ci
COPY apps/setup-center/ ./
RUN npm run build:web

# ── Stage 2: Build Python package ──
FROM python:3.11-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential git \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src/ src/
COPY skills/ skills/
COPY mcps/ mcps/
COPY identity/ identity/
# Bundle the plugin SDK so AI media plugins (openakita_plugin_sdk.contrib)
# resolve at runtime without relying on PyPI 0.3.0 being published.
COPY openakita-plugin-sdk/ openakita-plugin-sdk/

COPY --from=frontend /app/apps/setup-center/dist-web/ apps/setup-center/dist-web/
RUN mkdir -p docs-site/.vitepress/dist

RUN pip install --no-cache-dir . \
    && pip install --no-cache-dir ./openakita-plugin-sdk

# ── Stage 3: Final runtime image ──
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin/openakita /usr/local/bin/openakita

COPY src/ src/
COPY skills/ skills/
COPY identity/ identity/

ENV PYTHONUNBUFFERED=1
EXPOSE 18900

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:18900/health || exit 1

ENTRYPOINT ["openakita"]
CMD ["serve", "--host", "0.0.0.0", "--port", "18900"]
