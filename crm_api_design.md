# CRM系统API接口设计

## 一、API设计原则

### RESTful设计规范
- **资源命名**：使用名词复数形式（/customers, /opportunities）
- **HTTP方法**：GET（查询）、POST（创建）、PUT（全量更新）、PATCH（部分更新）、DELETE（删除）
- **状态码**：200（成功）、201（创建）、400（请求错误）、401（未认证）、403（无权限）、404（不存在）、500（服务器错误）
- **版本控制**：URL路径版本（/api/v1/）
- **响应格式**：统一JSON格式，包含code、message、data字段

### 认证方案
- **JWT Token**：无状态认证，适合分布式系统
- **Token刷新机制**：Access Token（短期）+ Refresh Token（长期）
- **权限验证**：基于RBAC的接口级权限控制

## 二、认证与授权API

### 用户认证
```
POST   /api/v1/auth/login          # 用户登录
POST   /api/v1/auth/logout         # 用户登出
POST   /api/v1/auth/refresh        # 刷新Token
GET    /api/v1/auth/current        # 获取当前用户信息
PUT    /api/v1/auth/password       # 修改密码
```

### 请求示例
```json
// POST /api/v1/auth/login
{
  "username": "admin",
  "password": "123456"
}

// 响应
{
  "code": 200,
  "message": "登录成功",
  "data": {
    "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    "expires_in": 7200,
    "user_info": {
      "id": 1,
      "username": "admin",
      "real_name": "管理员",
      "roles": ["admin"]
    }
  }
}
```

## 三、用户管理API

### 用户CRUD
```
GET    /api/v1/users               # 获取用户列表（分页）
POST   /api/v1/users               # 创建用户
GET    /api/v1/users/:id           # 获取用户详情
PUT    /api/v1/users/:id           # 更新用户信息
DELETE /api/v1/users/:id           # 删除用户
PATCH  /api/v1/users/:id/status    # 启用/禁用用户
```

### 角色管理
```
GET    /api/v1/roles               # 获取角色列表
POST   /api/v1/roles               # 创建角色
GET    /api/v1/roles/:id           # 获取角色详情
PUT    /api/v1/roles/:id           # 更新角色
DELETE /api/v1/roles/:id           # 删除角色
GET    /api/v1/roles/:id/permissions # 获取角色权限
PUT    /api/v1/roles/:id/permissions # 分配角色权限
```

### 部门管理
```
GET    /api/v1/departments         # 获取部门树
POST   /api/v1/departments         # 创建部门
GET    /api/v1/departments/:id     # 获取部门详情
PUT    /api/v1/departments/:id     # 更新部门
DELETE /api/v1/departments/:id     # 删除部门
GET    /api/v1/departments/:id/users # 获取部门用户
```

## 四、客户管理API

### 客户CRUD
```
GET    /api/v1/customers           # 获取客户列表（分页、筛选）
POST   /api/v1/customers           # 创建客户
GET    /api/v1/customers/:id       # 获取客户详情
PUT    /api/v1/customers/:id       # 更新客户信息
DELETE /api/v1/customers/:id       # 删除客户
PATCH  /api/v1/customers/:id/owner # 转移客户负责人
```

### 查询参数
```
GET /api/v1/customers?page=1&size=20&keyword=公司名&industry=互联网&level=A&owner_id=1
```

### 联系人管理
```
GET    /api/v1/customers/:id/contacts      # 获取客户联系人列表
POST   /api/v1/customers/:id/contacts      # 添加联系人
GET    /api/v1/contacts/:id                # 获取联系人详情
PUT    /api/v1/contacts/:id                # 更新联系人
DELETE /api/v1/contacts/:id                # 删除联系人
PATCH  /api/v1/contacts/:id/primary        # 设置主要联系人
```

### 客户标签
```
GET    /api/v1/customers/:id/tags          # 获取客户标签
POST   /api/v1/customers/:id/tags          # 添加标签
DELETE /api/v1/customers/:id/tags/:tagId   # 移除标签
GET    /api/v1/tags                        # 获取所有标签
POST   /api/v1/tags                        # 创建标签
```

## 五、商机管理API

### 商机CRUD
```
GET    /api/v1/opportunities               # 获取商机列表（分页、筛选）
POST   /api/v1/opportunities               # 创建商机
GET    /api/v1/opportunities/:id           # 获取商机详情
PUT    /api/v1/opportunities/:id           # 更新商机
DELETE /api/v1/opportunities/:id           # 删除商机
PATCH  /api/v1/opportunities/:id/stage     # 变更商机阶段
```

### 商机看板
```
GET    /api/v1/opportunities/board         # 获取商机看板数据
# 响应按阶段分组的商机统计
```

