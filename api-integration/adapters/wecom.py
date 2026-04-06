"""
3. 企业微信 API - 消息推送
支持发送文本、卡片、文件等消息
"""

import requests
import json
from typing import List, Optional
from adapters.base import BaseAPIAdapter, APIResponse, APIStatus


class WeComAdapter(BaseAPIAdapter):
    """企业微信适配器"""
    
    def __init__(self, config: dict):
        """
        配置参数:
        - corp_id: 企业 ID
        - agent_id: 应用 ID
        - secret: 应用 Secret
        """
        super().__init__(config)
        self.token_url = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
        self.message_url = "https://qyapi.weixin.qq.com/cgi-bin/message/send"
        self._access_token = None
    
    def connect(self) -> bool:
        try:
            assert self.config.get('corp_id')
            assert self.config.get('agent_id')
            assert self.config.get('secret')
            self._access_token = self._get_access_token()
            self._initialized = self._access_token is not None
            return self._initialized
        except Exception as e:
            print(f"连接失败：{e}")
            return False
    
    def disconnect(self) -> None:
        self._access_token = None
        self._initialized = False
    
    def _get_access_token(self) -> Optional[str]:
        """获取访问令牌"""
        params = {
            'corpid': self.config['corp_id'],
            'corpsecret': self.config['secret']
        }
        response = requests.get(self.token_url, params=params, timeout=30)
        result = response.json()
        if result.get('errcode') == 0:
            return result.get('access_token')
        else:
            print(f"获取 token 失败：{result.get('errmsg')}")
            return None
    
    def _refresh_token_if_needed(self):
        """刷新 token"""
        if not self._access_token:
            self._access_token = self._get_access_token()
    
    def execute(self, action: str, params: dict) -> APIResponse:
        if action == "send_text":
            return self.send_text_message(params)
        elif action == "send_markdown":
            return self.send_markdown_message(params)
        elif action == "send_card":
            return self.send_card_message(params)
        else:
            return APIResponse(
                status=APIStatus.FAILED,
                error=f"未知操作：{action}"
            )
    
    def send_text_message(self, params: dict) -> APIResponse:
        """
        发送文本消息
        
        参数:
        - to_user: 接收者用户 ID (多个用 | 分隔)
        - to_party: 接收者部门 ID (多个用 | 分隔)
        - to_tag: 接收者标签 ID (多个用 | 分隔)
        - content: 消息内容
        """
        try:
            self._refresh_token_if_needed()
            
            payload = {
                "touser": params.get('to_user', '@all'),
                "toparty": params.get('to_party', ''),
                "totag": params.get('to_tag', ''),
                "msgtype": "text",
                "agentid": self.config['agent_id'],
                "text": {
                    "content": params['content']
                },
                "safe": 0
            }
            
            response = requests.post(
                f"{self.message_url}?access_token={self._access_token}",
                json=payload,
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
    
    def send_markdown_message(self, params: dict) -> APIResponse:
        """
        发送 Markdown 消息
        
        参数:
        - to_user: 接收者用户 ID
        - content: Markdown 内容
        """
        try:
            self._refresh_token_if_needed()
            
            payload = {
                "touser": params.get('to_user', '@all'),
                "msgtype": "markdown",
                "agentid": self.config['agent_id'],
                "markdown": {
                    "content": params['content']
                }
            }
            
            response = requests.post(
                f"{self.message_url}?access_token={self._access_token}",
                json=payload,
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
    
    def send_card_message(self, params: dict) -> APIResponse:
        """
        发送卡片消息
        
        参数:
        - to_user: 接收者用户 ID
        - title: 卡片标题
        - description: 卡片描述
        - url: 点击跳转链接
        - btn_txt: 按钮文字
        """
        try:
            self._refresh_token_if_needed()
            
            payload = {
                "touser": params.get('to_user', '@all'),
                "msgtype": "textcard",
                "agentid": self.config['agent_id'],
                "textcard": {
                    "title": params['title'],
                    "description": params['description'],
                    "url": params['url'],
                    "btntxt": params.get('btn_txt', '详情')
                }
            }
            
            response = requests.post(
                f"{self.message_url}?access_token={self._access_token}",
                json=payload,
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


# ============ 使用示例 ============
if __name__ == "__main__":
    config = {
        'corp_id': 'YOUR_CORP_ID',
        'agent_id': 1000001,
        'secret': 'YOUR_SECRET'
    }
    
    wecom = WeComAdapter(config)
    
    if wecom.connect():
        print("✅ 企业微信连接成功")
        
        # 发送文本消息
        response = wecom.execute('send_text', {
            'to_user': 'user1|user2',
            'content': '【系统通知】您的订单已处理完成'
        })
        
        if response.is_success():
            print(f"✅ 消息发送成功")
        else:
            print(f"❌ 消息发送失败：{response.error}")
    else:
        print("❌ 企业微信连接失败")
