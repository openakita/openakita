# SendGrid 邮件发送 API 集成示例
# 适用于 MVP 邮件通知、验证码、营销邮件

import os
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Attachment, FileContent, FileName, FileType, Disposition


class SendGridClient:
    """SendGrid 邮件客户端封装"""
    
    def __init__(self):
        self.api_key = os.getenv("SENDGRID_API_KEY", "your-api-key")
        self.from_email = os.getenv("SENDGRID_FROM_EMAIL", "noreply@yourdomain.com")
        self.from_name = os.getenv("SENDGRID_FROM_NAME", "Your App")
        self.sg = SendGridAPIClient(self.api_key)
    
    def send_email(
        self,
        to_email: str,
        subject: str,
        content: str,
        is_html: bool = False,
        cc: list = None,
        bcc: list = None
    ) -> dict:
        """
        发送单封邮件
        
        Args:
            to_email: 收件人邮箱
            subject: 邮件主题
            content: 邮件内容
            is_html: 是否为 HTML 格式
            cc: 抄送列表
            bcc: 密送列表
        
        Returns:
            发送结果
        """
        try:
            message = Mail(
                from_email=(self.from_email, self.from_name),
                to_emails=to_email,
                subject=subject,
                plain_text_content=content if not is_html else None,
                html_content=content if is_html else None
            )
            
            # 添加抄送
            if cc:
                message.cc = cc
            
            # 添加密送
            if bcc:
                message.bcc = bcc
            
            response = self.sg.send(message)
            return {
                "success": True,
                "status_code": response.status_code,
                "message_id": response.headers.get('X-Message-Id')
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def send_verification_email(self, to_email: str, code: str) -> dict:
        """
        发送验证码邮件
        
        Args:
            to_email: 收件人邮箱
            code: 验证码
        
        Returns:
            发送结果
        """
        subject = "【Your App】验证码"
        content = f"""
        <html>
        <body>
            <h2>验证码</h2>
            <p>您的验证码是：<strong style="font-size: 24px; color: #007bff;">{code}</strong></p>
            <p>验证码 5 分钟内有效，请勿泄露给他人。</p>
            <p>如非本人操作，请忽略此邮件。</p>
            <hr>
            <p style="color: #666; font-size: 12px;">This is an automated message, please do not reply.</p>
        </body>
        </html>
        """
        return self.send_email(to_email, subject, content, is_html=True)
    
    def send_welcome_email(self, to_email: str, username: str) -> dict:
        """
        发送欢迎邮件
        
        Args:
            to_email: 收件人邮箱
            username: 用户名
        
        Returns:
            发送结果
        """
        subject = "欢迎加入 Your App！"
        content = f"""
        <html>
        <body>
            <h2>欢迎 {username}！🎉</h2>
            <p>感谢您注册 Your App，我们很高兴您加入我们！</p>
            <h3>快速开始：</h3>
            <ul>
                <li><a href="https://yourapp.com/getting-started">新手指南</a></li>
                <li><a href="https://yourapp.com/features">功能介绍</a></li>
                <li><a href="https://yourapp.com/support">获取帮助</a></li>
            </ul>
            <p>如有任何问题，请随时联系我们的支持团队。</p>
            <p>祝您使用愉快！</p>
            <hr>
            <p style="color: #666; font-size: 12px;">Your App Team</p>
        </body>
        </html>
        """
        return self.send_email(to_email, subject, content, is_html=True)
    
    def send_bulk_email(self, recipients: list, subject: str, content: str, is_html: bool = False) -> dict:
        """
        批量发送邮件
        
        Args:
            recipients: 收件人列表 [{'email': 'xxx', 'name': 'xxx'}]
            subject: 邮件主题
            content: 邮件内容
            is_html: 是否为 HTML 格式
        
        Returns:
            发送结果统计
        """
        results = {"success": 0, "failed": 0, "errors": []}
        
        for recipient in recipients:
            result = self.send_email(
                to_email=recipient["email"],
                subject=subject,
                content=content,
                is_html=is_html
            )
            if result["success"]:
                results["success"] += 1
            else:
                results["failed"] += 1
                results["errors"].append({
                    "email": recipient["email"],
                    "error": result["error"]
                })
        
        results["success"] = results["success"] > 0
        return results


# 使用示例
if __name__ == "__main__":
    client = SendGridClient()
    
    # 1. 发送普通邮件
    result = client.send_email(
        to_email="user@example.com",
        subject="测试邮件",
        content="这是一封测试邮件",
        is_html=False
    )
    print(f"发送结果：{result}")
    
    # 2. 发送验证码邮件
    result = client.send_verification_email(
        to_email="user@example.com",
        code="123456"
    )
    print(f"验证码邮件：{result}")
    
    # 3. 发送欢迎邮件
    result = client.send_welcome_email(
        to_email="user@example.com",
        username="TestUser"
    )
    print(f"欢迎邮件：{result}")
    
    # 4. 批量发送
    recipients = [
        {"email": "user1@example.com", "name": "User 1"},
        {"email": "user2@example.com", "name": "User 2"}
    ]
    result = client.send_bulk_email(
        recipients=recipients,
        subject="批量测试",
        content="批量邮件内容"
    )
    print(f"批量发送：{result}")
