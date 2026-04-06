"""
邮件发送 API 集成
支持：SMTP/SendGrid/阿里云邮件
"""
import aiosmtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List, Optional, Dict, Any
import httpx
import logging

from ..core.base import BaseAPIIntegration, APIConfig, APIResponse
from ..core.exceptions import AuthenticationError, ValidationError, ServiceUnavailableError
from ..core.config import config

logger = logging.getLogger(__name__)


class EmailAPIConfig(APIConfig):
    """邮件 API 配置"""
    provider: str = "smtp"  # smtp, sendgrid, aliyun
    smtp_host: Optional[str] = None
    smtp_port: int = 587
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    from_email: Optional[str] = None
    sendgrid_api_key: Optional[str] = None
    aliyun_access_key_id: Optional[str] = None
    aliyun_access_key_secret: Optional[str] = None
    aliyun_account_name: Optional[str] = None


class EmailAPI(BaseAPIIntegration):
    """邮件发送 API"""
    
    def __init__(self, config: EmailAPIConfig):
        super().__init__(config)
        self.config: EmailAPIConfig = config
        self.client: Optional[httpx.AsyncClient] = None
    
    async def initialize(self) -> None:
        """初始化"""
        self._validate_config()
        
        if self.config.provider in ('sendgrid', 'aliyun'):
            self.client = httpx.AsyncClient(timeout=self.config.timeout)
    
    async def close(self) -> None:
        """关闭连接"""
        if self.client:
            await self.client.aclose()
    
    def get_required_fields(self) -> list:
        """获取必需配置字段"""
        if self.config.provider == "smtp":
            return ['smtp_host', 'smtp_user', 'smtp_password', 'from_email']
        elif self.config.provider == "sendgrid":
            return ['sendgrid_api_key', 'from_email']
        elif self.config.provider == "aliyun":
            return ['aliyun_access_key_id', 'aliyun_access_key_secret', 'aliyun_account_name']
        return ['api_key']
    
    async def execute(self, action: str, **kwargs) -> APIResponse:
        """
        执行邮件操作
        
        Actions:
            - send: 发送邮件
                参数：to_emails, subject, content, html=False, cc=[], bcc=[]
        
        Returns:
            APIResponse
        """
        try:
            if action == "send":
                return await self._send_email(**kwargs)
            else:
                raise ValidationError(f"不支持的操作：{action}")
        except Exception as e:
            logger.error(f"邮件发送失败：{e}")
            return APIResponse(
                success=False,
                error=str(e),
                status_code=getattr(e, 'status_code', None)
            )
    
    async def _send_email(
        self,
        to_emails: List[str],
        subject: str,
        content: str,
        html: bool = False,
        cc: Optional[List[str]] = None,
        bcc: Optional[List[str]] = None,
        reply_to: Optional[str] = None
    ) -> APIResponse:
        """发送邮件"""
        if not to_emails:
            raise ValidationError("收件人不能为空")
        
        try:
            if self.config.provider == "smtp":
                await self._send_smtp(to_emails, subject, content, html, cc, bcc, reply_to)
            elif self.config.provider == "sendgrid":
                await self._send_sendgrid(to_emails, subject, content, html, cc, bcc, reply_to)
            elif self.config.provider == "aliyun":
                await self._send_aliyun(to_emails, subject, content, html, cc, bcc, reply_to)
            else:
                raise ValidationError(f"不支持的邮件服务商：{self.config.provider}")
            
            return APIResponse(
                success=True,
                data={"message_id": f"email_{len(to_emails)}_recipients"},
                status_code=200
            )
            
        except Exception as e:
            logger.error(f"邮件发送失败：{e}")
            raise
    
    async def _send_smtp(
        self, to_emails: List[str], subject: str, content: str,
        html: bool, cc: List[str], bcc: List[str], reply_to: str
    ) -> None:
        """SMTP 方式发送"""
        msg = MIMEMultipart()
        msg['From'] = self.config.from_email
        msg['To'] = ', '.join(to_emails)
        msg['Subject'] = subject
        
        if reply_to:
            msg['Reply-To'] = reply_to
        
        if cc:
            msg['Cc'] = ', '.join(cc)
        if bcc:
            # BCC 不添加到邮件头
            pass
        
        msg.attach(MIMEText(content, 'html' if html else 'plain', 'utf-8'))
        
        recipients = to_emails + (cc or []) + (bcc or [])
        
        await aiosmtplib.send(
            msg,
            hostname=self.config.smtp_host,
            port=self.config.smtp_port,
            username=self.config.smtp_user,
            password=self.config.smtp_password,
            start_tls=True if self.config.smtp_port == 587 else False
        )
    
    async def _send_sendgrid(
        self, to_emails: List[str], subject: str, content: str,
        html: bool, cc: List[str], bcc: List[str], reply_to: str
    ) -> None:
        """SendGrid API 发送"""
        url = "https://api.sendgrid.com/v3/mail/send"
        headers = {
            "Authorization": f"Bearer {self.config.sendgrid_api_key}",
            "Content-Type": "application/json"
        }
        
        personalization = {
            "to": [{"email": email} for email in to_emails],
            "subject": subject
        }
        
        if cc:
            personalization["cc"] = [{"email": email} for email in cc]
        if bcc:
            personalization["bcc"] = [{"email": email} for email in bcc]
        if reply_to:
            personalization["reply_to"] = {"email": reply_to}
        
        payload = {
            "personalizations": [personalization],
            "from": {"email": self.config.from_email},
            "subject": subject,
            "content": [{
                "type": "text/html" if html else "text/plain",
                "value": content
            }]
        }
        
        response = await self.client.post(url, headers=headers, json=payload)
        
        if response.status_code >= 400:
            raise ServiceUnavailableError(f"SendGrid API 错误：{response.text}")
    
    async def _send_aliyun(
        self, to_emails: List[str], subject: str, content: str,
        html: bool, cc: List[str], bcc: List[str], reply_to: str
    ) -> None:
        """阿里云邮件推送 API 发送"""
        # 简化实现，实际需按阿里云 API 文档签名
        url = "http://dm.aliyuncs.com/"
        
        params = {
            "Action": "SingleSendMail",
            "AccountName": self.config.aliyun_account_name,
            "ReplyToAddress": "true" if reply_to else "false",
            "AddressType": "1",
            "ToAddress": ",".join(to_emails),
            "FromAlias": self.config.from_email.split('@')[0],
            "Subject": subject,
            "HtmlBody": content if html else "",
            "TextBody": content if not html else "",
        }
        
        # TODO: 添加阿里云签名逻辑
        response = await self.client.get(url, params=params)
        
        if response.status_code >= 400:
            raise ServiceUnavailableError(f"阿里云邮件 API 错误：{response.text}")


# 工厂函数
def create_email_api(provider: str = "smtp") -> EmailAPI:
    """创建邮件 API 实例"""
    config_dict = {"provider": provider}
    
    if provider == "smtp":
        config_dict.update({
            "smtp_host": config.get("SMTP_HOST"),
            "smtp_port": config.get("SMTP_PORT", 587),
            "smtp_user": config.get("SMTP_USER"),
            "smtp_password": config.get("SMTP_PASSWORD"),
            "from_email": config.get("FROM_EMAIL"),
        })
    elif provider == "sendgrid":
        config_dict.update({
            "sendgrid_api_key": config.get("SENDGRID_API_KEY"),
            "from_email": config.get("FROM_EMAIL"),
        })
    elif provider == "aliyun":
        config_dict.update({
            "aliyun_access_key_id": config.get("ALIYUN_MAIL_ACCESS_KEY_ID"),
            "aliyun_access_key_secret": config.get("ALIYUN_MAIL_ACCESS_KEY_SECRET"),
            "aliyun_account_name": config.get("ALIYUN_MAIL_ACCOUNT_NAME"),
        })
    
    return EmailAPI(EmailAPIConfig(**config_dict))
