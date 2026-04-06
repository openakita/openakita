# MVP API 集成示例项目

## 项目说明
本项目包含 10 个常用 API 集成方案的示例代码，用于 MVP 开发参考。所有示例均采用 Python 3.9+ 编写，支持快速集成到 FastAPI/Flask 项目中。

## 完成状态
✅ 全部 10 个 API 示例已完成（2026-03-18）

## 目录结构
```
mvp-api-examples/
├── auth/
│   ├── jwt_auth.py       # 用户认证 API（JWT）
│   └── oauth_login.py    # 第三方登录 API（Google/GitHub OAuth2）
├── llm/
│   └── llm_client.py     # 大模型 API（Claude/OpenAI）
├── database/
│   └── postgresql_api.py # 数据库 API（PostgreSQL + SQLAlchemy）
├── email/
│   └── email_api.py      # 邮件服务 API（SendGrid/SMTP）
├── sms/
│   └── sms_api.py        # 短信服务 API（Twilio/阿里云）
├── storage/
│   └── storage_api.py    # 文件存储 API（AWS S3/阿里云 OSS）
├── payment/
│   └── payment_api.py    # 支付接口 API（Stripe/支付宝）
├── push/
│   └── push_api.py       # 消息推送 API（钉钉/企业微信）
├── integration/
│   └── analytics_api.py  # 数据分析 API（Mixpanel/Google Analytics）
└── README.md
```

## API 清单

| 序号 | API 类型 | 支持服务商 | 文件路径 | 核心功能 |
|------|----------|------------|----------|----------|
| 1 | 用户认证 | JWT | auth/jwt_auth.py | Token 生成/验证、装饰器保护 |
| 2 | 第三方登录 | Google/GitHub | auth/oauth_login.py | OAuth2 授权、用户信息获取 |
| 3 | 大模型 | Claude/OpenAI | llm/llm_client.py | 聊天接口、多 provider 支持 |
| 4 | 数据库 | PostgreSQL | database/postgresql_api.py | ORM 模型、CRUD 操作、事务支持 |
| 5 | 邮件服务 | SendGrid/SMTP | email/email_api.py | 邮件发送、验证码、欢迎邮件 |
| 6 | 短信服务 | Twilio/阿里云 | sms/sms_api.py | 短信发送、验证码 |
| 7 | 文件存储 | S3/OSS | storage/storage_api.py | 上传/下载、预签名 URL、文件列表 |
| 8 | 支付接口 | Stripe/支付宝 | payment/payment_api.py | 支付意图、webhook 验证、退款 |
| 9 | 消息推送 | 钉钉/企业微信 | push/push_api.py | Webhook 推送、工作流通知 |
| 10 | 数据分析 | Mixpanel/Google | integration/analytics_api.py | 事件追踪、用户属性、页面浏览 |

## 技术栈
- Python 3.9+
- FastAPI / Flask (后端框架)
- SQLAlchemy (数据库 ORM)
- PyJWT (JWT 认证)
- requests (HTTP 客户端)
- boto3 (AWS S3)
- stripe (Stripe 支付)

## 快速开始
```bash
# 安装依赖
pip install fastapi sqlalchemy pyjwt requests python-dotenv boto3 stripe

# 配置环境变量
cp .env.example .env
# 编辑 .env 文件，填入各服务的 API Key

# 运行示例
python auth/jwt_auth.py
python llm/llm_client.py
```

## 环境配置示例 (.env)
```bash
# JWT
JWT_SECRET_KEY=your-secret-key

# LLM
LLM_API_KEY=your-api-key

# 数据库
DATABASE_URL=postgresql://user:password@localhost:5432/mvp_db

# 邮件
SENDGRID_API_KEY=your-sendgrid-key
SMTP_SERVER=smtp.example.com
SMTP_PORT=587

# 短信
TWILIO_ACCOUNT_SID=your-account-sid
TWILIO_AUTH_TOKEN=your-auth-token

# 存储
AWS_ACCESS_KEY=your-aws-key
AWS_SECRET_KEY=your-aws-secret
AWS_S3_BUCKET=your-bucket

# 支付
STRIPE_SECRET_KEY=sk_test_xxx
STRIPE_WEBHOOK_SECRET=whsec_xxx

# 推送
DINGTALK_WEBHOOK=https://oapi.dingtalk.com/robot/send?access_token=xxx
DINGTALK_SECRET=your-secret

# 第三方登录
GOOGLE_CLIENT_ID=your-client-id
GOOGLE_CLIENT_SECRET=your-client-secret
GITHUB_CLIENT_ID=your-client-id
GITHUB_CLIENT_SECRET=your-client-secret

# 数据分析
MIXPANEL_TOKEN=your-token
GA_MEASUREMENT_ID=G-XXXXXX
GA_API_SECRET=your-secret
```

## 使用建议

### 1. 认证模块
- 生产环境使用 HTTPS
- JWT Secret 使用强随机字符串
- Token 过期时间建议 24 小时
- 支持刷新 Token 机制

### 2. 数据库
- 使用连接池管理数据库连接
- 所有写操作使用事务
- 添加适当的索引优化查询

### 3. 第三方服务
- API Key 通过环境变量管理
- 添加重试机制处理网络错误
- 记录详细的错误日志
- 实现熔断器模式防止雪崩

### 4. 支付安全
- 所有支付操作使用 HTTPS
- 验证 webhook 签名
- 实现幂等性防止重复扣款
- 记录完整的支付日志

## 注意事项
1. 所有示例代码中的 API Key 均为占位符，实际使用需替换为真实密钥
2. 敏感信息应通过环境变量管理，不要硬编码在代码中
3. 生产环境需添加错误处理和日志记录
4. 建议添加单元测试覆盖核心功能
5. 遵循各服务商的 API 使用限制和最佳实践

## 测试验证
每个 API 示例文件都包含 `if __name__ == '__main__'` 测试代码，可直接运行验证基本功能。

## 下一步
- [ ] 添加单元测试
- [ ] 添加 Docker 部署配置
- [ ] 添加 API 文档（Swagger/OpenAPI）
- [ ] 集成到 MVP 主项目

---
*创建时间：2026-03-18 | 负责人：全栈工程师 A | 状态：✅ 完成*
