#!/bin/bash
# MVP 开发环境启动脚本
# Linux/Mac 版本

echo "🚀 开始部署 MVP 开发环境..."

# 检查 Docker
echo -e "\n📦 检查 Docker..."
docker --version
if [ $? -ne 0 ]; then
    echo "❌ Docker 未安装或未启动"
    exit 1
fi
echo "✅ Docker 检查通过"

# 创建数据目录
echo -e "\n📁 创建数据目录..."
mkdir -p data/qdrant_storage data/redis_data data/mlflow data/mlflow/artifacts
echo "✅ 目录创建完成"

# 启动 Docker 服务
echo -e "\n🐳 启动 Docker 服务 (Qdrant + Redis + MLflow)..."
docker-compose up -d
if [ $? -ne 0 ]; then
    echo "❌ Docker 服务启动失败"
    exit 1
fi
echo "✅ Docker 服务启动成功"

# 等待服务就绪
echo -e "\n⏳ 等待服务就绪..."
sleep 10

# 检查服务状态
echo -e "\n📊 检查服务状态..."
docker-compose ps

# 安装 Python 依赖
echo -e "\n📦 安装 Python 依赖..."
if [ -d "../.venv" ]; then
    echo "激活虚拟环境..."
    source ../.venv/bin/activate
fi

pip install -r requirements.txt
if [ $? -ne 0 ]; then
    echo "⚠️  Python 依赖安装失败，请手动执行：pip install -r requirements.txt"
else
    echo "✅ Python 依赖安装完成"
fi

# 验证服务
echo -e "\n🔍 验证服务..."

# 验证 Qdrant
echo "  检查 Qdrant (http://localhost:6333)..."
if curl -s http://localhost:6333/ > /dev/null; then
    echo "  ✅ Qdrant 运行正常"
else
    echo "  ⚠️  Qdrant 未响应"
fi

# 验证 Redis
echo "  检查 Redis (localhost:6379)..."
if redis-cli ping > /dev/null 2>&1; then
    echo "  ✅ Redis 运行正常"
else
    echo "  ⚠️  Redis 未响应"
fi

# 验证 MLflow
echo "  检查 MLflow (http://localhost:5000)..."
if curl -s http://localhost:5000/ > /dev/null; then
    echo "  ✅ MLflow 运行正常"
else
    echo "  ⚠️  MLflow 未响应"
fi

echo -e "\n🎉 MVP 开发环境部署完成！"
echo -e "\n📝 访问地址:"
echo "  - Qdrant:   http://localhost:6333"
echo "  - Redis:    localhost:6379"
echo "  - MLflow:   http://localhost:5000"
echo "  - Flower:   http://localhost:5555 (启动 Celery 后)"

echo -e "\n🚀 启动 Celery Worker:"
echo "  celery -A celery_config worker --loglevel=info -Q high_priority,default,low_priority"

echo -e "\n📚 查看文档：README.md"
