"""
SMTP 邮件发送集成示例
用于 MVP 用户通知、验证码发送等场景
"""
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from typing import List, Optional, Dict
from dataclasses import dataclass


@dataclass
class EmailConfig:
    """邮件服务器配置"""
    smtp_server: str
    smtp_port: int
    username: str
    password: str
    use_tls: bool = True
    sender_name: str = ""


@dataclass
class EmailMessage:
    """邮件消息"""
    subject: str
    body_text: str
    body_html: Optional[str] = None
    to_emails: List[str] = None
    cc_emails: List[str] = None
    bcc_emails: List[str] = None
    attachments: List[str] = None  # 文件路径列表
    
    def __post_init__(self):
        self.to_emails = self.to_emails or []
        self.cc_emails = self.cc_emails or []
        self.bcc_emails = self.bcc_emails or []
        self.attachments = self.attachments or []


class SMTPEmailClient:
    """
    SMTP 邮件发送客户端
    
    使用场景:
    - 用户注册/登录验证码
    - 密码重置邮件
    - 系统通知邮件
    - 工作流审批通知
    """
    
    def __init__(self, config: EmailConfig):
        """
        初始化 SMTP 客户端
        
        Args:
            config: 邮件服务器配置
        """
        self.config = config
    
    def send_email(self, message: EmailMessage, 
                   from_email: Optional[str] = None) -> Dict:
        """
        发送邮件
        
        Args:
            message: 邮件消息对象
            from_email: 发件人邮箱（可选，默认使用配置中的 username）
        
        Returns:
            发送结果字典
        """
        from_email = from_email or self.config.username
        
        # 创建邮件
        msg = MIMEMultipart("alternative")
        msg["Subject"] = message.subject
        msg["From"] = f"{self.config.sender_name} <{from_email}>" if self.config.sender_name else from_email
        msg["To"] = ", ".join(message.to_emails)
        
        if message.cc_emails:
            msg["Cc"] = ", ".join(message.cc_emails)
        
        # 添加纯文本内容
        if message.body_text:
            part_text = MIMEText(message.body_text, "plain", "utf-8")
            msg.attach(part_text)
        
        # 添加 HTML 内容
        if message.body_html:
            part_html = MIMEText(message.body_html, "html", "utf-8")
            msg.attach(part_html)
        
        # 添加附件
        for file_path in message.attachments:
            try:
                with open(file_path, "rb") as attachment:
                    part = MIMEBase("application", "octet-stream")
                    part.set_payload(attachment.read())
                    encoders.encode_base64(part)
                    
                    # 设置附件文件名
                    filename = file_path.split("/")[-1].split("\\")[-1]
                    part.add_header(
                        "Content-Disposition",
                        f"attachment; filename= {filename}"
                    )
                    msg.attach(part)
            except Exception as e:
                return {
                    "success": False,
                    "error": f"附件 {file_path} 添加失败：{str(e)}"
                }
        
        # 收集所有收件人
        all_recipients = (
            message.to_emails + 
            message.cc_emails + 
            message.bcc_emails
        )
        
        try:
            # 创建 SMTP 连接
            if self.config.use_tls:
                context = ssl.create_default_context()
                server = smtplib.SMTP(self.config.smtp_server, self.config.smtp_port)
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
            else:
                server = smtplib.SMTP(self.config.smtp_server, self.config.smtp_port)
            
            # 登录
            server.login(self.config.username, self.config.password)
            
            # 发送邮件
            server.sendmail(from_email, all_recipients, msg.as_string())
            server.quit()
            
            return {
                "success": True,
                "message": f"邮件已发送至 {len(message.to_emails)} 个收件人",
                "recipients": all_recipients
            }
            
        except smtplib.SMTPAuthenticationError:
            return {"success": False, "error": "SMTP 认证失败，请检查用户名和密码"}
        except smtplib.SMTPConnectError:
            return {"success": False, "error": "无法连接到 SMTP 服务器"}
        except Exception as e:
            return {"success": False, "error": f"发送失败：{str(e)}"}
    
    def send_verification_code(self, to_email: str, code: str, 
                               expire_minutes: int = 10) -> Dict:
        """
        发送验证码邮件
        
        Args:
            to_email: 收件人邮箱
            code: 验证码
            expire_minutes: 有效期（分钟）
        
        Returns:
            发送结果字典
        """
        subject = "【MVP 平台】验证码"
        
        body_text = f"""
尊敬的用戶：

您的验证码是：{code}

验证码有效期为 {expire_minutes} 分钟，请尽快使用。
如非本人操作，请忽略此邮件。

此致
MVP 平台团队
"""
        
        body_html = f"""
<html>
<body style="font-family: Arial, sans-serif; line-height: 1.6;">
    <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
        <h2 style="color: #333;">验证码</h2>
        <p>尊敬的用戶：</p>
        <p>您的验证码是：</p>
        <div style="background-color: #f5f5f5; padding: 15px; text-align: center; margin: 20px 0;">
            <span style="font-size: 32px; font-weight: bold; color: #007bff; letter-spacing: 5px;">{code}</span>
        </div>
        <p>验证码有效期为 <strong>{expire_minutes} 分钟</strong>，请尽快使用。</p>
        <p style="color: #666; font-size: 14px;">如非本人操作，请忽略此邮件。</p>
        <hr style="border: none; border-top: 1px solid #eee; margin-top: 30px;">
        <p style="color: #999; font-size: 12px;">此致<br>MVP 平台团队</p>
    </div>
</body>
</html>
"""
        
        message = EmailMessage(
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            to_emails=[to_email]
        )
        
        return self.send_email(message)
    
    def send_password_reset(self, to_email: str, username: str, 
                           reset_url: str, expire_hours: int = 24) -> Dict:
        """
        发送密码重置邮件
        
        Args:
            to_email: 收件人邮箱
            username: 用户名
            reset_url: 密码重置链接
            expire_hours: 链接有效期（小时）
        
        Returns:
            发送结果字典
        """
        subject = "【MVP 平台】密码重置"
        
        body_text = f"""
尊敬的 {username}：

您请求重置密码。请点击以下链接重置您的密码：

{reset_url}

链接有效期为 {expire_hours} 小时。
如非本人操作，请忽略此邮件。

此致
MVP 平台团队
"""
        
        body_html = f"""
<html>
<body style="font-family: Arial, sans-serif; line-height: 1.6;">
    <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
        <h2 style="color: #333;">密码重置</h2>
        <p>尊敬的 {username}：</p>
        <p>您请求重置密码。请点击以下按钮重置您的密码：</p>
        <div style="text-align: center; margin: 30px 0;">
            <a href="{reset_url}" style="background-color: #007bff; color: white; padding: 12px 30px; text-decoration: none; border-radius: 5px; display: inline-block;">重置密码</a>
        </div>
        <p>或复制以下链接到浏览器：</p>
        <p style="word-break: break-all; color: #007bff;">{reset_url}</p>
        <p>链接有效期为 <strong>{expire_hours} 小时</strong>。</p>
        <p style="color: #666; font-size: 14px;">如非本人操作，请忽略此邮件。</p>
        <hr style="border: none; border-top: 1px solid #eee; margin-top: 30px;">
        <p style="color: #999; font-size: 12px;">此致<br>MVP 平台团队</p>
    </div>
</body>
</html>
"""
        
        message = EmailMessage(
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            to_emails=[to_email]
        )
        
        return self.send_email(message)


