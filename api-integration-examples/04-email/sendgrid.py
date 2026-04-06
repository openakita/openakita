"""
API 集成示例 4: SendGrid 邮件
"""
import requests

class SendGridClient:
    def __init__(self, api_key):
        self.api_key = api_key
        self.base_url = "https://api.sendgrid.com/v3"
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
    
    def send_email(self, from_email, to_emails, subject, content, html_content=None):
        """发送邮件"""
        data = {
            "personalizations": [{
                "to": [{"email": email} for email in to_emails],
                "subject": subject
            }],
            "from": {"email": from_email},
            "content": [{
                "type": "text/plain",
                "value": content
            }]
        }
        
        if html_content:
            data["content"].append({
                "type": "text/html",
                "value": html_content
            })
        
        response = requests.post(
            f"{self.base_url}/mail/send",
            json=data,
            headers=self.headers
        )
        
        return response.status_code == 202
    
    def send_template_email(self, from_email, to_emails, template_id, dynamic_data=None):
        """发送模板邮件"""
        data = {
            "personalizations": [{
                "to": [{"email": email} for email in to_emails],
                "dynamic_template_data": dynamic_data or {}
            }],
            "from": {"email": from_email},
            "template_id": template_id
        }
        
        response = requests.post(
            f"{self.base_url}/mail/send",
            json=data,
            headers=self.headers
        )
        
        return response.status_code == 202

# 使用示例
if __name__ == "__main__":
    sg = SendGridClient("your_api_key")
    # success = sg.send_email(
    #     "from@example.com",
    #     ["to@example.com"],
    #     "测试邮件",
    #     "这是测试内容"
    # )
    print("SendGrid 邮件示例已就绪")
