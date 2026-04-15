# OpenAkita Dockerfile

# ------------------------------
# Stage 1: Build web frontend
# ------------------------------
FROM node:20-slim AS web-builder

WORKDIR /build/apps/setup-center

COPY apps/setup-center/package.json apps/setup-center/package-lock.json ./
RUN npm ci

COPY apps/setup-center /build/apps/setup-center
COPY src/openakita/llm/registries/providers.json /build/src/openakita/llm/registries/providers.json

RUN npm run build:web

# ------------------------------
# Stage 2: Python runtime
# ------------------------------
FROM python:3.11-slim

# 设置环境变量
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PROJECT_ROOT=/workspace

# 代码安装与打包在 /app 下完成；运行时工作区使用独立的 /workspace，
# 避免把不可变镜像层和可写配置/数据混在一起。
WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    wget \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 复制应用文件
COPY . .

# 注入构建好的 Web 前端，并为 docs 生成最小占位目录，
# 以满足 pyproject.toml 中 hatch force-include 的打包要求。
COPY --from=web-builder /build/apps/setup-center/dist-web /app/apps/setup-center/dist-web
RUN mkdir -p /app/docs-site/.vitepress/dist \
    && printf '%s\n' \
    '<!doctype html>' \
    '<html><head><meta charset="utf-8"><title>OpenAkita Docs</title></head>' \
    '<body><h1>OpenAkita Docs Not Bundled</h1><p>Docs are not included in this image build.</p></body></html>' \
    > /app/docs-site/.vitepress/dist/index.html

# 安装 Python 依赖
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

# 创建非 root 用户并设置目录权限
RUN useradd --create-home --shell /bin/bash openakita \
    && mkdir -p /workspace/data /workspace/identity /workspace/logs \
    && chown -R openakita:openakita /workspace

USER openakita

WORKDIR /workspace

# 暴露端口（OpenAkita 默认端口 18900）
EXPOSE 18900

# 启动服务模式（API + IM 通道）
CMD ["openakita", "serve"]
