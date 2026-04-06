"""
API 客户端封装模块
包含 10 个常用 API 的客户端实现
"""

import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

# 加载环境变量
load_dotenv("config/.env")


class EmailClient:
    """邮件服务客户端 (SMTP/SendGrid)"""
    
    def __init__(self):
        self.smtp_host = os.getenv("SMTP_HOST", "smtp.sendgrid.net")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.api_key = os.getenv("SENDGRID_API_KEY")
    
    def send_email(self, to, subject, body):
        """发送测试邮件"""
        try:
            msg = MIMEMultipart()
            msg['From'] = "noreply@test.com"
            msg['To'] = to
            msg['Subject'] = subject
            msg.attach(MIMEText(body, 'plain'))
            
            # 模拟发送（实际使用时取消注释）
            # server = smtplib.SMTP(self.smtp_host, self.smtp_port)
            # server.starttls()
            # server.login("apikey", self.api_key)
            # server.send_message(msg)
            # server.quit()
            
            return {"success": True, "message": "邮件发送成功（模拟）"}
        except Exception as e:
            return {"success": False, "error": str(e)}


class GoogleCalendarClient:
    """Google Calendar 客户端"""
    
    def __init__(self):
        self.client_id = os.getenv("GOOGLE_CLIENT_ID")
        self.client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    
    def create_event(self, summary, start_time, end_time):
        """创建日历事件"""
        # 实际实现需要 Google API 客户端库
        return {
            "success": True,
            "message": "日历事件创建成功（模拟）",
            "event": {"summary": summary, "start": start_time, "end": end_time}
        }


class GoogleSheetsClient:
    """Google Sheets 客户端"""
    
    def __init__(self):
        self.spreadsheet_id = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID")
    
    def read_sheet(self, range_name="Sheet1!A1:B10"):
        """读取表格数据"""
        return {
            "success": True,
            "message": "表格读取成功（模拟）",
            "data": [["姓名", "邮箱"], ["张三", "zhangsan@example.com"]]
        }
    
    def write_sheet(self, values, range_name="Sheet1!A1"):
        """写入表格数据"""
        return {
            "success": True,
            "message": "表格写入成功（模拟）",
            "updated_cells": len(values) * len(values[0]) if values else 0
        }


class HubSpotClient:
    """HubSpot CRM 客户端"""
    
    def __init__(self):
        self.access_token = os.getenv("HUBSPOT_ACCESS_TOKEN")
    
    def create_contact(self, email, firstname, lastname):
        """创建联系人"""
        return {
            "success": True,
            "message": "联系人创建成功（模拟）",
            "contact": {"email": email, "firstname": firstname, "lastname": lastname}
        }


class DingTalkClient:
    """钉钉机器人客户端"""
    
    def __init__(self):
        self.webhook_url = os.getenv("DINGTALK_WEBHOOK_URL")
    
    def send_message(self, content):
        """发送钉钉消息"""
        return {
            "success": True,
            "message": "钉钉消息发送成功（模拟）",
            "content": content
        }


class OSSClient:
    """阿里云 OSS 客户端"""
    
    def __init__(self):
        self.access_key_id = os.getenv("OSS_ACCESS_KEY_ID")
        self.access_key_secret = os.getenv("OSS_ACCESS_KEY_SECRET")
        self.bucket = os.getenv("OSS_BUCKET")
        self.endpoint = os.getenv("OSS_ENDPOINT")
    
    def upload_file(self, local_path, object_key):
        """上传文件到 OSS"""
        return {
            "success": True,
            "message": "文件上传成功（模拟）",
            "url": f"https://{self.bucket}.{self.endpoint}/{object_key}"
        }


class WebhookClient:
    """通用 HTTP Webhook 客户端"""
    
    def __init__(self):
        pass
    
    def send_post(self, url, data):
        """发送 POST 请求"""
        return {
            "success": True,
            "message": "Webhook 调用成功（模拟）",
            "url": url,
            "data": data
        }


class PostgreSQLClient:
    """PostgreSQL 数据库客户端"""
    
    def __init__(self):
        self.database_url = os.getenv("DATABASE_URL")
    
    def execute_query(self, query):
        """执行 SQL 查询"""
        return {
            "success": True,
            "message": "SQL 执行成功（模拟）",
            "query": query
        }


class PDFClient:
    """PDF 生成客户端"""
    
    def __init__(self):
        pass
    
    def generate_pdf(self, content, output_path):
        """生成 PDF 文档"""
        return {
            "success": True,
            "message": "PDF 生成成功（模拟）",
            "output_path": output_path
        }


class AliyunSMSClient:
    """阿里云短信客户端"""
    
    def __init__(self):
        self.access_key_id = os.getenv("ALIYUN_ACCESS_KEY_ID")
        self.access_key_secret = os.getenv("ALIYUN_ACCESS_KEY_SECRET")
        self.sign_name = os.getenv("ALIYUN_SIGN_NAME")
        self.template_code = os.getenv("ALIYUN_TEMPLATE_CODE")
    
    def send_sms(self, phone_number, template_params):
        """发送短信"""
        return {
            "success": True,
            "message": "短信发送成功（模拟）",
            "phone": phone_number,
            "params": template_params
        }


# 客户端工厂
def get_client(name):
    """获取 API 客户端实例"""
    clients = {
        "email": EmailClient,
        "calendar": GoogleCalendarClient,
        "sheets": GoogleSheetsClient,
        "crm": HubSpotClient,
        "dingtalk": DingTalkClient,
        "oss": OSSClient,
        "webhook": WebhookClient,
        "database": PostgreSQLClient,
        "pdf": PDFClient,
        "sms": AliyunSMSClient,
    }
    
    if name not in clients:
        raise ValueError(f"未知的 API 客户端：{name}")
    
    return clients[name]()
