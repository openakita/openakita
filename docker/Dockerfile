# OpenAkita Dockerfile
# Builds the web frontend and user docs before packaging the Python wheel.

FROM node:20-bookworm-slim AS web-builder

WORKDIR /build/apps/setup-center

COPY apps/setup-center/package.json apps/setup-center/package-lock.json ./
RUN npm ci

COPY apps/setup-center/ ./
COPY src/openakita/llm/registries/providers.json /build/src/openakita/llm/registries/providers.json

RUN npm run build:web


FROM node:20-bookworm-slim AS docs-builder

WORKDIR /build/docs-site

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY docs-site/package.json docs-site/package-lock.json ./
RUN npm ci

COPY docs-site/ ./
COPY VERSION /build/VERSION

RUN mkdir -p .vitepress \
    && VERSION_VALUE="$(cat /build/VERSION)" \
    && printf '{\n  "version": "%s"\n}\n' "$VERSION_VALUE" > .vitepress/version.json \
    && npm run build


FROM python:3.11-slim AS wheel-builder

WORKDIR /build

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md LICENSE VERSION ./
COPY src ./src
COPY skills ./skills
COPY mcps ./mcps
COPY identity ./identity
COPY apps/setup-center ./apps/setup-center
COPY docs-site ./docs-site

COPY --from=web-builder /build/apps/setup-center/dist-web ./apps/setup-center/dist-web
COPY --from=docs-builder /build/docs-site/.vitepress/dist ./docs-site/.vitepress/dist

RUN python -m pip install --upgrade pip build \
    && python -m build --wheel \
    && mkdir -p /wheelhouse \
    && python -m pip wheel --wheel-dir /wheelhouse /build/dist/*.whl


FROM python:3.11-slim AS runtime

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    git \
    wget \
    && rm -rf /var/lib/apt/lists/*

COPY --from=wheel-builder /wheelhouse /wheelhouse

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir --no-index --find-links=/wheelhouse openakita \
    && rm -rf /wheelhouse

RUN useradd --create-home --shell /bin/bash openakita \
    && mkdir -p /app/data /app/identity /app/logs \
    && chown -R openakita:openakita /app

USER openakita

EXPOSE 18900

CMD ["openakita", "serve"]
