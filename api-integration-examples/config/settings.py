"""
API 集成示例 - 配置文件
"""
import os

# JWT 配置
JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-key")

# 支付宝配置
ALIPAY_APP_ID = os.getenv("ALIPAY_APP_ID", "")
ALIPAY_PRIVATE_KEY = os.getenv("ALIPAY_PRIVATE_KEY", "")

# 微信支付配置
WECHAT_APP_ID = os.getenv("WECHAT_APP_ID", "")
WECHAT_MCH_ID = os.getenv("WECHAT_MCH_ID", "")
WECHAT_API_KEY = os.getenv("WECHAT_API_KEY", "")

# 阿里云短信
ALIYUN_ACCESS_KEY = os.getenv("ALIYUN_ACCESS_KEY", "")
ALIYUN_ACCESS_SECRET = os.getenv("ALIYUN_ACCESS_SECRET", "")

# SendGrid 邮件
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "")

# 阿里云 OSS
OSS_ENDPOINT = os.getenv("OSS_ENDPOINT", "oss-cn-hangzhou.aliyuncs.com")
OSS_ACCESS_KEY = os.getenv("OSS_ACCESS_KEY", "")
OSS_ACCESS_SECRET = os.getenv("OSS_ACCESS_SECRET", "")
OSS_BUCKET = os.getenv("OSS_BUCKET", "")

# Claude AI
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# 高德地图
GAODE_API_KEY = os.getenv("GAODE_API_KEY", "")

# 极光推送
JIGUANG_APP_KEY = os.getenv("JIGUANG_APP_KEY", "")
JIGUANG_SECRET = os.getenv("JIGUANG_SECRET", "")

# Sentry
SENTRY_DSN = os.getenv("SENTRY_DSN", "")

# Redis
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# MongoDB
MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
