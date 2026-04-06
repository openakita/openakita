# 邮件服务 API 示例（SendGrid/SMTP）
# 用于 MVP 邮件通知功能

import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List, Optional

class EmailClient:
    """邮件客户端"""
    
    def __init__(self, provider: str = 'smtp'):
        self.provider = provider
        
        if provider == 'sendgrid':
            self.api_key = os.getenv('SENDGRID_API_KEY', 'your-sendgrid-key')
            self.base_url = 'https://api.sendgrid.com/v3'
        elif provider == 'smtp':
            self.smtp_server = os.getenv('SMTP_SERVER', 'smtp.example.com')
            self.smtp_port = int(os.getenv('SMTP_PORT', '587'))
            self.username = os.getenv('SMTP_USERNAME', 'your-username')
            self.password = os.getenv('SMTP_PASSWORD', 'your-password')
    
    def send_email(
        self,
        to_emails: List[str],
        subject: str,
        content: str,
        from_email: str = 'noreply@example.com',
        html: bool = False
    ) -> bool:
        """发送邮件"""
        if self.provider == 'sendgrid':
            return self._send_sendgrid(to_emails, subject, content, from_email, html)
        elif self.provider == 'smtp':
            return self._send_smtp(to_emails, subject, content, from_email, html)
    
    def _send_sendgrid(
        self,
        to_emails: List[str],
        subject: str,
        content: str,
        from_email: str,
        html: bool
    ) -> bool:
        """SendGrid API 发送"""
        import requests
        
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
        
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json'
        }
        
        response = requests.post(
            f'{self.base_url}/mail/send',
            headers=headers,
            json=payload
        )
        
        return response.status_code == 202
    
    def _send_smtp(
        self,
        to_emails: List[str],
        subject: str,
        content: str,
        from_email: str,
        html: bool
    ) -> bool:
        """SMTP 发送"""
        try:
            msg = MIMEMultipart()
            msg['From'] = from_email
            msg['To'] = ', '.join(to_emails)
            msg['Subject'] = subject
            
            msg.attach(MIMEText(content, 'html' if html else 'plain'))
            
            server = smtplib.SMTP(self.smtp_server, self.smtp_port)
            server.starttls()
            server.login(self.username, self.password)
            server.send_message(msg)
            server.quit()
            
            return True
        except Exception as e:
            print(f"SMTP Error: {e}")
            return False
    
    def send_welcome_email(self, email: str, username: str) -> bool:
        """发送欢迎邮件"""
        subject = "欢迎加入 MVP 平台！"
        content = f"""
        <h1>欢迎 {username}！</h1>
        <p>感谢您注册我们的平台。</p>
        <p>祝您使用愉快！</p>
        """
        return self.send_email([email], subject, content, html=True)
    
    def send_verification_email(self, email: str, code: str) -> bool:
        """发送验证码邮件"""
        subject = "邮箱验证"
        content = f"""
        <h1>邮箱验证</h1>
        <p>您的验证码是：<strong>{code}</strong></p>
        <p>验证码 10 分钟内有效。</p>
        """
        return self.send_email([email], subject, content, html=True)

# 使用示例
if __name__ == '__main__':
    # 初始化客户端
    email_client = EmailClient(provider='smtp')
    
    # 发送欢迎邮件
    success = email_client.send_welcome_email(
        email="user@example.com",
        username="TestUser"
    )
    
    print(f"Email sent: {success}")
    
    # 发送验证码
    success = email_client.send_verification_email(
        email="user@example.com",
        code="123456"
    )
    
    print(f"Verification email sent: {success}")
