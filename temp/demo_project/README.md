# User & Task Management API

FastAPI 后端服务，提供用户管理和任务管理功能，支持 JWT 认证。

## 功能特性

- ✅ 用户注册与登录
- ✅ JWT Token 认证
- ✅ 用户 CRUD 操作
- ✅ 任务 CRUD 操作
- ✅ SQLite 数据库 + SQLAlchemy ORM
- ✅ 密码 bcrypt 加密
- ✅ 分页与筛选支持

## 项目结构

```
demo_project/
├── main.py              # FastAPI 应用入口
├── models.py            # Pydantic + SQLAlchemy 数据模型
├── database.py          # 数据库连接配置
├── auth.py              # JWT 认证逻辑
├── requirements.txt     # Python 依赖
└── routes/
    ├── __init__.py
    ├── users.py         # 用户路由
    └── tasks.py         # 任务路由
```

## 安装步骤

### 1. 克隆/进入项目目录

```bash
cd temp/demo_project
```

### 2. 创建虚拟环境（推荐）

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# Linux/Mac
source venv/bin/activate
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

### 4. 配置环境变量（可选）

```bash
# Windows
set SECRET_KEY=your-secret-key-here

# Linux/Mac
export SECRET_KEY=your-secret-key-here
```

默认 SECRET_KEY 为 `your-secret-key-here-change-in-production`，生产环境务必修改。

### 5. 启动服务

```bash
python main.py
```

服务启动后访问：
- API 地址: http://localhost:8000
- Swagger 文档: http://localhost:8000/docs
- ReDoc 文档: http://localhost:8000/redoc

## API 接口列表

### 系统接口

| 方法 | 路径 | 描述 | 认证 |
|------|------|------|------|
| GET | `/` | 欢迎信息 | ❌ |
| GET | `/health` | 健康检查 | ❌ |

### 用户管理 `/users`

| 方法 | 路径 | 描述 | 认证 |
|------|------|------|------|
| POST | `/users/` | 创建新用户 | ❌ |
| POST | `/users/login` | 用户登录获取 Token | ❌ |
| GET | `/users/me` | 获取当前登录用户信息 | ✅ |
| GET | `/users/` | 获取用户列表 | ✅ |
| GET | `/users/{user_id}` | 获取指定用户 | ✅ |
| PUT | `/users/{user_id}` | 更新用户信息 | ✅ |
| DELETE | `/users/{user_id}` | 删除用户 | ✅ |

### 任务管理 `/tasks`

| 方法 | 路径 | 描述 | 认证 |
|------|------|------|------|
| POST | `/tasks/` | 创建新任务 | ✅ |
| GET | `/tasks/` | 获取任务列表 | ✅ |
| GET | `/tasks/{task_id}` | 获取指定任务 | ✅ |
| PUT | `/tasks/{task_id}` | 更新任务信息 | ✅ |
| DELETE | `/tasks/{task_id}` | 删除任务 | ✅ |

## 使用示例

### 1. 创建用户

```bash
curl -X POST "http://localhost:8000/users/" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "john",
    "email": "john@example.com",
    "password": "secret123",
    "full_name": "John Doe"
  }'
```

**响应：**
```json
{
  "id": 1,
  "username": "john",
  "email": "john@example.com",
  "full_name": "John Doe",
  "is_active": true,
  "created_at": "2024-01-01T00:00:00",
  "updated_at": "2024-01-01T00:00:00"
}
```

### 2. 用户登录获取 Token

```bash
curl -X POST "http://localhost:8000/users/login?username=john&password=secret123"
```

**响应：**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "user_id": 1,
  "username": "john"
}
```

### 3. 使用 Token 认证请求

```bash
# 获取当前用户信息
curl -X GET "http://localhost:8000/users/me" \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."

# 获取用户列表
curl -X GET "http://localhost:8000/users/?skip=0&limit=10" \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
```

### 4. 创建任务

```bash
curl -X POST "http://localhost:8000/tasks/" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..." \
  -d '{
    "title": "完成项目文档",
    "description": "编写 README 和 API 文档",
    "status": "pending",
    "user_id": 1
  }'
```

### 5. 获取任务列表（带筛选）

```bash
# 获取所有任务
curl -X GET "http://localhost:8000/tasks/" \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."

# 按状态筛选
curl -X GET "http://localhost:8000/tasks/?status_filter=pending" \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."

# 按用户筛选
curl -X GET "http://localhost:8000/tasks/?user_id=1" \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
```

### 6. 更新任务状态

```bash
curl -X PUT "http://localhost:8000/tasks/1" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..." \
  -d '{
    "status": "completed"
  }'
```

## 数据模型

### 用户 (User)

| 字段 | 类型 | 描述 |
|------|------|------|
| id | int | 用户 ID |
| username | string | 用户名（唯一） |
| email | string | 邮箱（唯一） |
| full_name | string | 全名 |
| is_active | bool | 是否活跃 |
| created_at | datetime | 创建时间 |
| updated_at | datetime | 更新时间 |

### 任务 (Task)

| 字段 | 类型 | 描述 |
|------|------|------|
| id | int | 任务 ID |
| title | string | 任务标题 |
| description | string | 任务描述 |
| status | string | 状态：pending/in_progress/completed/cancelled |
| due_date | datetime | 截止日期 |
| user_id | int | 所属用户 ID |
| created_at | datetime | 创建时间 |
| updated_at | datetime | 更新时间 |

## 技术栈

- **框架**: FastAPI 0.104+
- **ORM**: SQLAlchemy 2.0+
- **数据库**: SQLite
- **认证**: JWT (python-jose)
- **密码加密**: bcrypt (passlib)
- **数据验证**: Pydantic 2.0+
- **服务器**: Uvicorn

## 开发说明

- 数据库文件自动创建为 `demo.db`
- JWT Token 有效期默认 30 分钟
- 密码使用 bcrypt 加密存储
- 生产环境请务必修改 `SECRET_KEY`

## License

MIT
