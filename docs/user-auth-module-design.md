# 用户登录模块技术方案设计

> 版本：1.0.0  
> 作者：OpenAkita Architecture Team  
> 日期：2026-03-07  
> 技术栈：Python (FastAPI/Flask) + PostgreSQL + Redis

---

## 一、认证方式选择

### 1.1 方案对比

| 方案 | 优点 | 缺点 | 适用场景 |
|------|------|------|----------|
| **Session** | 服务端控制、易撤销、成熟稳定 | 服务端存储压力、跨域复杂 | 传统单体应用 |
| **JWT** | 无状态、性能好、跨域友好 | 撤销困难、Token 泄露风险高 | 微服务、API 优先 |
| **OAuth 2.0** | 标准化、支持第三方登录 | 实现复杂、需要额外服务 | 需要第三方授权 |
| **JWT + Refresh Token** | 平衡安全与性能、支持撤销 | 实现稍复杂 | **推荐：现代 Web 应用** |

### 1.2 推荐方案：JWT + Rotating Refresh Token

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│   Client    │────▶│  API Server  │────▶│   Redis     │
│  (Browser)  │     │  (FastAPI)   │     │  (Session)  │
└─────────────┘     └──────────────┘     └─────────────┘
       │                    │                    │
       │  1. httpOnly Cookie│                    │
       │◀───────────────────│                    │
       │  (Refresh Token)   │                    │
       │                    │                    │
       │  2. Authorization  │                    │
       │  Header (JWT)      │                    │
       │───────────────────▶│                    │
       │                    │  3. Validate +     │
       │                    │  Blacklist Check   │
       │                    │───────────────────▶│
```

**核心设计原则：**

1. **Access Token (JWT)**
   - 有效期：15 分钟
   - 存储：客户端内存（JavaScript 变量）
   - 用途：API 请求认证
   - 签名算法：HS256 或 RS256

2. **Refresh Token**
   - 有效期：7-30 天
   - 存储：**httpOnly + Secure + SameSite=Strict Cookie**
   - 用途：刷新 Access Token
   - 机制：**Rotating（每次刷新后轮换）**

3. **安全增强**
   - Refresh Token 绑定设备指纹（User-Agent + IP 哈希）
   - Token 黑名单存储于 Redis（支持即时撤销）
   - 登录失败次数限制（防暴力破解）
   - CSRF Token 双重验证

### 1.3 安全考虑

#### 1.3.1 防 XSS 攻击
- ✅ Refresh Token 存储在 httpOnly Cookie，JavaScript 无法访问
- ✅ Access Token 存储在内存，不持久化
- ✅ 所有用户输入严格校验和转义
- ✅ 启用 CSP (Content Security Policy)

#### 1.3.2 防 CSRF 攻击
- ✅ Cookie 设置 `SameSite=Strict`
- ✅ 敏感操作需要额外的 CSRF Token
- ✅ 验证 `Origin` 和 `Referer` 头

#### 1.3.3 Rotating Refresh Token 机制
```
用户请求刷新 Token
    ↓
服务端验证旧 Refresh Token
    ↓
生成新 Refresh Token + 新 Access Token
    ↓
将旧 Refresh Token 加入黑名单（宽限期 30 秒）
    ↓
返回新 Token 对
    ↓
如果检测到旧 Token 被重用 → 立即撤销所有 Token（可能被盗用）
```

#### 1.3.4 密码安全
- ✅ 使用 **Argon2id** 哈希算法（优于 bcrypt）
- ✅ 盐值：每用户随机 16 字节
- ✅ 密码复杂度要求：最小 8 位，包含大小写 + 数字 + 特殊字符
- ✅ 密码重置链接：一次性、15 分钟过期

---

## 二、数据库表结构设计

### 2.1 用户表 (users)

```sql
CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email           VARCHAR(255) NOT NULL UNIQUE,
    username        VARCHAR(50) NOT NULL UNIQUE,
    password_hash   VARCHAR(255) NOT NULL,
    
    -- 用户状态
    is_active       BOOLEAN NOT NULL DEFAULT true,
    is_verified     BOOLEAN NOT NULL DEFAULT false,  -- 邮箱验证
    is_locked       BOOLEAN NOT NULL DEFAULT false,  -- 账户锁定
    
    -- 安全字段
    failed_login_attempts  INTEGER NOT NULL DEFAULT 0,
    locked_until    TIMESTAMP WITH TIME ZONE,
    last_login_at   TIMESTAMP WITH TIME ZONE,
    last_login_ip   INET,
    
    -- 元数据
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at      TIMESTAMP WITH TIME ZONE,  -- 软删除
    
    -- 约束
    CONSTRAINT chk_email_format CHECK (email ~* '^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$'),
    CONSTRAINT chk_username_format CHECK (username ~* '^[a-zA-Z0-9_-]{3,50}$')
);

