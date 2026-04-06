# MVP 项目标准 Dockerfile
# 基于 Python 3.11 + Node.js 18
# 用于统一开发/测试/生产环境

FROM python:3.11-slim-bookworm AS base

# 设置工作目录
WORKDIR /app

# 设置环境变量
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    POETRY_VERSION=1.7.1 \
    POETRY_HOME="/opt/poetry" \
    POETRY_VIRTUALENVS_IN_PROJECT=true \
    POETRY_NO_INTERACTION=1 \
    NODE_VERSION=18.19.0

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gnupg \
    build-essential \
    libpq-dev \
    git \
    && rm -rf /var/lib/apt/lists/*

# 安装 Poetry (Python 包管理)
RUN curl -sSL https://install.python-poetry.org | python3 -

# 安装 Node.js 18
RUN curl -fsSL https://deb.nodesource.com/setup_18.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# 将 Poetry 添加到 PATH
ENV PATH="$POETRY_HOME/bin:$PATH"

# ============================================
# 开发阶段
# ============================================
FROM base AS development

# 安装开发工具
RUN apt-get update && apt-get install -y --no-install-recommends \
    vim \
    less \
    netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

# 复制项目文件
COPY pyproject.toml poetry.lock* ./

# 安装依赖（包含开发依赖）
RUN poetry install --no-root

# 复制源代码
COPY . .

# 暴露端口
EXPOSE 8000

# 默认命令
CMD ["poetry", "run", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ============================================
# 生产阶段
# ============================================
FROM base AS production

# 创建非 root 用户
RUN useradd --create-home --shell /bin/bash appuser

# 复制项目文件
COPY pyproject.toml poetry.lock* ./

# 安装依赖（仅生产依赖）
RUN poetry install --no-root --only main \
    && poetry cache clear . --all

# 复制源代码
COPY --chown=appuser:appuser . .

# 切换用户
USER appuser

# 暴露端口
EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# 默认命令
CMD ["poetry", "run", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
