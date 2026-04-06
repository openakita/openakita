# MVP 项目 Docker 标准化环境

> 统一开发/测试/生产环境配置，减少开发配置调试时间

## 📋 目录结构

```
docker/mvp/
├── Dockerfile          # 应用镜像构建文件
├── docker-compose.yml  # 多服务编排配置
├── .env.example        # 环境变量模板
├── init.sql           # 数据库初始化脚本
├── prometheus.yml     # 监控配置（可选）
└── README.md          # 本文档
```

## 🚀 快速开始

### 前置要求

- Docker Desktop 20.10+ (或 Docker Engine)
- Docker Compose 2.0+
- 8GB+ 内存 (推荐 16GB)
- 20GB+ 可用磁盘空间

### 1. 克隆项目并进入目录

```bash
cd docker/mvp
```

### 2. 配置环境变量

```bash
# 复制环境变量模板
cp .env.example .env

# 编辑 .env 文件，填写必要的配置
# 至少需要配置：
# - DATABASE_URL
# - REDIS_URL
# - QDRANT_URL
# - 大模型 API Key (DASHSCOPE_API_KEY 等)
```

### 3. 启动服务

```bash
# 启动所有服务（后台运行）
docker-compose up -d

# 查看服务状态
docker-compose ps

# 查看应用日志
docker-compose logs -f app
```

### 4. 验证服务

```bash
# 检查应用健康状态
curl http://localhost:8000/health

# 检查数据库连接
docker-compose exec db pg_isready -U postgres -d mvp

# 检查 Redis 连接
docker-compose exec redis redis-cli ping

# 检查 Qdrant 连接
curl http://localhost:6333/
```

### 5. 停止服务

```bash
# 停止所有服务
docker-compose down

# 停止并删除数据卷（谨慎使用！）
docker-compose down -v
```

## 🔧 开发环境使用

### 进入应用容器

```bash
# 进入应用容器 bash
docker-compose exec app bash

# 进入应用容器（指定用户）
docker-compose exec -u appuser app bash
```

### 安装依赖

```bash
# 在容器内安装 Python 依赖
docker-compose exec app poetry install

# 安装特定依赖
docker-compose exec app poetry add package-name
```

### 运行数据库迁移

```bash
# 执行数据库迁移
docker-compose exec app poetry run alembic upgrade head

# 查看迁移状态
docker-compose exec app poetry run alembic current
```

### 运行测试

```bash
# 运行全部测试
docker-compose exec app poetry run pytest tests/ -v

# 运行测试并生成覆盖率报告
docker-compose exec app poetry run pytest --cov=src --cov-report=html
```

### 访问服务

| 服务 | 地址 | 说明 |
|------|------|------|
| 应用服务 | http://localhost:8000 | FastAPI 应用 |
| API 文档 | http://localhost:8000/docs | Swagger UI |
| PostgreSQL | localhost:5432 | 数据库 |
| Redis | localhost:6379 | 缓存/队列 |
| Qdrant | http://localhost:6333 | 向量数据库 |
| Grafana | http://localhost:3000 | 监控面板 (admin/admin) |

## 📦 生产环境部署

### 1. 构建生产镜像

```bash
# 构建生产镜像
docker-compose -f docker-compose.prod.yml build

# 或使用 Docker Buildx 多平台构建
docker buildx build --platform linux/amd64,linux/arm64 -t mvp-app:latest .
```

### 2. 部署到服务器

```bash
# 上传配置文件到服务器
scp .env.prod user@server:/path/to/mvp/
scp docker-compose.prod.yml user@server:/path/to/mvp/

# 在服务器上启动服务
ssh user@server
cd /path/to/mvp
docker-compose -f docker-compose.prod.yml up -d
```

### 3. 健康检查

```bash
# 检查服务状态
docker-compose ps

# 查看应用日志
docker-compose logs -f app

# 检查健康状态
curl http://localhost:8000/health
```

## 🔍 故障排查

### 常见问题

#### 1. 容器启动失败

```bash
# 查看容器日志
docker-compose logs app

# 检查端口占用
netstat -ano | findstr :8000

# 重启服务
docker-compose restart app
```

#### 2. 数据库连接失败

```bash
# 检查数据库容器状态
docker-compose ps db

# 查看数据库日志
docker-compose logs db

# 测试数据库连接
docker-compose exec db pg_isready -U postgres -d mvp
```

#### 3. 内存不足

```bash
# 查看资源使用
docker stats

# 限制容器内存（在 docker-compose.yml 中）
services:
  app:
    deploy:
      resources:
        limits:
          memory: 2G
```

### 数据备份

```bash
# 备份 PostgreSQL 数据
docker-compose exec db pg_dump -U postgres mvp > backup.sql

# 恢复 PostgreSQL 数据
docker-compose exec -T db psql -U postgres mvp < backup.sql

# 备份 Redis 数据
docker-compose exec redis redis-cli SAVE
cp redis_data/dump.rdb backup-redis.rdb
```

## 📊 监控配置（可选）

### 启用 Prometheus + Grafana

1. 取消 `docker-compose.yml` 中监控服务的注释
2. 创建 `prometheus.yml` 配置文件
3. 启动服务：`docker-compose up -d prometheus grafana`
4. 访问 Grafana：http://localhost:3000 (admin/admin)

### 配置告警

在 `prometheus.yml` 中添加告警规则：

```yaml
alerting:
  alertmanagers:
    - static_configs:
        - targets:
          - alertmanager:9093

rule_files:
  - "alert_rules.yml"
```

## 🔐 安全建议

1. **生产环境**：
   - 修改默认密码（PostgreSQL/Redis/Grafana）
   - 使用强随机 JWT_SECRET_KEY
   - 启用 HTTPS
   - 限制 CORS 来源

2. **敏感信息**：
   - 不要将 `.env` 文件提交到 Git
   - 使用 Docker Secrets 或环境变量管理敏感配置

3. **网络隔离**：
   - 生产环境使用独立网络
   - 限制容器间通信

## 📝 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 1.0.0 | 2026-03-17 | 初始版本，包含基础服务 |

## 📞 支持

如有问题，请联系 DevOps 工程师或提交 Issue。