-- 索引设计
CREATE INDEX idx_users_email ON users(email) WHERE deleted_at IS NULL;
CREATE INDEX idx_users_username ON users(username) WHERE deleted_at IS NULL;
CREATE INDEX idx_users_created_at ON users(created_at);
CREATE INDEX idx_users_active ON users(is_active) WHERE deleted_at IS NULL;
```

**字段说明：**

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键，使用 UUID 避免枚举攻击 |
| email | VARCHAR(255) | 唯一邮箱，用于登录和通知 |
| username | VARCHAR(50) | 唯一用户名，用于展示 |
| password_hash | VARCHAR(255) | Argon2id 哈希值 |
| is_active | BOOLEAN | 账户是否激活 |
| is_verified | BOOLEAN | 邮箱是否验证 |
| is_locked | BOOLEAN | 是否因多次失败被锁定 |
| failed_login_attempts | INTEGER | 连续登录失败次数 |
| locked_until | TIMESTAMP | 锁定解除时间 |
| last_login_at | TIMESTAMP | 最后登录时间 |
| last_login_ip | INET | 最后登录 IP（PostgreSQL 专用类型） |
| deleted_at | TIMESTAMP | 软删除时间（NULL=未删除） |

### 2.2 Refresh Token 表 (refresh_tokens)

```sql
CREATE TABLE refresh_tokens (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    
    -- Token 信息
    token_hash      VARCHAR(255) NOT NULL,  -- 存储哈希值，非明文
    token_family    UUID NOT NULL,          -- Token 家族（用于检测重用）
    
    -- 设备信息
    device_fingerprint  VARCHAR(255) NOT NULL,  -- User-Agent + IP 哈希
    ip_address      INET NOT NULL,
    user_agent      VARCHAR(500),
    
    -- 有效期
    issued_at       TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at      TIMESTAMP WITH TIME ZONE NOT NULL,
    revoked_at      TIMESTAMP WITH TIME ZONE,  -- 手动撤销时间
    
    -- 轮换追踪
    is_rotated      BOOLEAN NOT NULL DEFAULT false,
    rotated_by_id   UUID REFERENCES refresh_tokens(id),  -- 被哪个 Token 轮换
    
    -- 元数据
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    -- 约束
    CONSTRAINT chk_expires CHECK (expires_at > issued_at)
);

-- 索引设计
CREATE INDEX idx_refresh_tokens_user_id ON refresh_tokens(user_id) WHERE revoked_at IS NULL;
CREATE INDEX idx_refresh_tokens_hash ON refresh_tokens(token_hash) UNIQUE;
CREATE INDEX idx_refresh_tokens_family ON refresh_tokens(token_family);
CREATE INDEX idx_refresh_tokens_expires ON refresh_tokens(expires_at);
CREATE INDEX idx_refresh_tokens_active ON refresh_tokens(user_id, expires_at) 
    WHERE revoked_at IS NULL AND expires_at > CURRENT_TIMESTAMP;
