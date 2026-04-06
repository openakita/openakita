"""
邮件 API 集成 - SendGrid
"""
from typing import List, Dict, Any
from .base_client import BaseAPIClient, APIError


class SendGridClient(BaseAPIClient):
    """SendGrid 邮件 API 客户端"""
    
    def __init__(self, api_key: str):
        super().__init__(
            base_url="https://api.sendgrid.com/v3",
            api_key=api_key
        )
    
    def _get_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
    
    async def send_email(
        self,
        to_emails: List[str],
        subject: str,
        content: str,
        from_email: str = "noreply@example.com",
        html: bool = False
    ) -> Dict[str, Any]:
        """发送邮件"""
        payload = {
            "personalizations": [
                {
                    "to": [{"email": email} for email in to_emails],
                    "subject": subject
                }
            ],
            "from": {"email": from_email},
            "content": [
                {
                    "type": "text/html" if html else "text/plain",
                    "value": content
                }
            ]
        }
        
        return await self.post("/mail/send", json=payload)
    
    async def send_template_email(
        self,
        to_emails: List[str],
        template_id: str,
        template_data: Dict[str, Any],
        from_email: str = "noreply@example.com"
    ) -> Dict[str, Any]:
        """使用模板发送邮件"""
        payload = {
            "personalizations": [
                {
                    "to": [{"email": email} for email in to_emails],
                    "dynamic_template_data": template_data
                }
            ],
            "from": {"email": from_email},
            "template_id": template_id
        }
        
        return await self.post("/mail/send", json=payload)
    
    async def test_connection(self) -> bool:
        """测试连接 - 获取 API 密钥信息"""
        try:
            await self.get("/api_keys")
            return True
        except APIError:
            return False


# 使用示例
async def example_sendgrid():
    """SendGrid 使用示例"""
    from config import APIConfig
    
    async with SendGridClient(APIConfig.SENDGRID_API_KEY) as client:
        # 发送普通邮件
        await client.send_email(
            to_emails=["user@example.com"],
            subject="测试邮件",
            content="这是一封测试邮件",
            from_email=APIConfig.SENDGRID_FROM_EMAIL
        )
        
        # 发送 HTML 邮件
        await client.send_email(
            to_emails=["user@example.com"],
            subject="HTML 测试",
            content="<h1>你好</h1><p>这是 HTML 邮件</p>",
            html=True
        )