# ============== 使用示例 ==============

def example_usage():
    """使用示例"""
    
    # 配置（从环境变量或配置文件中获取）
    config = EmailConfig(
        smtp_server="smtp.example.com",
        smtp_port=587,
        username="noreply@example.com",
        password="your-password",
        sender_name="MVP 平台"
    )
    
    # 初始化客户端
    client = SMTPEmailClient(config)
    
    # 示例 1: 发送验证码
    print("=== 发送验证码 ===")
    result = client.send_verification_code(
        to_email="user@example.com",
        code="123456",
        expire_minutes=10
    )
    print(f"结果：{result}")
    
    # 示例 2: 发送密码重置邮件
    print("\n=== 发送密码重置邮件 ===")
    result = client.send_password_reset(
        to_email="user@example.com",
        username="test_user",
        reset_url="https://example.com/reset-password?token=abc123",
        expire_hours=24
    )
    print(f"结果：{result}")
    
    # 示例 3: 发送自定义邮件
    print("\n=== 发送自定义邮件 ===")
    message = EmailMessage(
        subject="工作流审批通知",
        body_text="您有一个待审批的工作流，请登录系统查看。",
        body_html="<h3>工作流审批通知</h3><p>您有一个待审批的工作流，请<a href='https://example.com'>登录系统</a>查看。</p>",
        to_emails=["manager@example.com"],
        cc_emails=["admin@example.com"]
    )
    result = client.send_email(message)
    print(f"结果：{result}")


if __name__ == "__main__":
    example_usage()
