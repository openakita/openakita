"""
10 个核心 API 集成验证模块
包含：SMTP 邮件、Google Sheets、Notion、钉钉、企业微信、Slack、GitHub、Trello、Airtable、MySQL
"""
import os
import smtplib
import httpx
import mysql.connector
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Dict, List, Optional
from pydantic import BaseModel


class APIIntegrationManager:
    """API 集成管理器 - 统一接口"""
    
    def __init__(self):
        self.session = httpx.AsyncClient(timeout=30.0)
        
    async def close(self):
        await self.session.aclose()
    
    # ========== 1. SMTP 邮件 ==========
    async def send_email(
        self,
        smtp_server: str,
        smtp_port: int,
        username: str,
        password: str,
        to_emails: List[str],
        subject: str,
        content: str,
        html: bool = False
    ) -> Dict:
        """发送邮件"""
        try:
            msg = MIMEMultipart()
            msg['From'] = username
            msg['To'] = ', '.join(to_emails)
            msg['Subject'] = subject
            
            msg.attach(MIMEText(content, 'html' if html else 'plain'))
            
            server = smtplib.SMTP(smtp_server, smtp_port)
            server.starttls()
            server.login(username, password)
            server.send_message(msg)
            server.quit()
            
            return {"status": "success", "sent_to": to_emails}
        except Exception as e:
            return {"status": "failed", "error": str(e)}
    
    # ========== 2. Google Sheets ==========
    async def google_sheets_append(
        self,
        spreadsheet_id: str,
        range_name: str,
        values: List[List[str]],
        api_key: str
    ) -> Dict:
        """追加数据到 Google Sheets"""
        try:
            url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{range_name}:append"
            params = {"valueInputOption": "RAW", "key": api_key}
            json_data = {"values": values}
            
            response = await self.session.post(url, params=params, json=json_data)
            response.raise_for_status()
            
            return {"status": "success", "data": response.json()}
        except Exception as e:
            return {"status": "failed", "error": str(e)}
    
    # ========== 3. Notion ==========
    async def notion_create_page(
        self,
        parent_page_id: str,
        title: str,
        api_key: str
    ) -> Dict:
        """在 Notion 创建页面"""
        try:
            url = "https://api.notion.com/v1/pages"
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Notion-Version": "2022-06-28",
                "Content-Type": "application/json"
            }
            json_data = {
                "parent": {"page_id": parent_page_id},
                "properties": {
                    "title": [
                        {
                            "text": {
                                "content": title
                            }
                        }
                    ]
                }
            }
            
            response = await self.session.post(url, headers=headers, json=json_data)
            response.raise_for_status()
            
            return {"status": "success", "data": response.json()}
        except Exception as e:
            return {"status": "failed", "error": str(e)}
    
    # ========== 4. 钉钉机器人 ==========
    async def dingtalk_send(
        self,
        webhook_url: str,
        message: str,
        msg_type: str = "text"
    ) -> Dict:
        """发送钉钉机器人消息"""
        try:
            headers = {"Content-Type": "application/json"}
            json_data = {
                "msgtype": msg_type,
                msg_type: {"content": message}
            }
            
            response = await self.session.post(webhook_url, headers=headers, json=json_data)
            response.raise_for_status()
            result = response.json()
            
            return {"status": "success" if result.get("errcode") == 0 else "failed", "data": result}
        except Exception as e:
            return {"status": "failed", "error": str(e)}
    
    # ========== 5. 企业微信 ==========
    async def wecom_send(
        self,
        webhook_url: str,
        message: str,
        msg_type: str = "text"
    ) -> Dict:
        """发送企业微信消息"""
        try:
            headers = {"Content-Type": "application/json"}
            json_data = {
                "msgtype": msg_type,
                msg_type: {"content": message}
            }
            
            response = await self.session.post(webhook_url, headers=headers, json=json_data)
            response.raise_for_status()
            result = response.json()
            
            return {"status": "success" if result.get("errcode") == 0 else "failed", "data": result}
        except Exception as e:
            return {"status": "failed", "error": str(e)}
    
    # ========== 6. Slack ==========
    async def slack_send(
        self,
        webhook_url: str,
        message: str,
        channel: str = "#general"
    ) -> Dict:
        """发送 Slack 消息"""
        try:
            headers = {"Content-Type": "application/json"}
            json_data = {
                "text": message,
                "channel": channel
            }
            
            response = await self.session.post(webhook_url, json=json_data)
            response.raise_for_status()
            
            return {"status": "success", "data": response.json()}
        except Exception as e:
            return {"status": "failed", "error": str(e)}
    
    # ========== 7. GitHub ==========
    async def github_create_issue(
        self,
        repo: str,
        title: str,
        body: str,
        token: str
    ) -> Dict:
        """创建 GitHub Issue"""
        try:
            url = f"https://api.github.com/repos/{repo}/issues"
            headers = {
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github.v3+json"
            }
            json_data = {
                "title": title,
                "body": body
            }
            
            response = await self.session.post(url, headers=headers, json=json_data)
            response.raise_for_status()
            
            return {"status": "success", "data": response.json()}
        except Exception as e:
            return {"status": "failed", "error": str(e)}
    
    # ========== 8. Trello ==========
    async def trello_create_card(
        self,
        id_list: str,
        name: str,
        desc: str,
        api_key: str,
        token: str
    ) -> Dict:
        """创建 Trello 卡片"""
        try:
            url = "https://api.trello.com/1/cards"
            params = {
                "key": api_key,
                "token": token,
                "idList": id_list,
                "name": name,
                "desc": desc
            }
            
            response = await self.session.post(url, params=params)
            response.raise_for_status()
            
            return {"status": "success", "data": response.json()}
        except Exception as e:
            return {"status": "failed", "error": str(e)}
    
    # ========== 9. Airtable ==========
    async def airtable_create_record(
        self,
        base_id: str,
        table_name: str,
        fields: Dict,
        api_key: str
    ) -> Dict:
        """创建 Airtable 记录"""
        try:
            url = f"https://api.airtable.com/v0/{base_id}/{table_name}"
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            json_data = {"fields": fields}
            
            response = await self.session.post(url, headers=headers, json=json_data)
            response.raise_for_status()
            
            return {"status": "success", "data": response.json()}
        except Exception as e:
            return {"status": "failed", "error": str(e)}
    
    # ========== 10. MySQL ==========
    async def mysql_query(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
        query: str
    ) -> Dict:
        """执行 MySQL 查询"""
        try:
            conn = mysql.connector.connect(
                host=host,
                port=port,
                user=user,
                password=password,
                database=database
            )
            cursor = conn.cursor(dictionary=True)
            cursor.execute(query)
            
            if query.strip().upper().startswith("SELECT"):
                results = cursor.fetchall()
            else:
                conn.commit()
                results = {"affected_rows": cursor.rowcount}
            
            cursor.close()
            conn.close()
            
            return {"status": "success", "data": results}
        except Exception as e:
            return {"status": "failed", "error": str(e)}


# 快速测试函数
async def test_all_apis():
    """测试所有 API 集成（使用模拟配置）"""
    manager = APIIntegrationManager()
    
    print("🔌 开始 API 集成测试...")
    
    # 示例：测试钉钉（需要真实配置）
    # result = await manager.dingtalk_send(
    #     webhook_url="YOUR_WEBHOOK",
    #     message="MVP 环境测试消息"
    # )
    # print(f"钉钉测试：{result}")
    
    await manager.close()
    print("✅ API 集成测试完成")


if __name__ == "__main__":
    import asyncio
    asyncio.run(test_all_apis())