### 商机产品
```
GET    /api/v1/opportunities/:id/products  # 获取商机产品
POST   /api/v1/opportunities/:id/products  # 添加产品
PUT    /api/v1/opportunities/:id/products/:productId  # 更新产品
DELETE /api/v1/opportunities/:id/products/:productId  # 移除产品
```

### 商机预测
```
GET    /api/v1/opportunities/forecast      # 获取商机预测
# 参数：start_date, end_date, owner_id
# 返回：各阶段商机数量、金额、加权金额
```

## 六、跟进记录API

### 跟进记录
```
GET    /api/v1/follow-ups                  # 获取跟进记录列表
POST   /api/v1/follow-ups                  # 创建跟进记录
GET    /api/v1/follow-ups/:id              # 获取跟进详情
PUT    /api/v1/follow-ups/:id              # 更新跟进记录
DELETE /api/v1/follow-ups/:id              # 删除跟进记录
```

### 客户跟进时间线
```
GET    /api/v1/customers/:id/follow-ups    # 获取客户跟进时间线
GET    /api/v1/opportunities/:id/follow-ups # 获取商机跟进记录
```

### 待跟进提醒
```
GET    /api/v1/follow-ups/reminders        # 获取待跟进提醒列表
# 参数：owner_id, date_range
```

## 七、合同管理API

### 合同CRUD
```
GET    /api/v1/contracts                   # 获取合同列表
POST   /api/v1/contracts                   # 创建合同
GET    /api/v1/contracts/:id               # 获取合同详情
PUT    /api/v1/contracts/:id               # 更新合同
DELETE /api/v1/contracts/:id               # 删除合同
PATCH  /api/v1/contracts/:id/status        # 变更合同状态
```

### 合同审批
```
POST   /api/v1/contracts/:id/submit        # 提交审批
GET    /api/v1/contracts/:id/approvals     # 获取审批记录
POST   /api/v1/contracts/:id/approve       # 审批通过
POST   /api/v1/contracts/:id/reject        # 审批拒绝
```

### 合同统计
```
GET    /api/v1/contracts/statistics        # 合同统计
# 参数：start_date, end_date, group_by (month/quarter/year)
```

## 八、产品管理API

### 产品CRUD
```
GET    /api/v1/products                    # 获取产品列表
POST   /api/v1/products                    # 创建产品
GET    /api/v1/products/:id                # 获取产品详情
PUT    /api/v1/products/:id                # 更新产品
DELETE /api/v1/products/:id                # 删除产品
PATCH  /api/v1/products/:id/status         # 上架/下架产品
```

## 九、数据分析API

### 销售漏斗
```
GET    /api/v1/analytics/funnel            # 获取销售漏斗数据
# 参数：start_date, end_date, owner_id, department_id
# 返回：各阶段商机数量、金额
```

### 业绩排行
```
GET    /api/v1/analytics/ranking           # 获取业绩排行
# 参数：type (sales/contracts), period (month/quarter/year), limit
# 返回：个人/团队业绩排行
```

### 趋势分析
```
GET    /api/v1/analytics/trend             # 获取趋势数据
# 参数：metric (customers/opportunities/contracts), period, group_by
# 返回：时间序列数据
```

### 数据导出
```
POST   /api/v1/analytics/export            # 导出数据
# 参数：type, format (excel/csv), filters
# 返回：下载链接
```

## 十、通用响应格式

### 成功响应
```json
{
  "code": 200,
  "message": "操作成功",
  "data": {
    // 具体数据
  }
}
```

### 分页响应
```json
{
  "code": 200,
  "message": "查询成功",
  "data": {
    "list": [],
    "pagination": {
      "page": 1,
      "size": 20,
      "total": 100,
      "total_pages": 5
    }
  }
}
```

### 错误响应
```json
{
  "code": 400,
  "message": "请求参数错误",
  "error": {
    "field": "email",
    "message": "邮箱格式不正确"
  }
}
```

## 十一、请求头规范

### 认证头
```
Authorization: Bearer <access_token>
```

### 公共头
```
Content-Type: application/json
Accept: application/json
X-Request-ID: <uuid>  # 请求追踪ID
X-Client-Version: 1.0.0  # 客户端版本
```

## 十二、错误码定义

| 错误码 | 说明 |
|--------|------|
| 200 | 成功 |
| 201 | 创建成功 |
| 400 | 请求参数错误 |
| 401 | 未认证 |
| 403 | 无权限 |
| 404 | 资源不存在 |
| 409 | 资源冲突 |
| 422 | 请求格式正确但语义错误 |
| 500 | 服务器内部错误 |
| 1001 | 用户名或密码错误 |
| 1002 | Token已过期 |
| 1003 | Token无效 |
| 2001 | 客户不存在 |
| 2002 | 商机不存在 |
| 2003 | 合同不存在 |

---
*设计完成时间: 2026-03-29*
*遵循RESTful API设计最佳实践*