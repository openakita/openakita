"""
配置文件模板
复制此文件为 settings.py 并填入实际的 API 密钥
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ==================== 认证配置 ====================
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "your-secret-key-change-in-production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_MINUTES = 30

# OAuth2 配置
OAUTH2_CLIENT_ID = os.getenv("OAUTH2_CLIENT_ID", "")
OAUTH2_CLIENT_SECRET = os.getenv("OAUTH2_CLIENT_SECRET", "")
OAUTH2_REDIRECT_URI = os.getenv("OAUTH2_REDIRECT_URI", "")

# ==================== 支付配置 ====================
# 支付宝
ALIPAY_APP_ID = os.getenv("ALIPAY_APP_ID", "")
ALIPAY_PRIVATE_KEY = os.getenv("ALIPAY_PRIVATE_KEY", "")
ALIPAY_PUBLIC_KEY = os.getenv("ALIPAY_PUBLIC_KEY", "")
ALIPAY_SANDBOX = True  # 沙箱环境

# 微信支付
WECHAT_PAY_APP_ID = os.getenv("WECHAT_PAY_APP_ID", "")
WECHAT_PAY_MCH_ID = os.getenv("WECHAT_PAY_MCH_ID", "")
WECHAT_PAY_API_KEY = os.getenv("WECHAT_PAY_API_KEY", "")
WECHAT_PAY_SANDBOX = True

# ==================== 短信配置 ====================
# 阿里云短信
ALIYUN_SMS_ACCESS_KEY = os.getenv("ALIYUN_SMS_ACCESS_KEY", "")
ALIYUN_SMS_ACCESS_SECRET = os.getenv("ALIYUN_SMS_ACCESS_SECRET", "")
ALIYUN_SMS_SIGN_NAME = os.getenv("ALIYUN_SMS_SIGN_NAME", "")
ALIYUN_SMS_TEMPLATE_CODE = os.getenv("ALIYUN_SMS_TEMPLATE_CODE", "")

# 腾讯云短信
TENCENT_SMS_SECRET_ID = os.getenv("TENCENT_SMS_SECRET_ID", "")
TENCENT_SMS_SECRET_KEY = os.getenv("TENCENT_SMS_SECRET_KEY", "")
TENCENT_SMS_SDK_APP_ID = os.getenv("TENCENT_SMS_SDK_APP_ID", "")
TENCENT_SMS_TEMPLATE_ID = os.getenv("TENCENT_SMS_TEMPLATE_ID", "")

# ==================== 邮件配置 ====================
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "")
SENDGRID_FROM_EMAIL = os.getenv("SENDGRID_FROM_EMAIL", "")

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.example.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")

# ==================== 对象存储配置 ====================
# 阿里云 OSS
OSS_ENDPOINT = os.getenv("OSS_ENDPOINT", "oss-cn-hangzhou.aliyuncs.com")
OSS_ACCESS_KEY_ID = os.getenv("OSS_ACCESS_KEY_ID", "")
OSS_ACCESS_KEY_SECRET = os.getenv("OSS_ACCESS_KEY_SECRET", "")
OSS_BUCKET_NAME = os.getenv("OSS_BUCKET_NAME", "")

# AWS S3
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
AWS_S3_BUCKET = os.getenv("AWS_S3_BUCKET", "")

# ==================== AI 大模型配置 ====================
# Claude (Anthropic)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-3-sonnet-20240229")

# 通义千问 (阿里云)
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
QWEN_MODEL = os.getenv("QWEN_MODEL", "qwen-max")

# ==================== 地图配置 ====================
# 高德地图
GAODE_MAP_API_KEY = os.getenv("GAODE_MAP_API_KEY", "")
GAODE_MAP_SECRET = os.getenv("GAODE_MAP_SECRET", "")

# 百度地图
BAIDU_MAP_API_KEY = os.getenv("BAIDU_MAP_API_KEY", "")
BAIDU_MAP_SECRET_KEY = os.getenv("BAIDU_MAP_SECRET_KEY", "")

# ==================== 推送通知配置 ====================
# 极光推送
JIGUANG_APP_KEY = os.getenv("JIGUANG_APP_KEY", "")
JIGUANG_MASTER_SECRET = os.getenv("JIGUANG_MASTER_SECRET", "")

# 个推
GETUI_APP_ID = os.getenv("GETUI_APP_ID", "")
GETUI_APP_KEY = os.getenv("GETUI_APP_KEY", "")
GETUI_MASTER_SECRET = os.getenv("GETUI_MASTER_SECRET", "")

# ==================== 监控配置 ====================
SENTRY_DSN = os.getenv("SENTRY_DSN", "")
SENTRY_ENVIRONMENT = os.getenv("SENTRY_ENVIRONMENT", "development")

# ELK Stack
ELK_HOST = os.getenv("ELK_HOST", "localhost")
ELK_PORT = int(os.getenv("ELK_PORT", 9200))

# ==================== 数据库配置 ====================
# Redis
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")
REDIS_DB = int(os.getenv("REDIS_DB", 0))

# MongoDB
MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "mvp_db")
