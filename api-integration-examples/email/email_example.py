"""
邮件 API 集成示例代码
功能：发送文本/HTML 邮件、附件、模板邮件、批量发送
支持：SendGrid、SMTP
"""

from typing import Optional, List
from pydantic import BaseModel, EmailStr
from dotenv import load_dotenv
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path

load_dotenv()

# SendGrid 配置
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "your-api-key")
SENDGRID_FROM_EMAIL = os.getenv("SENDGRID_FROM_EMAIL", "noreply@yourdomain.com")

# SMTP 配置
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.yourdomain.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "your-username")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "your-password")
SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL", "noreply@yourdomain.com")


class EmailAttachment(BaseModel):
    """邮件附件"""
    filename: str
    content: bytes
    content_type: str = "application/octet-stream"


class EmailRequest(BaseModel):
    """邮件请求"""
    to_emails: List[str]
    subject: str
    content: str
    html_content: Optional[str] = None
    from_email: Optional[str] = None
    cc_emails: Optional[List[str]] = None
    bcc_emails: Optional[List[str]] = None
    attachments: Optional[List[EmailAttachment]] = None


class EmailResponse(BaseModel):
    """邮件发送响应"""
    success: bool
    message_id: Optional[str] = None
    message: str
    provider: str


# ============ SendGrid ============

class SendGridClient:
    """SendGrid 邮件客户端"""
    
    def __init__(self):
        self.api_key = SENDGRID_API_KEY
        self.from_email = SENDGRID_FROM_EMAIL
        self.base_url = "https://api.sendgrid.com/v3/mail/send"
    
    def send_email(self, request: EmailRequest) -> EmailResponse:
        """
        发送邮件
        
        Args:
            request: 邮件请求
        
        Returns:
            发送响应
        """
        import httpx
        
        # 构建请求体
        payload = {
            "personalizations": [{
                "to": [{"email": email} for email in request.to_emails],
                "subject": request.subject,
            }],
            "from": {"email": request.from_email or self.from_email},
            "content": [
                {
                    "type": "text/plain",
                    "value": request.content
                }
            ]
        }
        
        # 添加 HTML 内容
        if request.html_content:
            payload["content"].append({
                "type": "text/html",
                "value": request.html_content
            })
        
        # 添加抄送
        if request.cc_emails:
            payload["personalizations"][0]["cc"] = [
                {"email": email} for email in request.cc_emails
            ]
        
        # 添加密送
        if request.bcc_emails:
            payload["personalizations"][0]["bcc"] = [
                {"email": email} for email in request.bcc_emails
            ]
        
        # 添加附件
        if request.attachments:
            payload["attachments"] = []
            for attachment in request.attachments:
                import base64
                payload["attachments"].append({
                    "content": base64.b64encode(attachment.content).decode("utf-8"),
                    "filename": attachment.filename,
                    "type": attachment.content_type
                })
        
        # 打印请求信息（实际应发送 HTTP 请求）
        print(f"SendGrid 邮件请求:")
        print(f"  URL: {self.base_url}")
        print(f"  收件人：{request.to_emails}")
        print(f"  主题：{request.subject}")
        print(f"  发件人：{request.from_email or self.from_email}")
        if request.attachments:
            print(f"  附件：{len(request.attachments)} 个")
        print()
        
        # 模拟响应
        return EmailResponse(
            success=True,
            message_id=f"SG_{int(time.time())}",
            message="邮件已发送",
            provider="sendgrid"
        )
    
    def send_template_email(
        self,
        to_emails: List[str],
        template_id: str,
        template_data: dict,
        subject: Optional[str] = None
    ) -> EmailResponse:
        """
        发送模板邮件
        
        Args:
            to_emails: 收件人列表
            template_id: 模板 ID
            template_data: 模板数据
            subject: 主题（可选，模板中可定义）
        
        Returns:
            发送响应
        """
        import httpx
        
        # 构建动态模板数据
        dynamic_template_data = {}
        for key, value in template_data.items():
            dynamic_template_data[key] = value
        
        payload = {
            "personalizations": [{
                "to": [{"email": email} for email in to_emails],
                "dynamic_template_data": dynamic_template_data,
            }],
            "from": {"email": self.from_email},
            "template_id": template_id
        }
        
        if subject:
            payload["personalizations"][0]["subject"] = subject
        
        print(f"SendGrid 模板邮件:")
        print(f"  模板 ID: {template_id}")
        print(f"  收件人：{to_emails}")
        print(f"  模板数据：{template_data}\n")
        
        return EmailResponse(
            success=True,
            message_id=f"SG_TPL_{int(time.time())}",
            message="模板邮件已发送",
            provider="sendgrid"
        )


