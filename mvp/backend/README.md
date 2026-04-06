# Project Phoenix MVP - 后端服务

## 快速启动

### 开发环境（Docker Compose）

```bash
# 启动所有服务（应用 + 数据库 + Redis + Qdrant）
docker-compose up -d

# 查看日志
docker-compose logs -f app

# 停止服务
docker-compose down
```

访问：
- API 文档：http://localhost:8000/docs
- 健康检查：http://localhost:8000/health

### 本地开发

```bash
# 安装依赖
pip install -r requirements.txt

# 设置环境变量
cp .env.example .env
# 编辑 .env 配置数据库连接等

# 初始化数据库
python -m scripts.init_db

# 启动服务
python -m uvicorn mvp.backend.main:app --reload --host 0.0.0.0 --port 8000

# 启动 Celery Worker
celery -A mvp.backend.core.celery_app worker --loglevel=info

# 启动 Celery Beat（定时任务）
celery -A mvp.backend.core.celery_app beat --loglevel=info
```

## 项目结构

```
mvp/backend/
├── main.py              # FastAPI 应用入口
├── database.py          # 数据库配置
├── models/              # SQLAlchemy 模型
│   └── __init__.py      # 用户/工作流/权限等模型
├── schemas/             # Pydantic 模式
│   ├── user.py          # 用户相关模式
│   ├── workflow.py      # 工作流相关模式
│   └── permission.py    # 权限相关模式
├── routes/              # API 路由
│   ├── auth.py          # 认证授权路由
│   └── workflows.py     # 工作流管理路由
├── core/                # 核心模块
│   ├── security.py      # 安全工具（密码哈希/JWT）
│   └── celery_app.py    # Celery 配置
└── tasks/               # Celery 任务
    └── workflow_tasks.py # 工作流执行任务
```

## API 端点

### 认证授权
- `POST /api/auth/register` - 用户注册
- `POST /api/auth/login` - 用户登录
- `POST /api/auth/refresh` - 刷新令牌
- `GET /api/auth/me` - 获取当前用户信息
- `POST /api/auth/logout` - 注销

### 工作流管理
- `POST /api/workflows` - 创建工作流
- `GET /api/workflows` - 获取工作流列表
- `GET /api/workflows/{id}` - 获取工作流详情
- `PUT /api/workflows/{id}` - 更新工作流
- `DELETE /api/workflows/{id}` - 删除工作流
- `POST /api/workflows/{id}/run` - 运行工作流
- `GET /api/workflows/{id}/instances` - 获取执行实例列表

## 数据库模型

### 用户与认证
- `users` - 用户表
- `refresh_tokens` - 刷新令牌表（支持 rotating）

### RBAC 权限
- `roles` - 角色表
- `permissions` - 权限表
- `role_permissions` - 角色权限关联
- `user_roles` - 用户角色关联

### 工作流
- `workflows` - 工作流定义
- `workflow_instances` - 执行实例
- `workflow_logs` - 执行日志

### API 集成
- `api_integrations` - API 集成配置
- `api_credentials` - API 凭证

## 环境变量

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `DATABASE_URL` | PostgreSQL 连接字符串 | `postgresql://postgres:postgres@localhost:5432/openakita_mvp` |
| `REDIS_URL` | Redis 连接字符串 | `redis://localhost:6379/0` |
| `CELERY_BROKER_URL` | Celery Broker | `redis://localhost:6379/1` |
| `CELERY_RESULT_BACKEND` | Celery 结果后端 | `redis://localhost:6379/2` |
| `JWT_SECRET_KEY` | JWT 密钥 | `your-secret-key` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | 访问令牌过期时间 | `30` |
| `REFRESH_TOKEN_EXPIRE_DAYS` | 刷新令牌过期时间 | `7` |
| `INIT_DB_ON_START` | 启动时初始化数据库 | `false` |
| `ALLOWED_ORIGINS` | CORS 允许的来源 | `http://localhost:3000` |

## 安全特性

### JWT + httpOnly Cookie
- 访问令牌（Access Token）：JWT，30 分钟有效期
- 刷新令牌（Refresh Token）：JWT + 数据库存储，7 天有效期
- 支持 Rotating 机制：每次刷新令牌后，旧令牌失效

### 密码安全
- bcrypt 哈希算法
- 最小长度 8 位

### RBAC 权限控制
- 基于角色的访问控制
- 支持多角色分配
- 权限细粒度控制

## Celery 异步任务

### 工作流执行
- `execute_workflow` - 异步执行工作流
- 支持失败重试（最多 3 次）
- 详细的执行日志记录

### 定时任务
- `cleanup_expired_tokens` - 每天 3:00 清理过期令牌
- `cleanup_old_logs` - 每天 4:00 清理 30 天前的日志

## 测试

```bash
# 运行测试
pytest mvp/backend/tests/ -v

# 带覆盖率
pytest --cov=mvp.backend mvp/backend/tests/
```

## 部署

### Docker 部署

```bash
# 构建镜像
docker build -f Dockerfile.prod -t project-phoenix-mvp:latest .

# 运行容器
docker run -d \
  -p 8000:8000 \
  -e DATABASE_URL=postgresql://... \
  -e JWT_SECRET_KEY=your-secret-key \
  project-phoenix-mvp:latest
```

### Docker Compose 部署

```bash
docker-compose -f docker-compose.prod.yml up -d
```

## 监控

- Prometheus 指标：http://localhost:9090
- Grafana 可视化：http://localhost:3000 (admin/admin123)
- Loki 日志：http://localhost:3100

## 开发注意事项

1. **数据库迁移**：使用 Alembic 管理数据库迁移
2. **API 版本控制**：所有 API 路径包含 `/api/v1` 前缀
3. **错误处理**：统一使用 FastAPI 异常处理器
4. **日志记录**：所有关键操作记录日志
5. **性能优化**：数据库查询使用索引，热点数据使用 Redis 缓存
