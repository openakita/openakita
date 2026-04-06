"""
API 集成示例 04: 邮件服务 (SendGrid/SMTP)
=======================================
功能：实现邮件发送、模板邮件、附件功能
依赖：pip install sendgrid python-dotenv
"""

import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from typing import List, Optional, Dict
from pydantic import BaseModel, EmailStr

# ==================== SendGrid 集成 ====================

class SendGridConfig:
    """SendGrid 配置"""
    API_KEY = "SG.xxxxxxxxxxxxxxxxxxxx.xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    FROM_EMAIL = "noreply@yourdomain.com"
    FROM_NAME = "Your App"

class SendGridEmail:
    """SendGrid 邮件服务"""
    
    def __init__(self):
        self.config = SendGridConfig()
        # 实际使用需要初始化客户端
        # import sendgrid
        # self.sg = sendgrid.SendGridAPIClient(api_key=self.config.API_KEY)
    
    def send_simple_email(self, to_email: str, subject: str, 
                         content: str, is_html: bool = False) -> Dict:
        """
        发送简单邮件
        
        Args:
            to_email: 收件人邮箱
            subject: 邮件主题
            content: 邮件内容
            is_html: 是否为 HTML 内容
            
        Returns:
            发送结果
        """
        # 构建邮件内容
        from_email = f"{self.config.FROM_NAME} <{self.config.FROM_EMAIL}>"
        
        # 实际调用 SendGrid API
        # from sendgrid.helpers.mail import Mail
        # message = Mail(
        #     from_email=from_email,
        #     to_emails=to_email,
        #     subject=subject,
        #     html_content=content if is_html else None,
        #     plain_text_content=content if not is_html else None
        # )
        # response = self.sg.send(message)
        
        return {
            "success": True,
            "message_id": "msg_xxxxxxxxxxxxxxxxxxxx",
            "status": "sent",
            "to": to_email,
            "subject": subject
        }
    
    def send_template_email(self, to_email: str, template_id: str,
                           dynamic_template_data: Dict) -> Dict:
        """
        发送模板邮件
        
        Args:
            to_email: 收件人邮箱
            template_id: SendGrid 模板 ID
            dynamic_template_data: 模板动态数据
            
        Returns:
            发送结果
        """
        # 实际调用
        # from sendgrid.helpers.mail import Mail
        # message = Mail(
        #     from_email=self.config.FROM_EMAIL,
        #     to_emails=to_email
        # )
        # message.template_id = template_id
        # message.dynamic_template_data = dynamic_template_data
        
        return {
            "success": True,
            "message_id": "msg_xxxxxxxxxxxxxxxxxxxx",
            "template_id": template_id,
            "to": to_email
        }
    
    def send_email_with_attachment(self, to_email: str, subject: str,
                                   content: str, file_path: str) -> Dict:
        """
        发送带附件的邮件
        
        Args:
            to_email: 收件人邮箱
            subject: 邮件主题
            content: 邮件内容
            file_path: 附件文件路径
            
        Returns:
            发送结果
        """
        import base64
        
        # 读取附件
        # with open(file_path, "rb") as f:
        #     data = f.read()
        #     encoded = base64.b64encode(data).decode()
        
        # 实际调用
        # from sendgrid.helpers.mail import Attachment, FileContent, FileName, FileType, Disposition
        # attachment = Attachment()
        # attachment.file_content = FileContent(encoded)
        # attachment.file_name = FileName(os.path.basename(file_path))
        # attachment.file_type = FileType("application/octet-stream")
        # attachment.disposition = Disposition("attachment")
        
        return {
            "success": True,
            "message_id": "msg_xxxxxxxxxxxxxxxxxxxx",
            "attachment": os.path.basename(file_path)
        }
    
    def send_batch_email(self, recipients: List[str], subject: str,
                        content: str) -> Dict:
        """
        批量发送邮件
        
        Args:
            recipients: 收件人列表
            subject: 邮件主题
            content: 邮件内容
            
        Returns:
            发送结果
        """
        results = []
        for email in recipients:
            result = self.send_simple_email(email, subject, content)
            results.append({
                "email": email,
                "success": result["success"]
            })
        
        return {
            "total": len(recipients),
            "success_count": sum(1 for r in results if r["success"]),
            "results": results
        }

# ==================== SMTP 邮件集成 ====================

class SMTPConfig:
    """SMTP 配置"""
    SMTP_SERVER = "smtp.yourdomain.com"
    SMTP_PORT = 587  # TLS
    SMTP_USER = "your_email@yourdomain.com"
    SMTP_PASSWORD = "your_password"
    FROM_EMAIL = "noreply@yourdomain.com"
    FROM_NAME = "Your App"
    USE_TLS = True

