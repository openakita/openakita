"""
OneBot 适配器

基于 OneBot v11 协议实现，可对接任何兼容 OneBot 的实现:
- NapCat, Lagrange, go-cqhttp 等
- WebSocket 正向连接
- 文本/图片/语音/文件收发
"""

import asyncio
import contextlib
import json
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..base import ChannelAdapter
from ..types import (
    MediaFile,
    MediaStatus,
    MessageContent,
    OutgoingMessage,
    UnifiedMessage,
)

logger = logging.getLogger(__name__)

# 延迟导入
websockets = None


def _import_websockets():
    global websockets
    if websockets is None:
        try:
            import websockets as ws

            websockets = ws
        except ImportError:
            from openakita.tools._import_helper import import_or_hint
            raise ImportError(import_or_hint("websockets"))


@dataclass
class OneBotConfig:
    """OneBot 配置"""

    ws_url: str = "ws://127.0.0.1:8080"
    access_token: str | None = None


class OneBotAdapter(ChannelAdapter):
    """
    OneBot 适配器 (OneBot v11 协议)

    通用机器人协议适配器，可对接任何兼容 OneBot v11 的实现。

    支持:
    - WebSocket 正向连接
    - 文本/图片/语音/文件消息
    - 群聊/私聊
    """

    channel_name = "onebot"

    def __init__(
        self,
        ws_url: str = "ws://127.0.0.1:8080",
        access_token: str | None = None,
        media_dir: Path | None = None,
    ):
        """
        Args:
            ws_url: OneBot WebSocket 地址
            access_token: 访问令牌（可选）
            media_dir: 媒体文件存储目录
        """
        super().__init__()

        self.config = OneBotConfig(
            ws_url=ws_url,
            access_token=access_token,
        )
        self.media_dir = Path(media_dir) if media_dir else Path("data/media/onebot")
        self.media_dir.mkdir(parents=True, exist_ok=True)

        self._ws: Any | None = None
        self._api_callbacks: dict[str, asyncio.Future] = {}
        self._receive_task: asyncio.Task | None = None

    async def start(self) -> None:
        """启动 OneBot 客户端"""
        _import_websockets()

        self._running = True

        # 启动带自动重连的消息接收循环
        self._receive_task = asyncio.create_task(self._receive_loop_with_reconnect())

        logger.info(f"OneBot adapter starting, will connect to {self.config.ws_url}")

    async def _connect_ws(self) -> bool:
        """建立 WebSocket 连接，成功返回 True"""
        headers = {}
        if self.config.access_token:
            headers["Authorization"] = f"Bearer {self.config.access_token}"

        try:
            self._ws = await websockets.connect(
                self.config.ws_url,
                additional_headers=headers,
            )
            logger.info(f"OneBot adapter connected to {self.config.ws_url}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to OneBot: {e}")
            return False

    async def stop(self) -> None:
        """停止 OneBot 客户端"""
        self._running = False

        if self._receive_task:
            self._receive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._receive_task

        if self._ws:
            await self._ws.close()

        logger.info("OneBot adapter stopped")

    async def _receive_loop_with_reconnect(self) -> None:
        """带自动重连的 WebSocket 消息接收循环"""
        retry_delay = 1  # 初始重试延迟（秒）
        max_delay = 60  # 最大重试延迟

        while self._running:
            if not await self._connect_ws():
                logger.warning(
                    f"OneBot adapter: reconnect in {retry_delay}s..."
                )
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, max_delay)
                continue

            # 连接成功，重置延迟
            retry_delay = 1

            try:
                async for message in self._ws:
                    try:
                        data = json.loads(message)
                        await self._handle_event(data)
                    except json.JSONDecodeError:
                        logger.warning(f"Invalid JSON: {message}")
                    except Exception as e:
                        logger.error(f"Error handling event: {e}")
            except websockets.ConnectionClosed:
                logger.warning("OneBot WebSocket connection closed, will reconnect...")
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error(f"OneBot WebSocket error: {e}")

            if self._running:
                logger.info(f"OneBot adapter: reconnecting in {retry_delay}s...")
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, max_delay)

    async def _handle_event(self, data: dict) -> None:
        """处理 OneBot 事件"""
        # API 响应
        if "echo" in data:
            echo = data["echo"]
            if echo in self._api_callbacks:
                future = self._api_callbacks.pop(echo)
                if data.get("status") == "ok":
                    future.set_result(data.get("data"))
                else:
                    future.set_exception(RuntimeError(data.get("message", "API call failed")))
            return

        # 事件
        post_type = data.get("post_type")

        if post_type == "message":
            await self._handle_message_event(data)
        elif post_type == "notice":
            await self._emit_event("notice", data)
        elif post_type == "request":
            await self._emit_event("request", data)

    async def _handle_message_event(self, data: dict) -> None:
        """处理消息事件"""
        message_type = data.get("message_type")

        # 解析消息内容
        raw_message = data.get("message", [])
        if isinstance(raw_message, str):
            # CQ 码格式，转换为数组
            raw_message = self._parse_cq_code(raw_message)

        content = await self._parse_message(raw_message)

        # 确定聊天类型和 ID
        if message_type == "private":
            chat_type = "private"
            chat_id = str(data.get("user_id"))
        else:  # group
            chat_type = "group"
            chat_id = str(data.get("group_id"))

        sender = data.get("sender", {})
        user_id = str(data.get("user_id"))

        unified = UnifiedMessage.create(
            channel=self.channel_name,
            channel_message_id=str(data.get("message_id", "")),
            user_id=f"onebot_{user_id}",
            channel_user_id=user_id,
            chat_id=chat_id,
            content=content,
            chat_type=chat_type,
            raw=data,
            metadata={
                "nickname": sender.get("nickname"),
                "card": sender.get("card"),
                "is_group": chat_type == "group",
            },
        )

        self._log_message(unified)
        await self._emit_message(unified)

    def _parse_cq_code(self, message: str) -> list[dict]:
        """解析 CQ 码"""
        import re

        result = []
        pattern = r"\[CQ:(\w+)(?:,([^\]]+))?\]"

        last_end = 0
        for match in re.finditer(pattern, message):
            # 前面的文本
            if match.start() > last_end:
                text = message[last_end : match.start()]
                if text:
                    result.append({"type": "text", "data": {"text": text}})

            # CQ 码
            cq_type = match.group(1)
            params = {}
            if match.group(2):
                for param in match.group(2).split(","):
                    if "=" in param:
                        key, value = param.split("=", 1)
                        params[key] = value

            result.append({"type": cq_type, "data": params})
            last_end = match.end()

        # 剩余文本
        if last_end < len(message):
            text = message[last_end:]
            if text:
                result.append({"type": "text", "data": {"text": text}})

        return result

    async def _parse_message(self, message: list) -> MessageContent:
        """解析 OneBot 消息"""
        content = MessageContent()

        text_parts = []

        for segment in message:
            seg_type = segment.get("type")
            data = segment.get("data", {})

            if seg_type == "text":
                text_parts.append(data.get("text", ""))

            elif seg_type == "image":
                media = MediaFile.create(
                    filename=data.get("file", "image.jpg"),
                    mime_type="image/jpeg",
                    url=data.get("url"),
                    file_id=data.get("file"),
                )
                content.images.append(media)

            elif seg_type == "record":
                media = MediaFile.create(
                    filename=data.get("file", "voice.amr"),
                    mime_type="audio/amr",
                    url=data.get("url"),
                    file_id=data.get("file"),
                )
                content.voices.append(media)

            elif seg_type == "video":
                media = MediaFile.create(
                    filename=data.get("file", "video.mp4"),
                    mime_type="video/mp4",
                    url=data.get("url"),
                    file_id=data.get("file"),
                )
                content.videos.append(media)

            elif seg_type == "file":
                media = MediaFile.create(
                    filename=data.get("name", "file"),
                    mime_type="application/octet-stream",
                    file_id=data.get("id"),
                )
                content.files.append(media)

            elif seg_type == "at":
                text_parts.append(f"@{data.get('qq', data.get('id', ''))}")

            elif seg_type == "face":
                text_parts.append(f"[表情:{data.get('id', '')}]")

        content.text = "".join(text_parts) if text_parts else None

        return content

    async def _call_api(self, action: str, params: dict = None) -> Any:
        """调用 OneBot API"""
        if not self._ws:
            raise RuntimeError("WebSocket not connected")

        echo = str(uuid.uuid4())

        request = {
            "action": action,
            "params": params or {},
            "echo": echo,
        }

        # 创建 Future 等待响应
        future = asyncio.get_running_loop().create_future()
        self._api_callbacks[echo] = future

        try:
            await self._ws.send(json.dumps(request))
            result = await asyncio.wait_for(future, timeout=30)
            return result
        except TimeoutError:
            self._api_callbacks.pop(echo, None)
            raise RuntimeError(f"API call timeout: {action}")

    async def send_message(self, message: OutgoingMessage) -> str:
        """发送消息"""
        # 构建消息数组
        msg_array = []

        # 文本
        if message.content.text:
            msg_array.append({"type": "text", "data": {"text": message.content.text}})

        # 图片
        for img in message.content.images:
            if img.local_path:
                msg_array.append({"type": "image", "data": {"file": f"file:///{img.local_path}"}})
            elif img.url:
                msg_array.append({"type": "image", "data": {"file": img.url}})

        # 语音
        for voice in message.content.voices:
            if voice.local_path:
                msg_array.append(
                    {"type": "record", "data": {"file": f"file:///{voice.local_path}"}}
                )

        # 判断是群消息还是私聊
        chat_id = int(message.chat_id)

        # 发送（默认发送私聊，群聊需要额外判断）
        if message.metadata.get("is_group"):
            result = await self._call_api(
                "send_group_msg",
                {
                    "group_id": chat_id,
                    "message": msg_array,
                },
            )
        else:
            result = await self._call_api(
                "send_private_msg",
                {
                    "user_id": chat_id,
                    "message": msg_array,
                },
            )

        return str(result.get("message_id", ""))

    async def send_group_message(
        self,
        group_id: int,
        message: str,
    ) -> str:
        """发送群消息"""
        result = await self._call_api(
            "send_group_msg",
            {
                "group_id": group_id,
                "message": message,
            },
        )
        return str(result.get("message_id", ""))

    async def send_private_message(
        self,
        user_id: int,
        message: str,
    ) -> str:
        """发送私聊消息"""
        result = await self._call_api(
            "send_private_msg",
            {
                "user_id": user_id,
                "message": message,
            },
        )
        return str(result.get("message_id", ""))

    async def download_media(self, media: MediaFile) -> Path:
        """下载媒体文件"""
        if media.local_path and Path(media.local_path).exists():
            return Path(media.local_path)

        if media.url:
            try:
                import httpx as hx
            except ImportError:
                raise ImportError("httpx not installed. Run: pip install httpx")

            async with hx.AsyncClient() as client:
                response = await client.get(media.url)

                local_path = self.media_dir / media.filename
                with open(local_path, "wb") as f:
                    f.write(response.content)

                media.local_path = str(local_path)
                media.status = MediaStatus.READY

                return local_path

        raise ValueError("Media has no url")

    async def upload_media(self, path: Path, mime_type: str) -> MediaFile:
        """上传媒体文件"""
        # OneBot 直接使用本地路径
        return MediaFile.create(
            filename=path.name,
            mime_type=mime_type,
        )

    async def get_user_info(self, user_id: str) -> dict | None:
        """获取用户信息"""
        try:
            result = await self._call_api(
                "get_stranger_info",
                {
                    "user_id": int(user_id),
                },
            )
            return {
                "id": str(result.get("user_id")),
                "nickname": result.get("nickname"),
                "sex": result.get("sex"),
                "age": result.get("age"),
            }
        except Exception:
            return None

    async def get_group_info(self, group_id: int) -> dict | None:
        """获取群信息"""
        try:
            result = await self._call_api(
                "get_group_info",
                {
                    "group_id": group_id,
                },
            )
            return {
                "id": str(result.get("group_id")),
                "name": result.get("group_name"),
                "member_count": result.get("member_count"),
                "max_member_count": result.get("max_member_count"),
            }
        except Exception:
            return None

    async def send_file(
        self,
        chat_id: str,
        file_path: str,
        caption: str | None = None,
    ) -> str:
        """
        发送文件

        OneBot v11 不支持通过 CQ 码/消息段发送文件，
        必须使用 upload_group_file / upload_private_file 专用 API。
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        chat_id_int = int(chat_id)

        # 如果有 caption，先发送文本
        if caption:
            text_msg = [{"type": "text", "data": {"text": caption}}]
            try:
                await self._call_api(
                    "send_group_msg",
                    {"group_id": chat_id_int, "message": text_msg},
                )
            except Exception:
                with contextlib.suppress(Exception):
                    await self._call_api(
                        "send_private_msg",
                        {"user_id": chat_id_int, "message": text_msg},
                    )

        # 尝试群文件上传
        try:
            result = await self._call_api(
                "upload_group_file",
                {
                    "group_id": chat_id_int,
                    "file": str(path.resolve()),
                    "name": path.name,
                },
            )
            return str(result.get("message_id", f"file_{chat_id}"))
        except Exception:
            pass

        # 回退到私聊文件上传
        try:
            result = await self._call_api(
                "upload_private_file",
                {
                    "user_id": chat_id_int,
                    "file": str(path.resolve()),
                    "name": path.name,
                },
            )
            return str(result.get("message_id", f"file_{chat_id}"))
        except Exception as e:
            raise RuntimeError(f"Failed to send file via OneBot: {e}") from e

    async def send_voice(
        self,
        chat_id: str,
        voice_path: str,
        caption: str | None = None,
    ) -> str:
        """发送语音消息"""
        path = Path(voice_path)
        if not path.exists():
            raise FileNotFoundError(f"Voice file not found: {voice_path}")

        chat_id_int = int(chat_id)

        msg_array = [
            {"type": "record", "data": {"file": f"file:///{path.resolve()}"}}
        ]

        # 如果有 caption，加上文本（分两条消息，语音不支持附带文本）
        if caption:
            caption_msg = [{"type": "text", "data": {"text": caption}}]

        # 尝试群聊发送
        try:
            result = await self._call_api(
                "send_group_msg",
                {"group_id": chat_id_int, "message": msg_array},
            )
            if caption:
                with contextlib.suppress(Exception):
                    await self._call_api(
                        "send_group_msg",
                        {"group_id": chat_id_int, "message": caption_msg},
                    )
            return str(result.get("message_id", ""))
        except Exception:
            pass

        # 回退到私聊
        result = await self._call_api(
            "send_private_msg",
            {"user_id": chat_id_int, "message": msg_array},
        )
        if caption:
            with contextlib.suppress(Exception):
                await self._call_api(
                    "send_private_msg",
                    {"user_id": chat_id_int, "message": caption_msg},
                )
        return str(result.get("message_id", ""))

    async def delete_message(self, chat_id: str, message_id: str) -> bool:
        """撤回消息"""
        try:
            await self._call_api(
                "delete_msg",
                {
                    "message_id": int(message_id),
                },
            )
            return True
        except Exception:
            return False
