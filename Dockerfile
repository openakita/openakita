# ── Stage 1: Build web frontend ──
FROM node:20-slim AS frontend

WORKDIR /app/apps/setup-center
COPY apps/setup-center/package.json apps/setup-center/package-lock.json ./
RUN npm ci
COPY apps/setup-center/ ./
COPY src/openakita/llm/registries/providers.json /app/src/openakita/llm/registries/providers.json
RUN npm run build:web

# ── Stage 2: Build Python package ──
FROM python:3.11-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential git \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md VERSION hatch_build.py ./
COPY scripts/write_build_version.py scripts/write_build_version.py
COPY src/ src/
COPY skills/ skills/
COPY mcps/ mcps/
COPY identity/ identity/
# Bundle the plugin SDK so first-class plugins (tongyi-image / seedance-video)
# resolve `openakita_plugin_sdk` at runtime without relying on PyPI publish.
COPY openakita-plugin-sdk/ openakita-plugin-sdk/

COPY --from=frontend /app/apps/setup-center/dist-web/ apps/setup-center/dist-web/
RUN mkdir -p docs-site/.vitepress/dist

# Build arg: opt in to the finance-auto plugin extra (openpyxl, xlrd,
# xltpl, keyring, cryptography — see pyproject.toml). Off by default
# so plain OpenAkita installs stay slim. Build with:
#     docker build --build-arg INSTALL_FINANCE_AUTO=1 -t openakita:fa .
# Docs: plugins/finance-auto/docs/DEPLOY_DOCKER.md §3.
ARG INSTALL_FINANCE_AUTO=0
ARG OPENAKITA_BUILD_GIT_HASH=dev

RUN if [ "$INSTALL_FINANCE_AUTO" = "1" ]; then \
        pip install --no-cache-dir ".[finance-auto]"; \
    else \
        pip install --no-cache-dir .; \
    fi \
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
    CMD curl -f http://localhost:18900/api/health || exit 1

ENTRYPOINT ["openakita"]
CMD ["serve"]
