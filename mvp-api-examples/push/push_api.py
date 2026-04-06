# 消息推送 API 示例（WebSocket/钉钉/企业微信）
# 用于 MVP 实时通知

import os
import json
from typing import Set
from datetime import datetime

class PushClient:
    """消息推送客户端"""
    
    def __init__(self, provider: str = 'websocket'):
        self.provider = provider
        self.clients: Set = set()
        
        if provider == 'dingtalk':
            self.webhook_url = os.getenv('DINGTALK_WEBHOOK', '')
            self.secret = os.getenv('DINGTALK_SECRET', '')
        elif provider == 'wecom':
            self.webhook_url = os.getenv('WECOM_WEBHOOK', '')
    
    def send_message(self, content: str, title: str = '通知') -> bool:
        if self.provider in ['dingtalk', 'wecom']:
            return self._send_webhook(content, title)
        return True
    
    def _send_webhook(self, content: str, title: str) -> bool:
        import requests
        import hmac, hashlib, base64, urllib.parse, time
        
        if 'dingtalk' in self.webhook_url:
            timestamp = str(round(time.time() * 1000))
            string_to_sign = f'{timestamp}\n{self.secret}'
            hmac_code = hmac.new(
                self.secret.encode('utf-8'),
                string_to_sign.encode('utf-8'),
                digestmod=hashlib.sha256
            ).digest()
            sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
            url = f'{self.webhook_url}&timestamp={timestamp}&sign={sign}'
            payload = {'msgtype': 'markdown', 'markdown': {'title': title, 'text': f'## {title}\n\n{content}'}}
        else:
            url = self.webhook_url
            payload = {'msgtype': 'markdown', 'markdown': {'content': f'## {title}\n\n{content}'}}
        
        try:
            response = requests.post(url, json=payload)
            return response.status_code == 200
        except Exception as e:
            print(f"Webhook Error: {e}")
            return False
    
    def send_workflow_notification(self, workflow_id: str, status: str) -> bool:
        content = f"**工作流 ID**: {workflow_id}\n**状态**: {status}"
        return self.send_message(content, "工作流状态更新")
    
    def send_user_notification(self, user_id: int, message: str) -> bool:
        content = f"**用户 ID**: {user_id}\n**消息**: {message}"
        return self.send_message(content, "系统通知")

if __name__ == '__main__':
    push = PushClient(provider='dingtalk')
    success = push.send_workflow_notification('workflow_001', 'completed')
    print(f"Push sent: {success}")
