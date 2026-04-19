"""
Instant Messaging API adapters.
Supports DingTalk, WeCom (Enterprise WeChat), and Feishu (Lark).
"""

import base64
import hashlib
import hmac
from typing import Any

import aiohttp

from . import BaseAPIAdapter


class DingTalkAdapter(BaseAPIAdapter):
    """DingTalk bot adapter."""

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.webhook = config.get("webhook")
        self.secret = config.get("secret")

    async def authenticate(self) -> bool:
        return bool(self.webhook)

    async def call(self, endpoint: str, method: str = "GET", **kwargs) -> dict[str, Any]:
        return await self.send_message(kwargs.get("content", {}))

    async def send_message(self, content: dict) -> dict[str, Any]:
        import time

        timestamp = str(round(time.time() * 1000))

        if self.secret:
            secret_enc = self.secret.encode("utf-8")
            string_to_sign = f"{timestamp}\n{self.secret}"
            string_to_sign_enc = string_to_sign.encode("utf-8")
            hmac_code = hmac.new(secret_enc, string_to_sign_enc, digestmod=hashlib.sha256).digest()
            sign = base64.b64encode(hmac_code).decode("utf-8")
            webhook = f"{self.webhook}&timestamp={timestamp}&sign={sign}"
        else:
            webhook = self.webhook

        async with aiohttp.ClientSession() as session:
            async with session.post(webhook, json=content) as response:
                return await response.json()

    async def send_text(self, content: str, at_mobiles: list[str] | None = None):
        msg = {"msgtype": "text", "text": {"content": content}}
        if at_mobiles:
            msg["at"] = {"atMobiles": at_mobiles}
        return await self.send_message(msg)


class WeComAdapter(BaseAPIAdapter):
    """WeCom (Enterprise WeChat) bot adapter."""

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.webhook = config.get("webhook")

    async def authenticate(self) -> bool:
        return bool(self.webhook)

    async def call(self, endpoint: str, method: str = "GET", **kwargs) -> dict[str, Any]:
        return await self.send_message(kwargs.get("content", {}))

    async def send_message(self, content: dict) -> dict[str, Any]:
        async with aiohttp.ClientSession() as session:
            async with session.post(self.webhook, json=content) as response:
                return await response.json()

    async def send_text(self, content: str, mentioned_list: list[str] | None = None):
        msg = {
            "msgtype": "text",
            "text": {"content": content, "mentioned_list": mentioned_list or []},
        }
        return await self.send_message(msg)


class FeishuAdapter(BaseAPIAdapter):
    """Feishu (Lark) bot adapter."""

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.webhook = config.get("webhook")

    async def authenticate(self) -> bool:
        return bool(self.webhook)

    async def call(self, endpoint: str, method: str = "GET", **kwargs) -> dict[str, Any]:
        return await self.send_message(kwargs.get("content", {}))

    async def send_message(self, content: dict) -> dict[str, Any]:
        async with aiohttp.ClientSession() as session:
            async with session.post(self.webhook, json=content) as response:
                return await response.json()

    async def send_text(self, content: str):
        return await self.send_message({"msg_type": "text", "content": {"text": content}})


def create_im_adapter(provider: str, config: dict[str, Any]) -> BaseAPIAdapter:
    providers = {"dingtalk": DingTalkAdapter, "wecom": WeComAdapter, "feishu": FeishuAdapter}
    if provider not in providers:
        raise ValueError(f"Unsupported IM provider: {provider}")
    return providers[provider](config)
