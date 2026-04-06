"""
消息推送 API 集成示例
支持钉钉、企业微信、飞书
"""

import requests
import hmac
import hashlib
import base64
import time
from typing import List, Optional, Dict
from urllib.parse import quote_plus


class DingTalkAPI:
    """钉钉机器人消息推送 API"""
    
    def __init__(self, webhook: str, secret: Optional[str] = None):
        self.webhook = webhook
        self.secret = secret
    
    def _generate_sign(self) -> str:
        """生成钉钉签名"""
        if not self.secret:
            return ""
        
        timestamp = str(round(time.time() * 1000))
        secret_enc = self.secret.encode('utf-8')
        string_to_sign = f'{timestamp}\n{self.secret}'
        string_to_sign_enc = string_to_sign.encode('utf-8')
        
        hmac_code = hmac.new(secret_enc, string_to_sign_enc, digestmod=hashlib.sha256).digest()
        sign = quote_plus(base64.b64encode(hmac_code))
        
        return f"&timestamp={timestamp}&sign={sign}"
    
    def send_text(self, content: str, mentioned_all: bool = False) -> bool:
        """
        发送文本消息
        
        Args:
            content: 消息内容
            mentioned_all: 是否@所有人
            
        Returns:
            bool: 发送是否成功
        """
        url = self.webhook
        if self.secret:
            url += self._generate_sign()
        
        data = {
            "msgtype": "text",
            "text": {
                "content": content
            },
            "at": {
                "isAtAll": mentioned_all
            }
        }
        
        try:
            response = requests.post(url, json=data)
            result = response.json()
            
            if result.get('errcode') == 0:
                print(f"✓ 钉钉消息发送成功")
                return True
            else:
                print(f"✗ 钉钉消息发送失败：{result}")
                return False
                
        except Exception as e:
            print(f"✗ 钉钉请求异常：{e}")
            return False
    
    def send_markdown(self, title: str, text: str) -> bool:
        """发送 Markdown 消息"""
        url = self.webhook
        if self.secret:
            url += self._generate_sign()
        
        data = {
            "msgtype": "markdown",
            "markdown": {
                "title": title,
                "text": text
            }
        }
        
        try:
            response = requests.post(url, json=data)
            result = response.json()
            
            if result.get('errcode') == 0:
                print(f"✓ 钉钉 Markdown 消息发送成功")
                return True
            else:
                print(f"✗ 钉钉消息发送失败：{result}")
                return False
                
        except Exception as e:
            print(f"✗ 钉钉请求异常：{e}")
            return False


class WeComAPI:
    """企业微信机器人消息推送 API"""
    
    def __init__(self, webhook: str):
        self.webhook = webhook
    
    def send_text(self, content: str, mentioned_list: Optional[List[str]] = None) -> bool:
        """
        发送文本消息
        
        Args:
            content: 消息内容
            mentioned_list: 需要@的用户列表（['user1', 'user2'] 或 ['@all']）
            
        Returns:
            bool: 发送是否成功
        """
        data = {
            "msgtype": "text",
            "text": {
                "content": content,
                "mentioned_list": mentioned_list or []
            }
        }
        
        try:
            response = requests.post(self.webhook, json=data)
            result = response.json()
            
            if result.get('errcode') == 0:
                print(f"✓ 企业微信消息发送成功")
                return True
            else:
                print(f"✗ 企业微信消息发送失败：{result}")
                return False
                
        except Exception as e:
            print(f"✗ 企业微信请求异常：{e}")
            return False
    
    def send_markdown(self, content: str) -> bool:
        """发送 Markdown 消息"""
        data = {
            "msgtype": "markdown",
            "markdown": {
                "content": content
            }
        }
        
        try:
            response = requests.post(self.webhook, json=data)
            result = response.json()
            
            if result.get('errcode') == 0:
                print(f"✓ 企业微信 Markdown 消息发送成功")
                return True
            else:
                print(f"✗ 企业微信消息发送失败：{result}")
                return False
                
        except Exception as e:
            print(f"✗ 企业微信请求异常：{e}")
            return False


class FeishuAPI:
    """飞书机器人消息推送 API"""
    
    def __init__(self, webhook: str, secret: Optional[str] = None):
        self.webhook = webhook
        self.secret = secret
    
    def _generate_sign(self, timestamp: str) -> str:
        """生成飞书签名"""
        if not self.secret:
            return ""
        
        string_to_sign = f'{timestamp}\n{self.secret}'
        hmac_code = hmac.new(
            string_to_sign.encode('utf-8'),
            digestmod=hashlib.sha256
        ).digest()
        
        return base64.b64encode(hmac_code).decode('utf-8')
    
    def send_text(self, content: str) -> bool:
        """发送文本消息"""
        timestamp = str(int(time.time()))
        
        data = {
            "msg_type": "text",
            "content": {
                "text": content
            }
        }
        
        if self.secret:
            data["sign"] = self._generate_sign(timestamp)
            data["timestamp"] = timestamp
        
        try:
            response = requests.post(self.webhook, json=data)
            result = response.json()
            
            if result.get('StatusCode') == 0 or result.get('code') == 0:
                print(f"✓ 飞书消息发送成功")
                return True
            else:
                print(f"✗ 飞书消息发送失败：{result}")
                return False
                
        except Exception as e:
            print(f"✗ 飞书请求异常：{e}")
            return False
    
    def send_post(self, title: str, content: List[List[Dict]]) -> bool:
        """
        发送富文本消息（Post）
        
        Args:
            title: 消息标题
            content: 消息内容（二维数组，支持 text/link/image 等类型）
            
        Example:
            content = [
                [{"tag": "text", "text": "项目进度更新"}],
                [{"tag": "text", "text": "完成度："}, {"tag": "text", "text": "80%", "style": ["bold"]}]
            ]
        """
        timestamp = str(int(time.time()))
        
        data = {
            "msg_type": "post",
            "content": {
                "post": {
                    "zh_cn": {
                        "title": title,
                        "content": content
                    }
                }
            }
        }
        
        if self.secret:
            data["sign"] = self._generate_sign(timestamp)
            data["timestamp"] = timestamp
        
        try:
            response = requests.post(self.webhook, json=data)
            result = response.json()
            
            if result.get('StatusCode') == 0 or result.get('code') == 0:
                print(f"✓ 飞书富文本消息发送成功")
                return True
            else:
                print(f"✗ 飞书消息发送失败：{result}")
                return False
                
        except Exception as e:
            print(f"✗ 飞书请求异常：{e}")
            return False


# 使用示例
if __name__ == "__main__":
    # 钉钉
    dingtalk = DingTalkAPI(
        webhook="https://oapi.dingtalk.com/robot/send?access_token=YOUR_TOKEN",
        secret="YOUR_SECRET"
    )
    dingtalk.send_text("⚠️ 系统告警：服务器 CPU 使用率超过 90%")
    dingtalk.send_markdown(
        title="项目进度更新",
        text="## 项目进度\n- 前端：80%\n- 后端：60%\n- 测试：40%"
    )
    
    # 企业微信
    wecom = WeComAPI(
        webhook="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=YOUR_KEY"
    )
    wecom.send_text("✅ 任务完成：数据备份成功", mentioned_list=["@all"])
    
    # 飞书
    feishu = FeishuAPI(
        webhook="https://open.feishu.cn/open-apis/bot/v2/hook/YOUR_TOKEN",
        secret="YOUR_SECRET"
    )
    feishu.send_text("📅 会议提醒：下午 3 点产品评审会")
