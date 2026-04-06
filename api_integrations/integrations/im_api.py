"""
即时通讯 API 集成 - Slack
"""
from typing import List, Dict, Any, Optional
from .base_client import BaseAPIClient, APIError


class SlackClient(BaseAPIClient):
    """Slack API 客户端"""
    
    def __init__(self, bot_token: str):
        super().__init__(
            base_url="https://slack.com/api",
            api_key=bot_token
        )
    
    def _get_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
    
    async def send_message(
        self,
        channel: str,
        text: str,
        blocks: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """发送消息"""
        payload = {
            "channel": channel,
            "text": text
        }
        if blocks:
            payload["blocks"] = blocks
        
        return await self.post("/chat.postMessage", json=payload)
    
    async def send_ephemeral(
        self,
        channel: str,
        user: str,
        text: str
    ) -> Dict[str, Any]:
        """发送临时消息（仅用户可见）"""
        return await self.post("/chat.postEphemeral", json={
            "channel": channel,
            "user": user,
            "text": text
        })
    
    async def get_channel_info(self, channel_id: str) -> Dict[str, Any]:
        """获取频道信息"""
        return await self.get(f"/conversations.info?channel={channel_id}")
    
    async def list_channels(self, types: str = "public_channel") -> List[Dict[str, Any]]:
        """列出频道"""
        response = await self.get(f"/conversations.list?types={types}")
        return response.get("channels", [])
    
    async def upload_file(
        self,
        channel: str,
        file_path: str,
        title: str,
        initial_comment: Optional[str] = None
    ) -> Dict[str, Any]:
        """上传文件"""
        # 注意：实际实现需要 multipart/form-data
        return await self.post("/files.upload", data={
            "channels": channel,
            "file": open(file_path, "rb"),
            "title": title,
            "initial_comment": initial_comment or ""
        })
    
    async def test_connection(self) -> bool:
        """测试连接"""
        try:
            response = await self.get("/auth.test")
            return response.get("ok", False)
        except APIError:
            return False


# 使用示例
async def example_slack():
    """Slack 使用示例"""
    from config import APIConfig
    
    async with SlackClient(APIConfig.SLACK_BOT_TOKEN) as client:
        # 发送消息
        await client.send_message(
            channel=APIConfig.SLACK_CHANNEL_ID,
            text="🚀 MVP 开发进度更新",
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "*MVP 开发进度*\n- API 集成：完成 10/10\n- 测试：进行中\n- 预计上线：06-01"
                    }
                }
            ]
        )
