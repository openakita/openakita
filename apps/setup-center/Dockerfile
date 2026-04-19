# ─── Stage 1: Build ──────────────────────────────────────────────────────────
FROM node:20-alpine AS builder

WORKDIR /workspace

# Copy the monorepo paths that the build needs:
#   apps/setup-center  →  the frontend project
#   src/openakita/llm/registries/providers.json  →  @shared/providers.json alias
COPY apps/setup-center/package.json apps/setup-center/package-lock.json ./apps/setup-center/
COPY src/openakita/llm/registries/providers.json ./src/openakita/llm/registries/providers.json

# Install dependencies (ci = reproducible, production-safe)
RUN cd apps/setup-center && npm ci

# Copy the rest of the frontend source
COPY apps/setup-center/ ./apps/setup-center/

# Build for web target  →  output in apps/setup-center/dist-web/
RUN cd apps/setup-center && npm run build:web


# ─── Stage 2: Serve ──────────────────────────────────────────────────────────
FROM nginx:1.27-alpine

# gettext provides envsubst for runtime variable substitution
RUN apk add --no-cache gettext

# Remove default nginx content
RUN rm -rf /usr/share/nginx/html/*

# Copy built assets under /web/ to match vite base="/web/"
COPY --from=builder /workspace/apps/setup-center/dist-web/ /usr/share/nginx/html/web/

# Copy nginx config template; actual config is written at startup
COPY apps/setup-center/nginx.conf /etc/nginx/templates/default.conf.template

# Startup script: substitute env vars → write nginx config → start nginx
COPY apps/setup-center/docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# Default backend location (override via docker-compose or -e flags)
ENV BACKEND_HOST=host.docker.internal
ENV BACKEND_PORT=18900

EXPOSE 80

ENTRYPOINT ["/docker-entrypoint.sh"]