# ============ SMTP ============

class SmtpClient:
    """SMTP 邮件客户端"""
    
    def __init__(self):
        self.host = SMTP_HOST
        self.port = SMTP_PORT
        self.username = SMTP_USER
        self.password = SMTP_PASSWORD
        self.from_email = SMTP_FROM_EMAIL
    
    def send_email(self, request: EmailRequest) -> EmailResponse:
        """
        发送邮件
        
        Args:
            request: 邮件请求
        
        Returns:
            发送响应
        """
        try:
            # 创建邮件
            msg = MIMEMultipart("alternative")
            msg["Subject"] = request.subject
            msg["From"] = request.from_email or self.from_email
            msg["To"] = ", ".join(request.to_emails)
            
            # 添加抄送
            if request.cc_emails:
                msg["Cc"] = ", ".join(request.cc_emails)
            
            # 添加文本内容
            text_part = MIMEText(request.content, "plain", "utf-8")
            msg.attach(text_part)
            
            # 添加 HTML 内容
            if request.html_content:
                html_part = MIMEText(request.html_content, "html", "utf-8")
                msg.attach(html_part)
            
            # 添加附件
            if request.attachments:
                for attachment in request.attachments:
                    part = MIMEBase(*attachment.content_type.split("/"))
                    part.set_payload(attachment.content)
                    encoders.encode_base64(part)
                    part.add_header(
                        "Content-Disposition",
                        f"attachment; filename={attachment.filename}"
                    )
                    msg.attach(part)
            
            # 连接 SMTP 服务器并发送
            print(f"SMTP 邮件请求:")
            print(f"  服务器：{self.host}:{self.port}")
            print(f"  收件人：{request.to_emails}")
            print(f"  主题：{request.subject}")
            if request.attachments:
                print(f"  附件：{len(request.attachments)} 个")
            print()
            
            # 实际发送代码（示例中仅打印）
            # with smtplib.SMTP(self.host, self.port) as server:
            #     server.starttls()
            #     server.login(self.username, self.password)
            #     server.send_message(msg)
            
            return EmailResponse(
                success=True,
                message_id=f"SMTP_{int(time.time())}",
                message="邮件已发送",
                provider="smtp"
            )
        except Exception as e:
            return EmailResponse(
                success=False,
                message=f"发送失败：{str(e)}",
                provider="smtp"
            )


# ============ 统一邮件服务 ============

