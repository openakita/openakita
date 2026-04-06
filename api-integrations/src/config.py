"""
配置管理模块
使用 pydantic-settings 管理所有 API 配置
"""
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional
from functools import lru_cache


class SlackSettings(BaseSettings):
    """Slack API 配置"""
    slack_bot_token: str = ""
    slack_signing_secret: str = ""
    
    model_config = SettingsConfigDict(env_file=".env", env_prefix="SLACK_")


class GitHubSettings(BaseSettings):
    """GitHub API 配置"""
    github_token: str = ""
    github_webhook_secret: str = ""
    
    model_config = SettingsConfigDict(env_file=".env", env_prefix="GITHUB_")


class StripeSettings(BaseSettings):
    """Stripe API 配置"""
    stripe_api_key: str = ""
    stripe_webhook_secret: str = ""
    
    model_config = SettingsConfigDict(env_file=".env", env_prefix="STRIPE_")


class SalesforceSettings(BaseSettings):
    """Salesforce API 配置"""
    salesforce_client_id: str = ""
    salesforce_client_secret: str = ""
    salesforce_username: str = ""
    salesforce_password: str = ""
    salesforce_security_token: str = ""
    
    model_config = SettingsConfigDict(env_file=".env", env_prefix="SALESFORCE_")


class JiraSettings(BaseSettings):
    """Jira API 配置"""
    jira_api_token: str = ""
    jira_email: str = ""
    jira_server: str = ""
    
    model_config = SettingsConfigDict(env_file=".env", env_prefix="JIRA_")


class SendGridSettings(BaseSettings):
    """SendGrid API 配置"""
    sendgrid_api_key: str = ""
    
    model_config = SettingsConfigDict(env_file=".env", env_prefix="SENDGRID_")


class TwilioSettings(BaseSettings):
    """Twilio API 配置"""
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_phone_number: str = ""
    
    model_config = SettingsConfigDict(env_file=".env", env_prefix="TWILIO_")


class GoogleDriveSettings(BaseSettings):
    """Google Drive API 配置"""
    google_client_id: str = ""
    google_client_secret: str = ""
    google_refresh_token: str = ""
    
    model_config = SettingsConfigDict(env_file=".env", env_prefix="GOOGLE_")


class NotionSettings(BaseSettings):
    """Notion API 配置"""
    notion_api_key: str = ""
    
    model_config = SettingsConfigDict(env_file=".env", env_prefix="NOTION_")


class ZoomSettings(BaseSettings):
    """Zoom API 配置"""
    zoom_client_id: str = ""
    zoom_client_secret: str = ""
    zoom_account_id: str = ""
    
    model_config = SettingsConfigDict(env_file=".env", env_prefix="ZOOM_")


class AllSettings(BaseSettings):
    """所有 API 配置聚合"""
    # Slack
    slack_bot_token: str = ""
    slack_signing_secret: str = ""
    
    # GitHub
    github_token: str = ""
    github_webhook_secret: str = ""
    
    # Stripe
    stripe_api_key: str = ""
    stripe_webhook_secret: str = ""
    
    # Salesforce
    salesforce_client_id: str = ""
    salesforce_client_secret: str = ""
    salesforce_username: str = ""
    salesforce_password: str = ""
    salesforce_security_token: str = ""
    
    # Jira
    jira_api_token: str = ""
    jira_email: str = ""
    jira_server: str = ""
    
    # SendGrid
    sendgrid_api_key: str = ""
    
    # Twilio
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_phone_number: str = ""
    
    # Google Drive
    google_client_id: str = ""
    google_client_secret: str = ""
    google_refresh_token: str = ""
    
    # Notion
    notion_api_key: str = ""
    
    # Zoom
    zoom_client_id: str = ""
    zoom_client_secret: str = ""
    zoom_account_id: str = ""
    
    model_config = SettingsConfigDict(env_file=".env")


@lru_cache()
def get_settings() -> AllSettings:
    """获取配置（单例模式）"""
    return AllSettings()