```

**字段说明：**

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| user_id | UUID | 外键关联用户 |
| token_hash | VARCHAR(255) | Token 的 SHA256 哈希（不存储明文） |
| token_family | UUID | 同一家族 Token 共享 ID，检测重用攻击 |
| device_fingerprint | VARCHAR(255) | 设备指纹（User-Agent + IP 的哈希） |
| ip_address | INET | 颁发 Token 时的 IP |
| user_agent | VARCHAR(500) | 用户代理字符串 |
| issued_at | TIMESTAMP | 颁发时间 |
| expires_at | TIMESTAMP | 过期时间 |
| revoked_at | TIMESTAMP | 撤销时间（NULL=有效） |
| is_rotated | BOOLEAN | 是否已被轮换 |
| rotated_by_id | UUID | 轮换它的新 Token ID |

### 2.3 Token 黑名单表 (token_blacklist) - 可选

> 如果使用 Redis 存储黑名单，此表可省略。这里提供持久化方案。

```sql
CREATE TABLE token_blacklist (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token_hash      VARCHAR(255) NOT NULL,
    token_type      VARCHAR(20) NOT NULL,  -- 'access' | 'refresh'
    blacklisted_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at      TIMESTAMP WITH TIME ZONE NOT NULL,
    reason          VARCHAR(100),  -- 'revoked' | 'rotated' | 'suspicious'
    
    CONSTRAINT chk_token_type CHECK (token_type IN ('access', 'refresh'))
);

CREATE INDEX idx_blacklist_hash ON token_blacklist(token_hash);
CREATE INDEX idx_blacklist_expires ON token_blacklist(expires_at);

