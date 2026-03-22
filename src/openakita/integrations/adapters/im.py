"""
即时通讯 API 适配器
支持钉钉、企业微信、飞书
"""

import aiohttp
import hmac
import hashlib
import base64
from typing import Any, Dict, List, Optional
from datetime import datetime
from . import BaseAPIAdapter, APIError, AuthenticationError


class DingTalkAdapter(BaseAPIAdapter):
    """钉钉机器人适配器"""
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.webhook = config.get('webhook')
        self.secret = config.get('secret')
    
    async def authenticate(self) -> bool:
        return bool(self.webhook)
    
    async def call(self, endpoint: str, method: str = "GET", **kwargs) -> Dict[str, Any]:
        return await self.send_message(kwargs.get('content', {}))
    
    async def send_message(self, content: Dict) -> Dict[str, Any]:
        import time
        timestamp = str(round(time.time() * 1000))
        
        if self.secret:
            secret_enc = self.secret.encode('utf-8')
            string_to_sign = f'{timestamp}\n{self.secret}'
            string_to_sign_enc = string_to_sign.encode('utf-8')
            hmac_code = hmac.new(secret_enc, string_to_sign_enc, digestmod=hashlib.sha256).digest()
            sign = base64.b64encode(hmac_code).decode('utf-8')
            webhook = f"{self.webhook}&timestamp={timestamp}&sign={sign}"
        else:
            webhook = self.webhook
        
        async with aiohttp.ClientSession() as session:
            async with session.post(webhook, json=content) as response:
                return await response.json()
    
    async def send_text(self, content: str, at_mobiles: Optional[List[str]] = None):
        msg = {"msgtype": "text", "text": {"content": content}}
        if at_mobiles:
            msg["at"] = {"atMobiles": at_mobiles}
        return await self.send_message(msg)


class WeComAdapter(BaseAPIAdapter):
    """企业微信机器人适配器"""
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.webhook = config.get('webhook')
    
    async def authenticate(self) -> bool:
        return bool(self.webhook)
    
    async def call(self, endpoint: str, method: str = "GET", **kwargs) -> Dict[str, Any]:
        return await self.send_message(kwargs.get('content', {}))
    
    async def send_message(self, content: Dict) -> Dict[str, Any]:
        async with aiohttp.ClientSession() as session:
            async with session.post(self.webhook, json=content) as response:
                return await response.json()
    
    async def send_text(self, content: str, mentioned_list: Optional[List[str]] = None):
        msg = {"msgtype": "text", "text": {"content": content, "mentioned_list": mentioned_list or []}}
        return await self.send_message(msg)


class FeishuAdapter(BaseAPIAdapter):
    """飞书机器人适配器"""
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.webhook = config.get('webhook')
    
    async def authenticate(self) -> bool:
        return bool(self.webhook)
    
    async def call(self, endpoint: str, method: str = "GET", **kwargs) -> Dict[str, Any]:
        return await self.send_message(kwargs.get('content', {}))
    
    async def send_message(self, content: Dict) -> Dict[str, Any]:
        async with aiohttp.ClientSession() as session:
            async with session.post(self.webhook, json=content) as response:
                return await response.json()
    
    async def send_text(self, content: str):
        return await self.send_message({"msg_type": "text", "content": {"text": content}})


def create_im_adapter(provider: str, config: Dict[str, Any]) -> BaseAPIAdapter:
    providers = {'dingtalk': DingTalkAdapter, 'wecom': WeComAdapter, 'feishu': FeishuAdapter}
    if provider not in providers:
        raise ValueError(f"不支持的 IM 提供商：{provider}")
    return providers[provider](config)