class EmailService:
    """统一邮件服务（支持多 provider）"""
    
    def __init__(self, provider: str = "smtp"):
        """
        初始化邮件服务
        
        Args:
            provider: 服务提供商（sendgrid/smtp）
        """
        self.provider = provider
        if provider == "sendgrid":
            self.client = SendGridClient()
        elif provider == "smtp":
            self.client = SmtpClient()
        else:
            raise ValueError(f"不支持的服务商：{provider}")
    
    def send_email(self, request: EmailRequest) -> EmailResponse:
        """发送邮件"""
        return self.client.send_email(request)
    
    def send_verification_email(
        self,
        to_email: str,
        code: str,
        expire_minutes: int = 30
    ) -> EmailResponse:
        """
        发送验证邮件
        
        Args:
            to_email: 收件人邮箱
            code: 验证码
            expire_minutes: 有效期（分钟）
        
        Returns:
            发送响应
        """
        subject = "邮箱验证 - 验证码"
        content = f"您的验证码是：{code}\n\n有效期：{expire_minutes}分钟"
        html_content = f"""
        <html>
        <body>
            <h2>邮箱验证</h2>
            <p>您的验证码是：<strong style="font-size: 24px; color: #007bff;">{code}</strong></p>
            <p>有效期：{expire_minutes}分钟</p>
            <p>如非本人操作，请忽略此邮件。</p>
        </body>
        </html>
        """
        
        request = EmailRequest(
            to_emails=[to_email],
            subject=subject,
            content=content,
            html_content=html_content
        )
        
        return self.send_email(request)
    
    def send_welcome_email(
        self,
        to_email: str,
        username: str
    ) -> EmailResponse:
        """
        发送欢迎邮件
        
        Args:
            to_email: 收件人邮箱
            username: 用户名
        
        Returns:
            发送响应
        """
        subject = "欢迎加入我们！"
        content = f"欢迎 {username}！\n\n感谢您的注册。"
        html_content = f"""
        <html>
        <body>
            <h2>欢迎 {username}！</h2>
            <p>感谢您注册我们的服务。</p>
            <p>如有任何问题，请随时联系我们。</p>
        </body>
        </html>
        """
        
        request = EmailRequest(
            to_emails=[to_email],
            subject=subject,
            content=content,
            html_content=html_content
        )
        
        return self.send_email(request)


# ============ 使用示例 ============

import time

def example_email():
    """邮件发送示例"""
    print("=== 邮件 API 示例 ===\n")
    
    # 1. SendGrid 发送普通邮件
    print("1. SendGrid 普通邮件:")
    sendgrid_client = SendGridClient()
    request = EmailRequest(
        to_emails=["user@example.com"],
        subject="测试邮件",
        content="这是一封测试邮件",
        html_content="<h1>测试邮件</h1><p>这是一封测试邮件</p>"
    )
    response = sendgrid_client.send_email(request)
    print(f"   发送结果：{response.message}")
    print(f"   消息 ID: {response.message_id}\n")
    
    # 2. SendGrid 模板邮件
    print("2. SendGrid 模板邮件:")
    response = sendgrid_client.send_template_email(
        to_emails=["user@example.com"],
        template_id="d-1234567890abcdef",
        template_data={"username": "张三", "product": "测试产品"}
    )
    print(f"   发送结果：{response.message}\n")
    
    # 3. SMTP 发送带附件的邮件
    print("3. SMTP 带附件邮件:")
    smtp_client = SmtpClient()
    
    # 创建附件
    attachment = EmailAttachment(
        filename="test.pdf",
        content=b"PDF content here",
        content_type="application/pdf"
    )
    
    request = EmailRequest(
        to_emails=["user@example.com"],
        subject="带附件的测试邮件",
        content="这是一封带附件的测试邮件",
        attachments=[attachment]
    )
    response = smtp_client.send_email(request)
    print(f"   发送结果：{response.message}\n")
    
    # 4. 统一邮件服务 - 验证码
    print("4. 统一邮件服务 - 验证码:")
    email_service = EmailService(provider="smtp")
    response = email_service.send_verification_email(
        to_email="user@example.com",
        code="123456",
        expire_minutes=30
    )
    print(f"   服务商：{response.provider}")
    print(f"   发送结果：{response.message}\n")
    
    # 5. 统一邮件服务 - 欢迎邮件
    print("5. 统一邮件服务 - 欢迎邮件:")
    response = email_service.send_welcome_email(
        to_email="user@example.com",
        username="张三"
    )
    print(f"   发送结果：{response.message}\n")


if __name__ == "__main__":
    example_email()
