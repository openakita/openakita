"""
Slack API 客户端
支持消息发送、频道管理、用户查询等功能
文档：https://api.slack.com/
"""
from typing import List, Optional, Dict, Any
from .base import BaseAPIClient, APIError
import structlog

logger = structlog.get_logger()


class SlackClient(BaseAPIClient):
    """Slack API 客户端"""
    
    def __init__(self, bot_token: str):
        super().__init__(
            base_url="https://slack.com/api",
            api_key=bot_token,
            timeout=30
        )
        self.bot_token = bot_token
    
    def _get_auth_header(self) -> str:
        """Slack 使用 Bearer Token 认证"""
        return f"Bearer {self.bot_token}"
    
    async def test_auth(self) -> bool:
        """测试认证是否有效"""
        try:
            response = await self.get("/auth.test")
            return response.get("ok", False)
        except APIError:
            return False
    
    async def send_message(
        self,
        channel: str,
        text: str,
        blocks: Optional[List[Dict]] = None,
        attachments: Optional[List[Dict]] = None,
        thread_ts: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        发送消息到 Slack 频道
        
        Args:
            channel: 频道 ID 或名称 (#general 或 C123456)
            text: 消息文本
            blocks: 消息块（用于富文本）
            attachments: 附件
            thread_ts: 回复的线程时间戳
            
        Returns:
            消息发送结果
        """
        data = {
            "channel": channel,
            "text": text,
        }
        
        if blocks:
            data["blocks"] = blocks
        if attachments:
            data["attachments"] = attachments
        if thread_ts:
            data["thread_ts"] = thread_ts
        
        response = await self.post("/chat.postMessage", json_data=data)
        
        if not response.get("ok"):
            raise APIError(f"发送失败：{response.get('error')}")
        
        logger.info("slack_message_sent", channel=channel, ts=response.get("ts"))
        return response
    
    async def get_channel_id(self, channel_name: str) -> Optional[str]:
        """获取频道 ID"""
        response = await self.get("/conversations.list")
        
        channels = response.get("channels", [])
        for channel in channels:
            if channel.get("name") == channel_name.lstrip("#"):
                return channel.get("id")
        
        return None
    
    async def send_notification(
        self,
        channel: str,
        title: str,
        message: str,
        level: str = "info"
    ) -> Dict[str, Any]:
        """
        发送通知消息（带颜色和图标）
        
        Args:
            channel: 频道
            title: 通知标题
            message: 通知内容
            level: 级别 (info/warning/error/success)
        """
        colors = {
            "info": "#36a64f",
            "warning": "#ff9800",
            "error": "#ff0000",
            "success": "#00c853"
        }
        
        emojis = {
            "info": "ℹ️",
            "warning": "⚠️",
            "error": "❌",
            "success": "✅"
        }
        
        attachments = [{
            "color": colors.get(level, "#36a64f"),
            "title": f"{emojis.get(level, 'ℹ️')} {title}",
            "text": message,
            "ts": int(__import__('time').time())
        }]
        
        return await self.send_message(
            channel=channel,
            text=f"*{title}*",
            attachments=attachments
        )
    
    async def get_users(self) -> List[Dict[str, Any]]:
        """获取所有用户列表"""
        response = await self.get("/users.list")
        return response.get("members", [])
    
    async def get_user_info(self, user_id: str) -> Dict[str, Any]:
        """获取用户信息"""
        response = await self.get(f"/users.info?user={user_id}")
        return response.get("user", {})
    
    async def create_channel(self, name: str, is_private: bool = False) -> Dict[str, Any]:
        """创建频道"""
        data = {
            "name": name,
            "is_private": is_private
        }
        response = await self.post("/conversations.create", json_data=data)
        return response.get("channel", {})
    
    async def invite_to_channel(
        self,
        channel_id: str,
        user_ids: List[str]
    ) -> Dict[str, Any]:
        """邀请用户加入频道"""
        data = {
            "channel": channel_id,
            "users": ",".join(user_ids)
        }
        response = await self.post("/conversations.invite", json_data=data)
        return response


# 使用示例
async def example_usage():
    """Slack API 使用示例"""
    import os
    from dotenv import load_dotenv
    
    load_dotenv()
    
    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        print("❌ 请设置 SLACK_BOT_TOKEN 环境变量")
        return
    
    async with SlackClient(token) as client:
        # 测试认证
        is_valid = await client.test_auth()
        print(f"✅ 认证有效：{is_valid}")
        
        # 发送消息
        await client.send_message(
            channel="#general",
            text="Hello from OpenAkita! 🚀"
        )
        
        # 发送通知
        await client.send_notification(
            channel="#general",
            title="系统通知",
            message="API 集成验证成功",
            level="success"
        )


if __name__ == "__main__":
    import asyncio
    asyncio.run(example_usage())
