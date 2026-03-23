# OpenAkita Dockerfile
# 基于官方 Python 镜像
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 设置环境变量
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    wget \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 复制应用文件
COPY . .

# 安装 Python 依赖
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

# 创建非 root 用户并设置目录权限
RUN useradd --create-home --shell /bin/bash openakita \
    && mkdir -p /app/data /app/identity /app/logs \
    && chown -R openakita:openakita /app/data /app/identity /app/logs

USER openakita

# 暴露端口（OpenAkita 默认端口 18900）
EXPOSE 18900

# 启动服务模式（API + IM 通道）
CMD ["openakita", "serve"]
