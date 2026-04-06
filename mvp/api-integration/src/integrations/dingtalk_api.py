"""
钉钉 API 集成
支持：消息通知、机器人 webhook、审批回调
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


class DingTalkConfig(APIConfig):
    """钉钉 API 配置"""
    corp_id: Optional[str] = None
    app_key: Optional[str] = None
    app_secret: Optional[str] = None
    agent_id: Optional[str] = None
    webhook_url: Optional[str] = None
    webhook_secret: Optional[str] = None
    access_token: Optional[str] = None
    token_expire_time: Optional[int] = None


class DingTalkAPI(BaseAPIIntegration):
    """钉钉 API 集成"""
    
    def __init__(self, config: DingTalkConfig):
        super().__init__(config)
        self.config: DingTalkConfig = config
        self.client: Optional[httpx.AsyncClient] = None
        self.base_url = "https://oapi.dingtalk.com"
    
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
        return ['corp_id', 'app_key', 'app_secret']
    
    async def execute(self, action: str, **kwargs) -> APIResponse:
        """
        执行钉钉 API 调用
        
        Actions:
            - send_message: 发送工作通知
                参数：user_ids, msg_type, content
            - send_robot: 发送机器人消息
                参数：msg_type, content, mentioned_users
            - get_user_info: 获取用户信息
                参数：userid
            - create_task: 创建待办任务
                参数：userid, task_content
        
        Returns:
            APIResponse
        """
        try:
            if action == "send_message":
                return await self._send_work_notification(**kwargs)
            elif action == "send_robot":
                return await self._send_robot_message(**kwargs)
            elif action == "get_user_info":
                return await self._get_user_info(**kwargs)
            elif action == "create_task":
                return await self._create_task(**kwargs)
            else:
                raise ValidationError(f"不支持的操作：{action}")
        except Exception as e:
            logger.error(f"钉钉 API 调用失败：{e}")
            return APIResponse(
                success=False,
                error=str(e),
                status_code=getattr(e, 'status_code', None)
            )
    
    async def _refresh_access_token(self) -> str:
        """刷新访问令牌"""
        url = f"{self.base_url}/gettoken"
        params = {
            "appkey": self.config.app_key,
            "appsecret": self.config.app_secret
        }
        
        response = await self.client.get(url, params=params)
        result = response.json()
        
        if result.get('errcode') != 0:
            raise AuthenticationError(f"获取 access_token 失败：{result.get('errmsg')}")
        
        self.config.access_token = result['access_token']
        self.config.token_expire_time = int(time.time()) + result['expires_in'] - 60
        
        logger.info("钉钉 access_token 刷新成功")
        return self.config.access_token
    
    async def _ensure_token_valid(self) -> None:
        """确保 token 有效"""
        if not self.config.access_token or \
           (self.config.token_expire_time and time.time() > self.config.token_expire_time):
            await self._refresh_access_token()
    
    async def _send_work_notification(
        self,
        user_ids: List[str],
        msg_type: str = "text",
        content: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> APIResponse:
        """发送工作通知"""
        await self._ensure_token_valid()
        
        url = f"{self.base_url}/topapi/message/corpconversation/asyncsend_v2"
        params = {"access_token": self.config.access_token}
        
        if msg_type == "text":
            msg_content = {"content": content.get("text", "") if isinstance(content, dict) else str(content)}
        elif msg_type == "markdown":
            msg_content = {"markdown": content}
        elif msg_type == "link":
            msg_content = content
        else:
            raise ValidationError(f"不支持的消息类型：{msg_type}")
        
        payload = {
            "agent_id": int(self.config.agent_id) if self.config.agent_id else None,
            "userid_list": ",".join(user_ids),
            "msgtype": msg_type,
            msg_type: msg_content
        }
        
        # 过滤 None 值
        payload = {k: v for k, v in payload.items() if v is not None}
        
        response = await self.client.post(url, params=params, json=payload)
        result = response.json()
        
        if result.get('errcode') != 0:
            raise ServiceUnavailableError(f"发送失败：{result.get('errmsg')}")
        
        return APIResponse(
            success=True,
            data={"task_id": result.get('task_id'), "recipients": len(user_ids)},
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
        
        # 添加签名（如果配置了 secret）
        webhook_url = self.config.webhook_url
        if self.config.webhook_secret:
            timestamp = str(round(time.time() * 1000))
            secret_enc = self.config.webhook_secret.encode('utf-8')
            string_to_sign = f'{timestamp}\n{self.config.webhook_secret}'
            string_to_sign_enc = string_to_sign.encode('utf-8')
            hmac_code = hmac.new(secret_enc, string_to_sign_enc, digestmod=hashlib.sha256).digest()
            sign = quote(base64.b64encode(hmac_code), safe='')
            webhook_url = f"{webhook_url}&timestamp={timestamp}&sign={sign}"
        
        payload = {"msgtype": msg_type}
        
        if msg_type == "text":
            text_content = {
                "content": content.get("text", "") if isinstance(content, dict) else str(content)
            }
            if mentioned_users:
                if "all" in mentioned_users:
                    text_content["at"] = {"isAtAll": True}
                else:
                    text_content["at"] = {"atUserIds": mentioned_users}
            payload["text"] = text_content
        elif msg_type == "markdown":
            payload["markdown"] = content
            if mentioned_users:
                payload["markdown"]["at"] = mentioned_users
        elif msg_type == "link":
            payload["link"] = content
        else:
            raise ValidationError(f"不支持的消息类型：{msg_type}")
        
        response = await self.client.post(webhook_url, json=payload)
        result = response.json()
        
        if result.get('errcode') != 0:
            raise ServiceUnavailableError(f"机器人消息发送失败：{result.get('errmsg')}")
        
        return APIResponse(
            success=True,
            data={"message_id": result.get('message_id')},
            status_code=200
        )
    
    async def _get_user_info(self, userid: str) -> APIResponse:
        """获取用户信息"""
        await self._ensure_token_valid()
        
        url = f"{self.base_url}/topapi/v2/user/get"
        params = {
            "access_token": self.config.access_token,
            "userid": userid
        }
        
        response = await self.client.get(url, params=params)
        result = response.json()
        
        if result.get('errcode') != 0:
            raise ServiceUnavailableError(f"获取用户信息失败：{result.get('errmsg')}")
        
        return APIResponse(
            success=True,
            data=result.get('result', {}),
            status_code=200
        )
    
    async def _create_task(
        self,
        userid: str,
        task_content: str,
        **kwargs
    ) -> APIResponse:
        """创建待办任务"""
        await self._ensure_token_valid()
        
        url = f"{self.base_url}/topapi/workrecord/add"
        params = {"access_token": self.config.access_token}
        
        payload = {
            "userid": userid,
            "create_time": int(time.time() * 1000),
            "title": task_content[:50],
            "content": task_content,
            "url": kwargs.get("url", "")
        }
        
        response = await self.client.post(url, params=params, json=payload)
        result = response.json()
        
        if result.get('errcode') != 0:
            raise ServiceUnavailableError(f"创建任务失败：{result.get('errmsg')}")
        
        return APIResponse(
            success=True,
            data={"record_id": result.get('record_id')},
            status_code=200
        )


# 工厂函数
def create_dingtalk_api(
    corp_id: Optional[str] = None,
    app_key: Optional[str] = None,
    app_secret: Optional[str] = None,
    agent_id: Optional[str] = None,
    webhook_url: Optional[str] = None,
    webhook_secret: Optional[str] = None,
    use_mock: bool = False
) -> DingTalkAPI:
    """创建钉钉 API 实例"""
    if use_mock:
        logger.info("使用 Mock 模式创建钉钉 API")
        config_dict = {
            "corp_id": corp_id or "mock_corp_id",
            "app_key": app_key or "mock_app_key",
            "app_secret": app_secret or "mock_app_secret",
            "agent_id": agent_id or "mock_agent_id",
            "webhook_url": webhook_url or "https://mock.dingtalk.com/webhook",
        }
    else:
        config_dict = {
            "corp_id": corp_id or config.get("DINGTALK_CORP_ID"),
            "app_key": config.get("DINGTALK_APP_KEY"),
            "app_secret": config.get("DINGTALK_APP_SECRET"),
            "agent_id": agent_id or config.get("DINGTALK_AGENT_ID"),
            "webhook_url": webhook_url or config.get("DINGTALK_WEBHOOK_URL"),
            "webhook_secret": config.get("DINGTALK_WEBHOOK_SECRET"),
        }
    
    return DingTalkAPI(DingTalkConfig(**config_dict))