-- 定期清理过期记录（建议每天执行）
-- DELETE FROM token_blacklist WHERE expires_at < CURRENT_TIMESTAMP;
```

### 2.4 登录日志表 (login_logs) - 审计用

```sql
CREATE TABLE login_logs (
    id              BIGSERIAL PRIMARY KEY,
    user_id         UUID REFERENCES users(id),  -- 可为空（失败登录）
    email           VARCHAR(255),  -- 尝试登录的邮箱
    
    -- 结果
    success         BOOLEAN NOT NULL,
    failure_reason  VARCHAR(50),  -- 'invalid_password' | 'user_not_found' | 'locked'
    
    -- 设备信息
    ip_address      INET NOT NULL,
    user_agent      VARCHAR(500),
    device_fingerprint VARCHAR(255),
    
    -- 时间
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_login_logs_user_id ON login_logs(user_id);
CREATE INDEX idx_login_logs_email ON login_logs(email);
CREATE INDEX idx_login_logs_created_at ON login_logs(created_at);
CREATE INDEX idx_login_logs_ip ON login_logs(ip_address);
```

### 2.5 数据库关系图

```mermaid
erDiagram
    users ||--o{ refresh_tokens : "owns"
    users ||--o{ login_logs : "generates"
    refresh_tokens ||--o| refresh_tokens : "rotated_by"
    
    users {
        UUID id PK
        string email UK
        string username UK
        string password_hash
        boolean is_active
        timestamp last_login_at
    }
    
    refresh_tokens {
        UUID id PK
        UUID user_id FK
        string token_hash UK
        UUID token_family
        timestamp expires_at
        timestamp revoked_at
    }
    
    login_logs {
        bigint id PK
        UUID user_id FK
        string email
        boolean success
        timestamp created_at
    }
```

---

## 三、API 接口设计

### 3.1 通用规范

**基础信息：**
- Base URL: `https://api.example.com`
- 认证方式：Bearer Token (Access Token)
- 请求格式：`application/json`
- 响应格式：`application/json`

**统一响应格式：**
```json
{
  "success": true,
  "data": { ... },
  "error": null
}
```

**错误响应格式：**
```json
{
  "success": false,
  "data": null,
  "error": {
    "code": "INVALID_CREDENTIALS",
    "message": "邮箱或密码错误",
    "details": {}
  }
}
```

### 3.2 错误码定义

| 错误码 | HTTP 状态码 | 说明 |
|--------|------------|------|
| `INVALID_CREDENTIALS` | 401 | 邮箱或密码错误 |
| `USER_NOT_FOUND` | 404 | 用户不存在 |
| `USER_LOCKED` | 403 | 账户被锁定 |
| `USER_INACTIVE` | 403 | 账户未激活 |
| `TOKEN_EXPIRED` | 401 | Token 已过期 |
| `TOKEN_INVALID` | 401 | Token 无效 |
| `TOKEN_REVOKED` | 401 | Token 已被撤销 |
| `REFRESH_TOKEN_REUSED` | 401 | Refresh Token 被重用（可能被盗） |
| `EMAIL_ALREADY_EXISTS` | 409 | 邮箱已被注册 |
| `USERNAME_ALREADY_EXISTS` | 409 | 用户名已被注册 |
| `WEAK_PASSWORD` | 400 | 密码强度不足 |
| `RATE_LIMIT_EXCEEDED` | 429 | 请求频率超限 |
| `INTERNAL_ERROR` | 500 | 服务器内部错误 |

---

### 3.3 注册接口

#### `POST /api/auth/register`

**请求参数：**
```json
{
  "email": "user@example.com",
  "username": "john_doe",
  "password": "SecurePass123!",
  "confirm_password": "SecurePass123!"
}
```

**参数验证规则：**
| 字段 | 类型 | 规则 |
|------|------|------|
| email | string | 有效邮箱格式，最大 255 字符 |
| username | string | 3-50 字符，字母数字下划线 |
| password | string | 最小 8 位，包含大小写 + 数字 + 特殊字符 |
| confirm_password | string | 必须与 password 一致 |

**成功响应 (201 Created)：**
```json
{
  "success": true,
  "data": {
    "user_id": "550e8400-e29b-41d4-a716-446655440000",
    "email": "user@example.com",
    "username": "john_doe",
    "is_verified": false,
    "message": "注册成功，请查收验证邮件"
  },
  "error": null
}
```

**错误响应示例：**
```json
{
  "success": false,
  "data": null,
  "error": {
    "code": "EMAIL_ALREADY_EXISTS",
    "message": "该邮箱已被注册",
    "details": {}
  }
}
```

**实现要点：**
1. 密码强度校验（使用 `zxcvbn` 库）
2. 邮箱唯一性检查（忽略大小写）
3. 用户名唯一性检查
4. 发送验证邮件（异步）
5. 记录注册日志（审计）

---

### 3.4 登录接口

#### `POST /api/auth/login`

**请求参数：**
```json
{
  "email": "user@example.com",
  "password": "SecurePass123!",
  "remember_me": false
}
```

**参数说明：**
| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| email | string | ✅ | 用户邮箱 |
| password | string | ✅ | 用户密码 |
| remember_me | boolean | ❌ | 是否记住我（影响 Refresh Token 有效期） |

**成功响应 (200 OK)：**
```json
{
  "success": true,
  "data": {
    "user": {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "email": "user@example.com",
      "username": "john_doe",
      "is_verified": true
    },
    "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    "token_type": "Bearer",
    "expires_in": 900
  },
  "error": null
}
```

**Set-Cookie Header（同时返回）：**
```
Set-Cookie: refresh_token=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...; 
  HttpOnly; 
  Secure; 
  SameSite=Strict; 
  Path=/; 
  Max-Age=604800
```

**错误响应示例：**
```json
{
  "success": false,
  "data": null,
  "error": {
    "code": "INVALID_CREDENTIALS",
    "message": "邮箱或密码错误",
    "details": {
      "remaining_attempts": 3
    }
  }
}
```

**实现要点：**
1. 密码验证使用恒定时间比较（防时序攻击）
2. 失败次数累加，超过 5 次锁定账户 30 分钟
3. 生成 Access Token（15 分钟）和 Refresh Token（7-30 天）
4. Refresh Token 存入 httpOnly Cookie
5. 记录登录日志（成功/失败）
6. 更新用户最后登录时间和 IP

**Python 示例代码（FastAPI）：**
```python
@app.post("/api/auth/login")
async def login(
    request: Request,
    payload: LoginRequest,
    db: AsyncSession = Depends(get_db)
):
    # 查找用户
    user = await get_user_by_email(db, payload.email)
    if not user:
        # 故意延迟，防枚举攻击
        await asyncio.sleep(0.1)
        raise HTTPException(401, "INVALID_CREDENTIALS")
    
    # 验证密码
    is_valid = verify_password(payload.password, user.password_hash)
    if not is_valid:
        await handle_failed_login(db, user)
        raise HTTPException(401, "INVALID_CREDENTIALS")
    
    # 检查账户状态
    if user.is_locked and user.locked_until > datetime.now():
        raise HTTPException(403, "USER_LOCKED")
    
    # 生成 Token
    access_token = create_access_token(user.id)
    refresh_token = create_refresh_token(user.id, request)
    
    # 设置 Cookie
    response = JSONResponse({...})
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=604800  # 7 天
    )
    
    return response
```

---

### 3.5 刷新 Token 接口

#### `POST /api/auth/refresh`

**认证方式：** 从 Cookie 自动读取 Refresh Token

**请求参数：** 无（从 Cookie 读取）

**成功响应 (200 OK)：**
```json
{
  "success": true,
  "data": {
    "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    "token_type": "Bearer",
    "expires_in": 900
  },
  "error": null
}
```

**Set-Cookie Header（同时返回新 Refresh Token）：**
```
Set-Cookie: refresh_token=NEW_TOKEN_HASH...; 
  HttpOnly; 
  Secure; 
  SameSite=Strict; 
  Path=/; 
  Max-Age=604800
```

**错误响应示例：**
```json
{
  "success": false,
  "data": null,
  "error": {
    "code": "TOKEN_EXPIRED",
    "message": "Refresh Token 已过期，请重新登录",
    "details": {}
  }
}
```

**实现要点（Rotating 机制）：**
```python
@app.post("/api/auth/refresh")
async def refresh_token(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    # 从 Cookie 获取 Refresh Token
    old_refresh_token = request.cookies.get("refresh_token")
    if not old_refresh_token:
        raise HTTPException(401, "TOKEN_INVALID")
    
    # 验证 Token
    token_record = await validate_refresh_token(db, old_refresh_token)
    
    # 检测重用攻击
    if token_record.is_rotated:
        # 同一 Token 被重用 → 可能被盗 → 撤销所有 Token
        await revoke_all_user_tokens(db, token_record.user_id)
        raise HTTPException(401, "REFRESH_TOKEN_REUSED")
    
    # 生成新 Token 对
    new_access_token = create_access_token(token_record.user_id)
    new_refresh_token = create_refresh_token(
        token_record.user_id, 
        request,
        token_family=token_record.token_family,
        rotated_by=token_record.id
    )
    
    # 标记旧 Token 为已轮换
    token_record.is_rotated = True
    token_record.rotated_by_id = new_refresh_token.id
    
    # 设置新 Cookie
    response = JSONResponse({...})
    response.set_cookie(
        key="refresh_token",
        value=new_refresh_token,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=604800
    )
    
    return response
```

---

### 3.6 登出接口

#### `POST /api/auth/logout`

**认证方式：** Bearer Token（Access Token）或 Refresh Token

**请求参数：**
```json
{
  "revoke_all_devices": false
}
```

**参数说明：**
| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| revoke_all_devices | boolean | ❌ | 是否撤销所有设备的 Token（默认只撤销当前设备） |

**成功响应 (200 OK)：**
```json
{
  "success": true,
  "data": {
    "message": "已成功登出"
  },
  "error": null
}
```

**实现要点：**
```python
@app.post("/api/auth/logout")
async def logout(
    request: Request,
    payload: LogoutRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    # 获取当前 Refresh Token
    refresh_token = request.cookies.get("refresh_token")
    
    if payload.revoke_all_devices:
        # 撤销用户所有 Token
        await revoke_all_user_tokens(db, current_user.id)
    else:
        # 只撤销当前 Token
        if refresh_token:
            await revoke_refresh_token(db, refresh_token)
    
    # 将 Access Token 加入黑名单
    await blacklist_access_token(request.headers.get("Authorization"))
    
    # 清除 Cookie
    response = JSONResponse({...})
    response.delete_cookie(
        key="refresh_token",
        path="/",
        secure=True,
        samesite="strict"
    )
    
    return response
```

---

### 3.7 补充接口

#### `GET /api/auth/me` - 获取当前用户信息

**认证方式：** Bearer Token

**响应示例：**
```json
{
  "success": true,
  "data": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "email": "user@example.com",
    "username": "john_doe",
    "is_verified": true,
    "created_at": "2026-01-01T00:00:00Z",
    "last_login_at": "2026-03-07T10:30:00Z"
  },
  "error": null
}
```

#### `POST /api/auth/forgot-password` - 忘记密码

**请求参数：**
```json
{
  "email": "user@example.com"
}
```

**响应：**
```json
{
  "success": true,
  "data": {
    "message": "如果该邮箱已注册，您将收到重置邮件"
  },
  "error": null
}
```

**注意：** 不透露邮箱是否存在（防枚举攻击）

#### `POST /api/auth/reset-password` - 重置密码

**请求参数：**
```json
{
  "token": "reset_token_from_email",
  "new_password": "NewSecurePass123!"
}
```

---

## 四、技术实现建议

### 4.1 Python 依赖库推荐

```txt
# Web 框架
fastapi==0.109.0
uvicorn[standard]==0.27.0

# 认证
python-jose[cryptography]==3.3.0  # JWT
passlib[argon2]==1.7.4            # 密码哈希
pydantic==2.5.0                   # 数据验证

# 数据库
sqlalchemy[asyncio]==2.0.25
asyncpg==0.29.0                   # PostgreSQL 驱动
redis==5.0.1                      # Redis 客户端

# 安全
python-multipart==0.0.6           # 表单解析
itsdangerous==2.1.2               # 签名
zxcvbn==4.4.28                    # 密码强度检测

# 工具
python-dotenv==1.0.0              # 环境变量
structlog==24.1.0                 # 结构化日志
```

### 4.2 环境变量配置

```bash
# .env
# JWT 配置
JWT_SECRET_KEY=your-super-secret-key-min-32-chars
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=15
REFRESH_TOKEN_EXPIRE_DAYS=7

# 数据库
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/dbname
REDIS_URL=redis://localhost:6379/0

# 安全
SECRET_KEY=for-cookie-signing
CORS_ORIGINS=https://your-frontend.com
```

### 4.3 项目结构建议

```
src/
├── api/
│   ├── routes/
│   │   ├── auth.py          # 认证相关路由
│   │   └── users.py
│   ├── deps.py              # 依赖注入
│   └── middleware/
│       ├── auth.py          # JWT 验证中间件
│       └── rate_limit.py    # 限流中间件
├── core/
│   ├── config.py            # 配置管理
│   ├── security.py          # 密码/TOKEN 工具
│   └── exceptions.py        # 自定义异常
├── models/
│   ├── user.py
│   └── token.py
├── schemas/
│   ├── auth.py              # 请求/响应模型
│   └── user.py
├── services/
│   ├── auth_service.py      # 认证业务逻辑
│   └── email_service.py     # 邮件服务
└── utils/
    └── token.py             # Token 生成/验证
```

### 4.4 安全最佳实践清单

- [ ] 所有密码使用 Argon2id 哈希
- [ ] JWT 密钥长度至少 32 字符，定期轮换
- [ ] 启用 HTTPS（生产环境强制）
- [ ] 设置合理的 CORS 策略
- [ ] 实现速率限制（登录接口：5 次/分钟）
- [ ] 记录所有认证相关日志
- [ ] 定期清理过期 Token
- [ ] 监控异常登录行为
- [ ] 实施账户锁定策略
- [ ] 敏感操作需要二次验证

---

## 五、总结

本方案采用 **JWT + Rotating Refresh Token + httpOnly Cookie** 的组合，在以下方面达到平衡：

| 维度 | 方案选择 |
|------|----------|
| **安全性** | httpOnly Cookie 防 XSS，Rotating 机制防盗用，黑名单支持即时撤销 |
| **性能** | Access Token 无状态验证，Redis 缓存黑名单 |
| **用户体验** | 自动刷新 Token，用户无感知 |
| **可维护性** | 标准化实现，清晰的表结构，完善的日志审计 |
| **扩展性** | 支持多设备、第三方登录、2FA |

**实施建议：**
1. 优先实现核心流程（注册/登录/刷新/登出）
2. 逐步添加安全增强（限流、审计、监控）
3. 编写完整的单元测试和集成测试
4. 进行安全渗透测试

---

## 附录

### A. JWT Payload 示例

```json
{
  "sub": "550e8400-e29b-41d4-a716-446655440000",
  "email": "user@example.com",
  "type": "access",
  "iat": 1709812800,
  "exp": 1709813700,
  "jti": "unique-token-id"
}
```

### B. 参考文档

- [OWASP Authentication Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html)
- [JWT Best Practices](https://datatracker.ietf.org/doc/html/draft-ietf-oauth-jwt-bcp)
- [Argon2 RFC 9106](https://www.rfc-editor.org/rfc/rfc9106.html)
