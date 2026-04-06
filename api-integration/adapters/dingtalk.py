"""
4. 钉钉 API - 机器人 Webhook
支持发送文本、Markdown、链接等消息
"""

import requests
import json
import hmac
import hashlib
import base64
import urllib.parse
import time
from typing import List, Optional
from adapters.base import BaseAPIAdapter, APIResponse, APIStatus


class DingTalkRobotAdapter(BaseAPIAdapter):
    """钉钉机器人适配器"""
    
    def __init__(self, config: dict):
        """
        配置参数:
        - webhook: 机器人 Webhook URL
        - secret: 加签密钥 (可选，如启用安全设置)
        """
        super().__init__(config)
        self.webhook = config.get('webhook')
        self.secret = config.get('secret')
    
    def connect(self) -> bool:
        try:
            assert self.webhook
            self._initialized = True
            return True
        except Exception as e:
            print(f"连接失败：{e}")
            return False
    
    def disconnect(self) -> None:
        self._initialized = False
    
    def _generate_sign(self) -> str:
        """生成加签"""
        if not self.secret:
            return ""
        
        timestamp = str(round(time.time() * 1000))
        secret_enc = self.secret.encode('utf-8')
        string_to_sign = f'{timestamp}\n{self.secret}'
        string_to_sign_enc = string_to_sign.encode('utf-8')
        
        hmac_code = hmac.new(
            secret_enc,
            string_to_sign_enc,
            digestmod=hashlib.sha256
        ).digest()
        
        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
        return f"&timestamp={timestamp}&sign={sign}"
    
    def execute(self, action: str, params: dict) -> APIResponse:
        if action == "send_text":
            return self.send_text(params)
        elif action == "send_markdown":
            return self.send_markdown(params)
        elif action == "send_link":
            return self.send_link(params)
        elif action == "send_action_card":
            return self.send_action_card(params)
        else:
            return APIResponse(
                status=APIStatus.FAILED,
                error=f"未知操作：{action}"
            )
    
    def _send_request(self, payload: dict) -> APIResponse:
        """发送请求"""
        try:
            url = self.webhook
            if self.secret:
                url += self._generate_sign()
            
            response = requests.post(
                url,
                json=payload,
                headers={'Content-Type': 'application/json'},
                timeout=30
            )
            
            result = response.json()
            if result.get('errcode') == 0:
                return APIResponse(
                    status=APIStatus.SUCCESS,
                    data=result,
                    status_code=200
                )
            else:
                return APIResponse(
                    status=APIStatus.FAILED,
                    error=result.get('errmsg', '发送失败'),
                    status_code=200
                )
        except Exception as e:
            return APIResponse(
                status=APIStatus.FAILED,
                error=str(e)
            )
    
    def send_text(self, params: dict) -> APIResponse:
        """
        发送文本消息
        
        参数:
        - content: 消息内容
        - at_mobiles: 需要@的手机号列表
        - at_all: 是否@所有人
        """
        payload = {
            "msgtype": "text",
            "text": {
                "content": params['content']
            },
            "at": {
                "atMobiles": params.get('at_mobiles', []),
                "isAtAll": params.get('at_all', False)
            }
        }
        return self._send_request(payload)
    
    def send_markdown(self, params: dict) -> APIResponse:
        """
        发送 Markdown 消息
        
        参数:
        - title: 消息标题
        - text: Markdown 内容
        - at_mobiles: 需要@的手机号列表
        - at_all: 是否@所有人
        """
        payload = {
            "msgtype": "markdown",
            "markdown": {
                "title": params['title'],
                "text": params['text']
            },
            "at": {
                "atMobiles": params.get('at_mobiles', []),
                "isAtAll": params.get('at_all', False)
            }
        }
        return self._send_request(payload)
    
    def send_link(self, params: dict) -> APIResponse:
        """
        发送链接消息
        
        参数:
        - title: 消息标题
        - text: 消息内容
        - message_url: 点击跳转链接
        - pic_url: 图片 URL
        """
        payload = {
            "msgtype": "link",
            "link": {
                "title": params['title'],
                "text": params['text'],
                "messageUrl": params['message_url'],
                "picUrl": params.get('pic_url', '')
            }
        }
        return self._send_request(payload)
    
    def send_action_card(self, params: dict) -> APIResponse:
        """
        发送行动卡片消息
        
        参数:
        - title: 标题
        - text: 内容
        - btn_orientation: 按钮方向 (0: 竖直，1: 水平)
        - buttons: 按钮列表 [{"title": "按钮 1", "action_url": "url1"}]
        """
        payload = {
            "msgtype": "actionCard",
            "actionCard": {
                "title": params['title'],
                "text": params['text'],
                "btnOrientation": str(params.get('btn_orientation', 0)),
                "btns": params.get('buttons', [])
            }
        }
        return self._send_request(payload)


# ============ 使用示例 ============
if __name__ == "__main__":
    config = {
        'webhook': 'https://oapi.dingtalk.com/robot/send?access_token=YOUR_TOKEN',
        'secret': 'YOUR_SECRET'  # 如启用加签
    }
    
    dingtalk = DingTalkRobotAdapter(config)
    
    if dingtalk.connect():
        print("✅ 钉钉机器人连接成功")
        
        # 发送 Markdown 消息
        response = dingtalk.execute('send_markdown', {
            'title': '项目进度通知',
            'text': '## 项目进度更新\n\n- ✅ 需求分析已完成\n- 🔄 开发进行中\n- ⏳ 测试待开始\n\n> 请相关人员注意时间节点',
            'at_all': False
        })
        
        if response.is_success():
            print(f"✅ 消息发送成功")
        else:
            print(f"❌ 消息发送失败：{response.error}")
    else:
        print("❌ 钉钉机器人连接失败")
