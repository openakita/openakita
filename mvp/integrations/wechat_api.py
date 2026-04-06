"""
企业微信 API - 消息通知
支持发送文本、卡片、Markdown 等消息类型
"""

from typing import Dict, Any, List
import logging
from .base import BaseAPI, APIResponse, APIMode
import time
import hashlib
import hmac

logger = logging.getLogger(__name__)


class WeChatAPI(BaseAPI):
    """企业微信消息通知 API"""
    
    def __init__(self, mode: APIMode = APIMode.MOCK):
        super().__init__(mode)
        self.mock_sent_messages = []
    
    def _call_mock(self, **kwargs) -> APIResponse:
        """Mock 模式：模拟企业微信消息发送"""
        action = kwargs.get('action', 'send_text')
        
        try:
            if action == 'send_text':
                content = kwargs.get('content', '')
                to_users = kwargs.get('to_users', ['@all'])
                
                if not content:
                    return APIResponse(
                        success=False,
                        data=None,
                        error="消息内容不能为空",
                        status_code=400
                    )
                
                message_id = f'mock_wx_{int(time.time())}'
                self.mock_sent_messages.append({
                    'id': message_id,
                    'type': 'text',
                    'content': content,
                    'to': to_users,
                    'sent_at': time.strftime('%Y-%m-%d %H:%M:%S')
                })
                
                logger.info(f"[MOCK] 企业微信消息已发送：{content[:50]}...")
                return APIResponse(
                    success=True,
                    data={
                        'message_id': message_id,
                        'status': 'sent',
                        'recipients': to_users
                    }
                )
            
            elif action == 'send_markdown':
                markdown = kwargs.get('markdown', '')
                to_users = kwargs.get('to_users', ['@all'])
                
                message_id = f'mock_wx_md_{int(time.time())}'
                self.mock_sent_messages.append({
                    'id': message_id,
                    'type': 'markdown',
                    'content': markdown,
                    'to': to_users,
                    'sent_at': time.strftime('%Y-%m-%d %H:%M:%S')
                })
                
                return APIResponse(
                    success=True,
                    data={'message_id': message_id, 'status': 'sent'}
                )
            
            elif action == 'send_card':
                card_data = kwargs.get('card_data', {})
                to_users = kwargs.get('to_users', ['@all'])
                
                message_id = f'mock_wx_card_{int(time.time())}'
                self.mock_sent_messages.append({
                    'id': message_id,
                    'type': 'card',
                    'content': card_data,
                    'to': to_users,
                    'sent_at': time.strftime('%Y-%m-%d %H:%M:%S')
                })
                
                return APIResponse(
                    success=True,
                    data={'message_id': message_id, 'status': 'sent'}
                )
            
            else:
                return APIResponse(
                    success=False,
                    data=None,
                    error=f"未知操作：{action}",
                    status_code=400
                )
                
        except Exception as e:
            return APIResponse(success=False, data=None, error=str(e), status_code=500)
    
    def _call_real(self, **kwargs) -> APIResponse:
        """真实 API 调用 - 企业微信"""
        try:
            import requests
            
            action = kwargs.get('action', 'send_text')
            
            # 获取 access_token
            corp_id = self._config.get('WECHAT_CORP_ID')
            agent_secret = self._config.get('WECHAT_AGENT_SECRET')
            
            token_url = f"https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={corp_id}&corpsecret={agent_secret}"
            token_response = requests.get(token_url, timeout=10)
            token_data = token_response.json()
            
            if token_data.get('errcode') != 0:
                return APIResponse(
                    success=False,
                    data=None,
                    error=f"获取 token 失败：{token_data.get('errmsg')}",
                    status_code=401
                )
            
            access_token = token_data.get('access_token')
            
            if action == 'send_text':
                url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={access_token}"
                data = {
                    "touser": kwargs.get('to_user', '@all'),
                    "msgtype": "text",
                    "agentid": self._config.get('WECHAT_AGENT_ID'),
                    "text": {
                        "content": kwargs.get('content', '')
                    }
                }
                response = requests.post(url, json=data, timeout=10)
                result = response.json()
                
                if result.get('errcode') == 0:
                    return APIResponse(success=True, data={'message_id': result.get('message_id')})
                else:
                    return APIResponse(
                        success=False,
                        data=None,
                        error=f"发送失败：{result.get('errmsg')}",
                        status_code=400
                    )
            
            else:
                return APIResponse(
                    success=False,
                    data=None,
                    error=f"不支持的操作：{action}",
                    status_code=400
                )
                
        except Exception as e:
            return APIResponse(success=False, data=None, error=str(e), status_code=500)
    
    def send_text(self, content: str, to_user: str = '@all') -> APIResponse:
        """发送文本消息"""
        return self.call(action='send_text', content=content, to_user=to_user)
    
    def send_markdown(self, markdown: str, to_user: str = '@all') -> APIResponse:
        """发送 Markdown 消息"""
        return self.call(action='send_markdown', markdown=markdown, to_user=to_user)


def test_wechat_api():
    """企业微信 API 测试"""
    print("=" * 50)
    print("企业微信 API 测试")
    print("=" * 50)
    
    api = WeChatAPI(mode=APIMode.MOCK)
    
    print("\n[测试 1] 发送文本消息")
    result = api.send_text("【系统通知】MVP API 集成测试成功")
    print(f"结果：{'✅ 成功' if result.success else '❌ 失败'}")
    if result.success:
        print(f"消息 ID: {result.data.get('message_id')}")
    
    print("\n[测试 2] 发送 Markdown 消息")
    markdown = """## MVP 进度通知
- **任务**: API 集成验证
- **进度**: 80%
- **状态**: 🟢 正常
"""
    result = api.send_markdown(markdown)
    print(f"结果：{'✅ 成功' if result.success else '❌ 失败'}")
    
    print("\n[测试 3] 空消息测试")
    result = api.send_text("")
    print(f"结果：{'✅ 成功' if result.success else '❌ 失败'}")
    print(f"错误：{result.error}")
    
    print("\n" + "=" * 50)


if __name__ == "__main__":
    test_wechat_api()
