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

RUN pip install --no-cache-dir .

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