class SMTPEmail:
    """SMTP 邮件服务"""
    
    def __init__(self):
        self.config = SMTPConfig()
    
    def send_email(self, to_email: str, subject: str, content: str,
                   is_html: bool = False) -> Dict:
        """
        通过 SMTP 发送邮件
        
        Args:
            to_email: 收件人邮箱
            subject: 邮件主题
            content: 邮件内容
            is_html: 是否为 HTML 内容
            
        Returns:
            发送结果
        """
        try:
            # 创建邮件
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = f"{self.config.FROM_NAME} <{self.config.FROM_EMAIL}>"
            msg["To"] = to_email
            
            # 添加内容
            content_type = "html" if is_html else "plain"
            msg.attach(MIMEText(content, content_type, "utf-8"))
            
            # 连接 SMTP 服务器并发送
            # with smtplib.SMTP(self.config.SMTP_SERVER, self.config.SMTP_PORT) as server:
            #     if self.config.USE_TLS:
            #         server.starttls()
            #     server.login(self.config.SMTP_USER, self.config.SMTP_PASSWORD)
            #     server.sendmail(self.config.FROM_EMAIL, to_email, msg.as_string())
            
            return {
                "success": True,
                "message": "发送成功",
                "to": to_email,
                "subject": subject
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    def send_email_with_attachment(self, to_email: str, subject: str,
                                   content: str, file_path: str) -> Dict:
        """
        发送带附件的邮件
        
        Args:
            to_email: 收件人邮箱
            subject: 邮件主题
            content: 邮件内容
            file_path: 附件文件路径
            
        Returns:
            发送结果
        """
        try:
            # 创建邮件
            msg = MIMEMultipart()
            msg["Subject"] = subject
            msg["From"] = f"{self.config.FROM_NAME} <{self.config.FROM_EMAIL}>"
            msg["To"] = to_email
            
            # 添加正文
            msg.attach(MIMEText(content, "html", "utf-8"))
            
            # 添加附件
            # with open(file_path, "rb") as f:
            #     part = MIMEBase("application", "octet-stream")
            #     part.set_payload(f.read())
            #     encoders.encode_base64(part)
            #     part.add_header(
            #         "Content-Disposition",
            #         f"attachment; filename={os.path.basename(file_path)}"
            #     )
            #     msg.attach(part)
            
            # 发送邮件
            # with smtplib.SMTP(self.config.SMTP_SERVER, self.config.SMTP_PORT) as server:
            #     if self.config.USE_TLS:
            #         server.starttls()
            #     server.login(self.config.SMTP_USER, self.config.SMTP_PASSWORD)
            #     server.sendmail(self.config.FROM_EMAIL, to_email, msg.as_string())
            
            return {
                "success": True,
                "message": "发送成功",
                "attachment": os.path.basename(file_path)
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

# ==================== 邮件服务封装 ====================

class EmailProvider:
    """邮件服务商枚举"""
    SENDGRID = "sendgrid"
    SMTP = "smtp"

class EmailService:
    """邮件服务（支持多服务商）"""
    
    def __init__(self, provider: str = EmailProvider.SENDGRID):
        self.provider = provider
        if provider == EmailProvider.SENDGRID:
            self.client = SendGridEmail()
        elif provider == EmailProvider.SMTP:
            self.client = SMTPEmail()
        else:
            raise ValueError(f"Unsupported provider: {provider}")
    
    def send(self, to: str, subject: str, content: str, 
             is_html: bool = False) -> Dict:
        """发送邮件"""
        return self.client.send_simple_email(to, subject, content, is_html)
    
    def send_template(self, to: str, template_id: str, 
                     data: Dict) -> Dict:
        """发送模板邮件"""
        if self.provider == EmailProvider.SENDGRID:
            return self.client.send_template_email(to, template_id, data)
        else:
            raise ValueError("Template email only supported for SendGrid")

# ==================== 使用示例 ====================

if __name__ == "__main__":
    # SendGrid 示例
    sendgrid_email = SendGridEmail()
    result = sendgrid_email.send_simple_email(
        to_email="user@example.com",
        subject="欢迎注册",
        content="<h1>欢迎加入我们的平台！</h1>",
        is_html=True
    )
    print(f"SendGrid 发送结果：{result}")
    
    # SMTP 示例
    smtp_email = SMTPEmail()
    result = smtp_email.send_email(
        to_email="user@example.com",
        subject="测试邮件",
        content="<p>这是一封测试邮件</p>",
        is_html=True
    )
    print(f"SMTP 发送结果：{result}")
    
    # 统一服务示例
    email_service = EmailService(provider=EmailProvider.SENDGRID)
    result = email_service.send(
        to="user@example.com",
        subject="统一邮件服务",
        content="测试内容"
    )
    print(f"统一邮件服务结果：{result}")
