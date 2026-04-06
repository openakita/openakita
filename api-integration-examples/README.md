# MVP API 集成示例代码库

本仓库包含 10 个常用 API 集成方案及示例代码，用于 MVP 项目快速开发参考。

## 目录结构

```
api-integration-examples/
├── auth/           # 用户认证 (JWT/OAuth2)
├── payment/        # 支付 (支付宝/微信支付)
├── sms/            # 短信 (阿里云/腾讯云)
├── email/          # 邮件 (SendGrid/SMTP)
├── storage/        # 对象存储 (阿里云 OSS/S3)
├── ai/             # AI 大模型 (Claude/通义千问)
├── map/            # 地图 (高德/百度)
├── push/           # 推送通知 (极光/个推)
├── monitoring/     # 日志监控 (Sentry/ELK)
└── database/       # 数据库 (Redis/MongoDB)
```

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 配置环境变量
cp .env.example .env
# 编辑 .env 文件填入各平台 API 密钥

# 运行示例
python auth/jwt_example.py
```

## API 集成列表

| 模块 | 功能 | 支持平台 | 状态 |
|------|------|----------|------|
| auth | 用户认证 | JWT, OAuth2 | ✅ 已完成 |
| payment | 支付 | 支付宝，微信支付 | 🔄 进行中 |
| sms | 短信 | 阿里云，腾讯云 | ⏳ 待开发 |
| email | 邮件 | SendGrid, SMTP | ⏳ 待开发 |
| storage | 对象存储 | 阿里云 OSS, S3 | ⏳ 待开发 |
| ai | AI 大模型 | Claude, 通义千问 | ⏳ 待开发 |
| map | 地图 | 高德，百度 | ⏳ 待开发 |
| push | 推送通知 | 极光，个推 | ⏳ 待开发 |
| monitoring | 日志监控 | Sentry, ELK | ⏳ 待开发 |
| database | 数据库 | Redis, MongoDB | ⏳ 待开发 |

## 环境配置

在 `.env` 文件中配置以下环境变量：

```bash
# JWT 配置
JWT_SECRET_KEY=your-secret-key
JWT_ALGORITHM=HS256
JWT_EXPIRATION_MINUTES=30

# 支付宝
ALIPAY_APP_ID=your-app-id
ALIPAY_PRIVATE_KEY=your-private-key
ALIPAY_ALIPAY_PUBLIC_KEY=alipay-public-key

# 微信支付
WECHAT_APPID=your-appid
WECHAT_MCHID=your-mchid
WECHAT_API_KEY=your-api-key

# 阿里云短信
ALIYUN_ACCESS_KEY_ID=your-access-key-id
ALIYUN_ACCESS_KEY_SECRET=your-access-key-secret
ALIYUN_SIGN_NAME=your-sign-name
ALIYUN_TEMPLATE_CODE=your-template-code

# 其他配置...
```

## 注意事项

1. 所有示例代码仅供学习和参考，生产环境请根据实际需求调整
2. API 密钥请妥善保管，不要提交到版本控制
3. 部分 API 需要企业认证才能使用
4. 建议先在沙箱环境测试

## 许可证

MIT License
