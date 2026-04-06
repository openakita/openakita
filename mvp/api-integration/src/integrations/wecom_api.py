"""
企业微信 API 集成
支持：消息通知、机器人 webhook、应用消息
"""
import httpx
import hmac
import hashlib
import base64
import time
from typing import Optional, Dict, Any, List
from urllib.parse import quote

from ..core.base import BaseAPIIntegration, APIConfig, APIResponse
from ..core.exceptions import AuthenticationError, ValidationError, ServiceUnavailableError
from ..core.config import config

import logging
logger = logging.getLogger(__name__)


class WeComConfig(APIConfig):
    """企业微信 API 配置"""
    corp_id: Optional[str] = None
    agent_id: Optional[str] = None
    secret: Optional[str] = None
    webhook_url: Optional[str] = None
    access_token: Optional[str] = None
    token_expire_time: Optional[int] = None


class WeComAPI(BaseAPIIntegration):
    """企业微信 API 集成"""
    
    def __init__(self, config: WeComConfig):
        super().__init__(config)
        self.config: WeComConfig = config
        self.client: Optional[httpx.AsyncClient] = None
        self.base_url = "https://qyapi.weixin.qq.com/cgi-bin"
    
    async def initialize(self) -> None:
        """初始化"""
        self._validate_config()
        self.client = httpx.AsyncClient(timeout=self.config.timeout)
        
        # 获取访问令牌
        if not self.config.access_token:
            await self._refresh_access_token()
    
    async def close(self) -> None:
        """关闭连接"""
        if self.client:
            await self.client.aclose()
    
    def get_required_fields(self) -> list:
        """获取必需配置字段"""
        if self.config.webhook_url:
            return ['webhook_url']
        return ['corp_id', 'agent_id', 'secret']
    
    async def execute(self, action: str, **kwargs) -> APIResponse:
        """
        执行企业微信 API 调用
        
        Actions:
            - send_message: 发送应用消息
                参数：user_ids, msg_type, content
            - send_robot: 发送机器人消息
                参数：msg_type, content, mentioned_users
            - get_user_info: 获取用户信息
                参数：userid
            - send_to_chat: 发送群聊消息
                参数：chat_id, msg_type, content
        
        Returns:
            APIResponse
        """
        try:
            if action == "send_message":
                return await self._send_app_message(**kwargs)
            elif action == "send_robot":
                return await self._send_robot_message(**kwargs)
            elif action == "get_user_info":
                return await self._get_user_info(**kwargs)
            elif action == "send_to_chat":
                return await self._send_chat_message(**kwargs)
            else:
                raise ValidationError(f"不支持的操作：{action}")
        except Exception as e:
            logger.error(f"企业微信 API 调用失败：{e}")
            return APIResponse(
                success=False,
                error=str(e),
                status_code=getattr(e, 'status_code', None)
            )
    
    async def _refresh_access_token(self) -> str:
        """刷新访问令牌"""
        url = f"{self.base_url}/gettoken"
        params = {
            "corpid": self.config.corp_id,
            "corpsecret": self.config.secret
        }
        
        response = await self.client.get(url, params=params)
        result = response.json()
        
        if result.get('errcode') != 0:
            raise AuthenticationError(f"获取 access_token 失败：{result.get('errmsg')}")
        
        self.config.access_token = result['access_token']
        self.config.token_expire_time = int(time.time()) + result['expires_in'] - 60
        
        logger.info("企业微信 access_token 刷新成功")
        return self.config.access_token
    
    async def _ensure_token_valid(self) -> None:
        """确保 token 有效"""
        if not self.config.access_token or \
           (self.config.token_expire_time and time.time() > self.config.token_expire_time):
            await self._refresh_access_token()
    
    async def _send_app_message(
        self,
        user_ids: List[str],
        msg_type: str = "text",
        content: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> APIResponse:
        """发送应用消息"""
        await self._ensure_token_valid()
        
        url = f"{self.base_url}/message/send?access_token={self.config.access_token}"
        
        if msg_type == "text":
            msg_content = {"content": content.get("text", "") if isinstance(content, dict) else str(content)}
        elif msg_type == "markdown":
            msg_content = {"content": content.get("content", "") if isinstance(content, dict) else str(content)}
        elif msg_type == "textcard":
            msg_content = content
        else:
            raise ValidationError(f"不支持的消息类型：{msg_type}")
        
        payload = {
            "touser": "|".join(user_ids),
            "msgtype": msg_type,
            "agentid": int(self.config.agent_id) if self.config.agent_id else None,
            msg_type: msg_content,
            "safe": 0
        }
        
        # 过滤 None 值
        payload = {k: v for k, v in payload.items() if v is not None}
        
        response = await self.client.post(url, json=payload)
        result = response.json()
        
        if result.get('errcode') != 0:
            raise ServiceUnavailableError(f"发送失败：{result.get('errmsg')}")
        
        return APIResponse(
            success=True,
            data={"message_id": result.get('message_id'), "recipients": len(user_ids)},
            status_code=200
        )
    
    async def _send_robot_message(
        self,
        msg_type: str = "text",
        content: Optional[Dict[str, Any]] = None,
        mentioned_users: Optional[List[str]] = None,
        **kwargs
    ) -> APIResponse:
        """发送机器人消息（webhook 方式）"""
        if not self.config.webhook_url:
            raise ValidationError("webhook_url 未配置")
        
        payload = {"msgtype": msg_type}
        
        if msg_type == "text":
            text_content = {
                "content": content.get("content", "") if isinstance(content, dict) else str(content)
            }
            if mentioned_users:
                if "all" in mentioned_users:
                    text_content["mentioned_list"] = ["@all"]
                else:
                    text_content["mentioned_list"] = mentioned_users
            payload["text"] = text_content
        elif msg_type == "markdown":
            markdown_content = {
                "content": content.get("content", "") if isinstance(content, dict) else str(content)
            }
            if mentioned_users:
                if "all" in mentioned_users:
                    markdown_content["mentioned_list"] = ["@all"]
                else:
                    markdown_content["mentioned_list"] = mentioned_users
            payload["markdown"] = markdown_content
        elif msg_type == "news":
            payload["news"] = content
        else:
            raise ValidationError(f"不支持的消息类型：{msg_type}")
        
        response = await self.client.post(self.config.webhook_url, json=payload)
        result = response.json()
        
        if result.get('errcode') != 0:
            raise ServiceUnavailableError(f"机器人消息发送失败：{result.get('errmsg')}")
        
        return APIResponse(
            success=True,
            data={},
            status_code=200
        )
    
    async def _get_user_info(self, userid: str) -> APIResponse:
        """获取用户信息"""
        await self._ensure_token_valid()
        
        url = f"{self.base_url}/user/get?access_token={self.config.access_token}&userid={userid}"
        
        response = await self.client.get(url)
        result = response.json()
        
        if result.get('errcode') != 0:
            raise ServiceUnavailableError(f"获取用户信息失败：{result.get('errmsg')}")
        
        return APIResponse(
            success=True,
            data=result,
            status_code=200
        )
    
    async def _send_chat_message(
        self,
        chat_id: str,
        msg_type: str = "text",
        content: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> APIResponse:
        """发送群聊消息"""
        await self._ensure_token_valid()
        
        url = f"{self.base_url}/appchat/send?access_token={self.config.access_token}"
        
        if msg_type == "text":
            msg_content = {"content": content.get("content", "") if isinstance(content, dict) else str(content)}
        elif msg_type == "markdown":
            msg_content = {"content": content.get("content", "") if isinstance(content, dict) else str(content)}
        else:
            raise ValidationError(f"不支持的消息类型：{msg_type}")
        
        payload = {
            "chatid": chat_id,
            "msgtype": msg_type,
            msg_type: msg_content
        }
        
        response = await self.client.post(url, json=payload)
        result = response.json()
        
        if result.get('errcode') != 0:
            raise ServiceUnavailableError(f"群聊消息发送失败：{result.get('errmsg')}")
        
        return APIResponse(
            success=True,
            data={},
            status_code=200
        )


# 工厂函数
def create_wecom_api(
    corp_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    secret: Optional[str] = None,
    webhook_url: Optional[str] = None,
    use_mock: bool = False
) -> WeComAPI:
    """创建企业微信 API 实例"""
    if use_mock:
        logger.info("使用 Mock 模式创建企业微信 API")
        config_dict = {
            "corp_id": corp_id or "mock_corp_id",
            "agent_id": agent_id or "mock_agent_id",
            "secret": secret or "mock_secret",
            "webhook_url": webhook_url or "https://mock.wecom.com/webhook",
        }
    else:
        config_dict = {
            "corp_id": corp_id or config.get("WECHAT_WORK_CORP_ID"),
            "agent_id": agent_id or config.get("WECHAT_WORK_AGENT_ID"),
            "secret": secret or config.get("WECHAT_WORK_SECRET"),
            "webhook_url": webhook_url or config.get("WECHAT_WORK_WEBHOOK_URL"),
        }
    
    return WeComAPI(WeComConfig(**config_dict))
