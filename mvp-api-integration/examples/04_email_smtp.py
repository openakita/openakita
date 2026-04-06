"""
API 集成示例 04: SMTP 邮件发送

功能：
- 发送普通邮件
- 发送 HTML 邮件
- 发送带附件的邮件
- 批量发送邮件

依赖：
pip install yagmail  # 或使用内置 smtplib

文档：
https://docs.python.org/3/library/smtplib.html
"""

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path

# ============ 配置区域 ============
SMTP_SERVER = "smtp.example.com"
SMTP_PORT = 587  # TLS 端口
SMTP_USER = "your_email@example.com"
SMTP_PASSWORD = "your_password_or_app_token"
FROM_EMAIL = "your_email@example.com"
FROM_NAME = "你的发件人名称"
# =================================


class EmailClient:
    """SMTP 邮件客户端"""
    
    def __init__(self, smtp_server, smtp_port, username, password, from_email, from_name=None):
        self.smtp_server = smtp_server
        self.smtp_port = smtp_port
        self.username = username
        self.password = password
        self.from_email = from_email
        self.from_name = from_name or from_email
    
    def send_text_email(self, to_email, subject, content):
        """
        发送纯文本邮件
        
        Args:
            to_email: 收件人邮箱
            subject: 邮件主题
            content: 邮件内容
        
        Returns:
            bool: 是否发送成功
        """
        msg = MIMEText(content, 'plain', 'utf-8')
        msg['From'] = f"{self.from_name} <{self.from_email}>"
        msg['To'] = to_email
        msg['Subject'] = subject
        
        try:
            server = smtplib.SMTP(self.smtp_server, self.smtp_port)
            server.starttls()
            server.login(self.username, self.password)
            server.sendmail(self.from_email, [to_email], msg.as_string())
            server.quit()
            print(f"✅ 邮件已发送至 {to_email}")
            return True
        except Exception as e:
            print(f"❌ 发送失败：{e}")
            return False
    
    def send_html_email(self, to_email, subject, html_content):
        """
        发送 HTML 邮件
        
        Args:
            to_email: 收件人邮箱
            subject: 邮件主题
            html_content: HTML 内容
        
        Returns:
            bool: 是否发送成功
        """
        msg = MIMEMultipart('alternative')
        msg['From'] = f"{self.from_name} <{self.from_email}>"
        msg['To'] = to_email
        msg['Subject'] = subject
        
        # 纯文本版本 (兼容不支持 HTML 的客户端)
        text_part = MIMEText(f"请查看 HTML 版本邮件", 'plain', 'utf-8')
        # HTML 版本
        html_part = MIMEText(html_content, 'html', 'utf-8')
        
        msg.attach(text_part)
        msg.attach(html_part)
        
        try:
            server = smtplib.SMTP(self.smtp_server, self.smtp_port)
            server.starttls()
            server.login(self.username, self.password)
            server.sendmail(self.from_email, [to_email], msg.as_string())
            server.quit()
            print(f"✅ HTML 邮件已发送至 {to_email}")
            return True
        except Exception as e:
            print(f"❌ 发送失败：{e}")
            return False
    
    def send_email_with_attachment(self, to_email, subject, content, attachment_paths):
        """
        发送带附件的邮件
        
        Args:
            to_email: 收件人邮箱
            subject: 邮件主题
            content: 邮件内容
            attachment_paths: 附件路径列表
        
        Returns:
            bool: 是否发送成功
        """
        msg = MIMEMultipart()
        msg['From'] = f"{self.from_name} <{self.from_email}>"
        msg['To'] = to_email
        msg['Subject'] = subject
        
        # 添加邮件正文
        msg.attach(MIMEText(content, 'plain', 'utf-8'))
        
        # 添加附件
        for file_path in attachment_paths:
            try:
                with open(file_path, 'rb') as f:
                    part = MIMEBase('application', 'octet-stream')
                    part.set_payload(f.read())
                    encoders.encode_base64(part)
                    
                    # 设置附件文件名
                    filename = Path(file_path).name
                    part.add_header(
                        'Content-Disposition',
                        f'attachment; filename="{filename}"'
                    )
                    msg.attach(part)
            except Exception as e:
                print(f"⚠️  附件 {file_path} 添加失败：{e}")
        
        try:
            server = smtplib.SMTP(self.smtp_server, self.smtp_port)
            server.starttls()
            server.login(self.username, self.password)
            server.sendmail(self.from_email, [to_email], msg.as_string())
            server.quit()
            print(f"✅ 带附件邮件已发送至 {to_email}")
            return True
        except Exception as e:
            print(f"❌ 发送失败：{e}")
            return False
    
    def batch_send(self, recipients, subject, content):
        """
        批量发送邮件
        
        Args:
            recipients: 收件人列表
            subject: 邮件主题
            content: 邮件内容
        
        Returns:
            dict: 发送结果统计
        """
        results = {'success': 0, 'failed': 0, 'details': []}
        
        for email in recipients:
            success = self.send_text_email(email, subject, content)
            if success:
                results['success'] += 1
            else:
                results['failed'] += 1
            results['details'].append({'email': email, 'success': success})
        
        return results


# ============ 使用示例 ============
if __name__ == "__main__":
    print("=" * 50)
    print("SMTP 邮件 API 集成示例")
    print("=" * 50)
    
    email_client = EmailClient(
        SMTP_SERVER, SMTP_PORT, SMTP_USER, SMTP_PASSWORD,
        FROM_EMAIL, FROM_NAME
    )
    
    # 1. 发送普通邮件
    print("\n1️⃣  发送普通邮件")
    # email_client.send_text_email(
    #     "user@example.com",
    #     "测试邮件",
    #     "这是一封测试邮件的内容"
    # )
    print("⚠️  实际环境调用 send_text_email 发送")
    
    # 2. 发送 HTML 邮件
    print("\n2️⃣  发送 HTML 邮件")
    html = """
    <html>
        <body>
            <h1>欢迎注册</h1>
            <p>感谢您的注册，请点击下方按钮激活账户：</p>
            <a href="https://example.com/activate" style="background-color: #4CAF50; color: white; padding: 10px 20px; text-decoration: none;">激活账户</a>
        </body>
    </html>
    """
    # email_client.send_html_email("user@example.com", "欢迎注册", html)
    print("⚠️  实际环境调用 send_html_email 发送")
    
    # 3. 发送带附件的邮件
    print("\n3️⃣  发送带附件的邮件")
    # email_client.send_email_with_attachment(
    #     "user@example.com",
    #     "月度报告",
    #     "请查收附件中的月度报告",
    #     ["report.pdf", "data.xlsx"]
    # )
    print("⚠️  实际环境调用 send_email_with_attachment 发送")
    
    # 4. 批量发送
    print("\n4️⃣  批量发送示例")
    # recipients = ["user1@example.com", "user2@example.com"]
    # results = email_client.batch_send(recipients, "通知", "这是一条群发消息")
    print("⚠️  实际环境调用 batch_send 批量发送")
    
    print("\n" + "=" * 50)
    print("关键要点:")
    print("1. 推荐使用应用专用密码 (非登录密码)")
    print("2. 注意邮件发送频率限制 (防封号)")
    print("3. 批量发送建议添加延迟 (避免被识别为垃圾邮件)")
    print("4. HTML 邮件应包含纯文本版本 (兼容性)")
    print("=" * 50)
