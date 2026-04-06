"""
邮件发送 API 集成示例
支持 SMTP 和 SendGrid 两种方式
"""

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional


class EmailAPI:
    """邮件发送 API 集成"""
    
    def __init__(self, smtp_server: str, smtp_port: int, username: str, password: str):
        self.smtp_server = smtp_server
        self.smtp_port = smtp_port
        self.username = username
        self.password = password
    
    def send_email(self, to: str, subject: str, content: str, html: bool = False) -> bool:
        """
        发送邮件
        
        Args:
            to: 收件人邮箱
            subject: 邮件主题
            content: 邮件内容
            html: 是否为 HTML 格式
            
        Returns:
            bool: 发送是否成功
        """
        try:
            msg = MIMEMultipart()
            msg['From'] = self.username
            msg['To'] = to
            msg['Subject'] = subject
            
            # 添加邮件内容
            content_type = 'html' if html else 'plain'
            msg.attach(MIMEText(content, content_type, 'utf-8'))
            
            # 连接 SMTP 服务器并发送
            server = smtplib.SMTP(self.smtp_server, self.smtp_port)
            server.starttls()
            server.login(self.username, self.password)
            server.send_message(msg)
            server.quit()
            
            print(f"✓ 邮件已发送至 {to}")
            return True
            
        except Exception as e:
            print(f"✗ 邮件发送失败：{e}")
            return False


class SendGridAPI:
    """SendGrid 邮件服务 API"""
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api.sendgrid.com/v3/mail/send"
    
    def send_email(self, to: str, subject: str, content: str, from_email: str) -> dict:
        """
        通过 SendGrid 发送邮件
        
        Args:
            to: 收件人邮箱
            subject: 邮件主题
            content: 邮件内容
            from_email: 发件人邮箱
            
        Returns:
            dict: 发送结果
        """
        import requests
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        data = {
            "personalizations": [{"to": [{"email": to}]}],
            "from": {"email": from_email},
            "subject": subject,
            "content": [{"type": "text/plain", "value": content}]
        }
        
        try:
            response = requests.post(self.base_url, json=data, headers=headers)
            if response.status_code == 202:
                print(f"✓ SendGrid 邮件已发送至 {to}")
                return {"success": True, "status_code": response.status_code}
            else:
                print(f"✗ SendGrid 发送失败：{response.text}")
                return {"success": False, "error": response.text}
        except Exception as e:
            print(f"✗ SendGrid 请求异常：{e}")
            return {"success": False, "error": str(e)}


# 使用示例
if __name__ == "__main__":
    # SMTP 方式
    smtp_email = EmailAPI(
        smtp_server="smtp.gmail.com",
        smtp_port=587,
        username="your_email@gmail.com",
        password="your_password"
    )
    smtp_email.send_email(
        to="recipient@example.com",
        subject="测试邮件",
        content="这是一封测试邮件"
    )
    
    # SendGrid 方式
    sendgrid = SendGridAPI(api_key="your_sendgrid_api_key")
    sendgrid.send_email(
        to="recipient@example.com",
        subject="测试邮件",
        content="这是一封测试邮件",
        from_email="noreply@yourdomain.com"
    )
