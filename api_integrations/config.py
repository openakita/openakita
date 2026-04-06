"""
API 集成配置管理
"""
import os
from dotenv import load_dotenv
from typing import Optional

load_dotenv()


class APIConfig:
    """API 配置类"""
    
    # 邮件 API (SendGrid)
    SENDGRID_API_KEY: str = os.getenv("SENDGRID_API_KEY", "")
    SENDGRID_FROM_EMAIL: str = os.getenv("SENDGRID_FROM_EMAIL", "noreply@example.com")
    
    # 日历 API (Google)
    GOOGLE_CALENDAR_API_KEY: str = os.getenv("GOOGLE_CALENDAR_API_KEY", "")
    GOOGLE_CALENDAR_ID: str = os.getenv("GOOGLE_CALENDAR_ID", "primary")
    
    # 文档 API (Notion)
    NOTION_API_KEY: str = os.getenv("NOTION_API_KEY", "")
    NOTION_DATABASE_ID: str = os.getenv("NOTION_DATABASE_ID", "")
    
    # CRM API (HubSpot)
    HUBSPOT_API_KEY: str = os.getenv("HUBSPOT_API_KEY", "")
    HUBSPOT_APP_ID: str = os.getenv("HUBSPOT_APP_ID", "")
    
    # 即时通讯 API (Slack)
    SLACK_BOT_TOKEN: str = os.getenv("SLACK_BOT_TOKEN", "")
    SLACK_CHANNEL_ID: str = os.getenv("SLACK_CHANNEL_ID", "")
    
    # 云存储 API (AWS S3)
    AWS_ACCESS_KEY_ID: str = os.getenv("AWS_ACCESS_KEY_ID", "")
    AWS_SECRET_ACCESS_KEY: str = os.getenv("AWS_SECRET_ACCESS_KEY", "")
    AWS_REGION: str = os.getenv("AWS_REGION", "us-east-1")
    AWS_S3_BUCKET: str = os.getenv("AWS_S3_BUCKET", "")
    
    # 数据库 API (Supabase)
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
    SUPABASE_KEY: str = os.getenv("SUPABASE_KEY", "")
    
    # 支付 API (Stripe)
    STRIPE_API_KEY: str = os.getenv("STRIPE_API_KEY", "")
    STRIPE_WEBHOOK_SECRET: str = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    
    # SMS API (Twilio)
    TWILIO_ACCOUNT_SID: str = os.getenv("TWILIO_ACCOUNT_SID", "")
    TWILIO_AUTH_TOKEN: str = os.getenv("TWILIO_AUTH_TOKEN", "")
    TWILIO_PHONE_NUMBER: str = os.getenv("TWILIO_PHONE_NUMBER", "")
    
    # 第三方数据 API
    WEATHER_API_KEY: str = os.getenv("WEATHER_API_KEY", "")
    MAPS_API_KEY: str = os.getenv("MAPS_API_KEY", "")
    
    @classmethod
    def validate(cls, service: str) -> bool:
        """验证指定服务的配置是否完整"""
        config_map = {
            "sendgrid": [cls.SENDGRID_API_KEY],
            "google_calendar": [cls.GOOGLE_CALENDAR_API_KEY],
            "notion": [cls.NOTION_API_KEY],
            "hubspot": [cls.HUBSPOT_API_KEY],
            "slack": [cls.SLACK_BOT_TOKEN],
            "aws_s3": [cls.AWS_ACCESS_KEY_ID, cls.AWS_SECRET_ACCESS_KEY],
            "supabase": [cls.SUPABASE_URL, cls.SUPABASE_KEY],
            "stripe": [cls.STRIPE_API_KEY],
            "twilio": [cls.TWILIO_ACCOUNT_SID, cls.TWILIO_AUTH_TOKEN],
            "weather": [cls.WEATHER_API_KEY],
        }
        
        keys = config_map.get(service.lower(), [])
        return all(keys)
