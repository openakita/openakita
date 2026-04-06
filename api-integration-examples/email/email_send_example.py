# 邮件发送 API 示例 (SMTP + SendGrid)
# 安装依赖：pip install sendgrid

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from typing import List, Optional
import os

# ============================================
# 方案 1: SMTP 直接发送 (适用于小型项目)
# ============================================

class SMTPMailService:
    """SMTP 邮件服务"""
    
    def __init__(self, smtp_server: str, smtp_port: int, username: str, password: str):
        self.smtp_server = smtp_server
        self.smtp_port = smtp_port
        self.username = username
        self.password = password
    
    def send_email(
        self,
        subject: str,
        body: str,
        to_emails: List[str],
        from_email: Optional[str] = None,
        html: bool = False,
        attachments: Optional[List[str]] = None
    ) -> bool:
        """发送电子邮件"""
        try:
            msg = MIMEMultipart()
            msg['From'] = from_email or self.username
            msg['To'] = ', '.join(to_emails)
            msg['Subject'] = subject
            
            # 添加正文
            content_type = 'html' if html else 'plain'
            msg.attach(MIMEText(body, content_type))
            
            # 添加附件
            if attachments:
                for file_path in attachments:
                    self._attach_file(msg, file_path)
            
            # 发送邮件
            server = smtplib.SMTP(self.smtp_server, self.smtp_port)
            server.starttls()
            server.login(self.username, self.password)
            server.send_message(msg)
            server.quit()
            
            print(f"✓ 邮件发送成功：{to_emails}")
            return True
            
        except Exception as e:
            print(f"✗ 邮件发送失败：{str(e)}")
            return False
    
    def _attach_file(self, msg: MIMEMultipart, file_path: str):
        """添加附件"""
        with open(file_path, "rb") as attachment:
            part = MIMEBase('application', 'octet-stream')
            part.set_payload(attachment.read())
            encoders.encode_base64(part)
            part.add_header(
                'Content-Disposition',
                f'attachment; filename= {os.path.basename(file_path)}'
            )
            msg.attach(part)


# ============================================
# 方案 2: SendGrid API (适用于生产环境)
# ============================================

class SendGridMailService:
    """SendGrid 邮件服务"""
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api.sendgrid.com/v3/mail/send"
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
    
    def send_email(
        self,
        subject: str,
        body: str,
        to_emails: List[str],
        from_email: str,
        html: bool = False,
        template_id: Optional[str] = None
    ) -> bool:
        """发送电子邮件"""
        import requests
        
        personalizations = [{
            "to": [{"email": email} for email in to_emails],
            "subject": subject
        }]
        
        content_type = "text/html" if html else "text/plain"
        content = [{"type": content_type, "value": body}]
        
        payload = {
            "personalizations": personalizations,
            "from": {"email": from_email},
            "content": content
        }
        
        if template_id:
            payload["template_id"] = template_id
        
        try:
            response = requests.post(
                self.base_url,
                json=payload,
                headers=self.headers,
                timeout=30
            )
            
            if response.status_code == 202:
                print(f"✓ SendGrid 邮件发送成功：{to_emails}")
                return True
            else:
                print(f"✗ SendGrid 发送失败：{response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            print(f"✗ SendGrid 请求异常：{str(e)}")
            return False


# ============================================
# 使用示例
# ============================================

if __name__ == "__main__":
    # SMTP 示例
    smtp_service = SMTPMailService(
        smtp_server="smtp.gmail.com",
        smtp_port=587,
        username="your-email@gmail.com",
        password="your-app-password"
    )
    
    smtp_service.send_email(
        subject="测试邮件",
        body="这是一封测试邮件的内容",
        to_emails=["recipient@example.com"],
        html=False
    )
    
    # SendGrid 示例
    sendgrid_service = SendGridMailService(api_key="your-sendgrid-api-key")
    
    sendgrid_service.send_email(
        subject="SendGrid 测试邮件",
        body="<h1>这是一封 HTML 邮件</h1><p>内容...</p>",
        to_emails=["recipient@example.com"],
        from_email="noreply@yourdomain.com",
        html=True
    )
