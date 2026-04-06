# MVP API 集成示例

本目录包含 MVP 项目常用的 10 个 API 集成方案示例代码。

## 目录结构

```
mvp_api_examples/
├── auth/
│   └── jwt_auth.py          # JWT 认证示例
├── im/
│   ├── wechat_work.py       # 企业微信消息推送
│   └── dingtalk.py          # 钉钉消息推送
├── email/
│   └── smtp_email.py        # SMTP 邮件发送
├── sms/
│   └── aliyun_sms.py        # 阿里云短信服务
├── storage/
│   └── aliyun_oss.py        # 阿里云对象存储
├── vector/
│   └── qdrant_client.py     # Qdrant 向量数据库
├── cache/
│   └── redis_cache.py       # Redis 缓存
├── async_task/
│   └── celery_task.py       # Celery 异步任务
├── payment/
│   └── wechat_pay.py        # 微信支付
├── requirements.txt         # Python 依赖
└── README.md               # 本文档
```

## API 清单

| 序号 | API 类型 | 文件名 | 集成复杂度 | 优先级 |
|------|----------|--------|------------|--------|
| 1 | JWT 认证 | auth/jwt_auth.py | 低 | P0 |
| 2 | 企业微信消息 | im/wechat_work.py | 低 | P0 |
| 3 | 钉钉消息 | im/dingtalk.py | 低 | P0 |
| 4 | SMTP 邮件 | email/smtp_email.py | 低 | P0 |
| 5 | 阿里云短信 | sms/aliyun_sms.py | 中 | P1 |
| 6 | 阿里云 OSS | storage/aliyun_oss.py | 中 | P1 |
| 7 | Qdrant 向量库 | vector/qdrant_client.py | 中 | P1 |
| 8 | Redis 缓存 | cache/redis_cache.py | 低 | P0 |
| 9 | Celery 异步任务 | async_task/celery_task.py | 中 | P1 |
| 10 | 微信支付 | payment/wechat_pay.py | 高 | P2 |

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

建议将敏感配置（如 API Key、密码等）放入环境变量或 `.env` 文件：

```bash
# .env 示例
JWT_SECRET_KEY=your-secret-key
WECHAT_WORK_WEBHOOK=https://qyapi.weixin.qq.com/...
DINGTALK_WEBHOOK=https://oapi.dingtalk.com/...
ALIYUN_ACCESS_KEY_ID=your-access-key-id
ALIYUN_ACCESS_KEY_SECRET=your-access-key-secret
REDIS_HOST=localhost
REDIS_PORT=6379
```

### 3. 运行示例

每个示例文件都可以独立运行：

```bash
# 运行 JWT 认证示例
python auth/jwt_auth.py

# 运行企业微信示例
python im/wechat_work.py

# 运行 Redis 缓存示例
python cache/redis_cache.py
```

## 使用指南

### JWT 认证

```python
from auth.jwt_auth import generate_token, verify_token

# 生成 token
token = generate_token("user_001", "username", ["admin"])

# 验证 token
payload = verify_token(token)
```

### 企业微信消息

```python
from im.wechat_work import WeChatWorkBot

bot = WeChatWorkBot(webhook_url, secret)
bot.send_text("Hello, 工作流通知！")
```

### Redis 缓存

```python
from cache.redis_cache import RedisCacheClient

cache = RedisCacheClient(host="localhost", port=6379)
cache.set("key", {"data": "value"}, expire_seconds=3600)
result = cache.get("key")
```

### Celery 异步任务

```python
# 启动 worker
celery -A async_task.celery_task worker --loglevel=info

# 启动 beat（定时任务）
celery -A async_task.celery_task beat --loglevel=info

# 提交任务
from async_task.celery_task import send_email_task
task = send_email_task.delay("user@example.com", "主题", "内容")
```

## 注意事项

1. **敏感配置**: 所有 API Key、密码等敏感信息不应硬编码在代码中，应使用环境变量或配置中心管理。

2. **生产环境**: 示例代码主要用于开发和测试，生产环境需要:
   - 添加错误处理和日志记录
   - 实现连接池和重试机制
   - 添加监控和告警

3. **微信支付**: 支付示例需要正式商户资质，沙箱环境仅用于测试。

4. **版本兼容**: 依赖包版本可能随时间更新，请根据实际需求调整。

## 下一步

- [ ] 添加单元测试
- [ ] 集成到 MVP 项目主代码库
- [ ] 编写 API 文档
- [ ] 添加更多 API 集成示例（如：阿里云视频点播、腾讯云 COS 等）

## 相关文档

- [MVP 技术方案](../docs/mvp-tech-design.md)
- [API 集成规范](../docs/api-integration-spec.md)
