"""
通道适配器基类

定义 IM 通道适配器的抽象接口:
- 启动/停止
- 消息收发
- 媒体处理
- 事件回调
"""

import logging
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from pathlib import Path

from .types import MediaFile, OutgoingMessage, UnifiedMessage

logger = logging.getLogger(__name__)

# 回调类型定义
MessageCallback = Callable[[UnifiedMessage], Awaitable[None]]
EventCallback = Callable[[str, dict], Awaitable[None]]


class ChannelAdapter(ABC):
    """
    IM 通道适配器基类

    各平台适配器需要实现此接口:
    - Telegram
    - 飞书
    - 企业微信
    - 钉钉
    - OneBot (通用协议)
    - QQ 官方机器人
    """

    # 通道名称（子类必须覆盖）
    channel_name: str = "unknown"

    def __init__(self):
        self._message_callback: MessageCallback | None = None
        self._event_callback: EventCallback | None = None
        self._running = False

    @property
    def is_running(self) -> bool:
        """是否运行中"""
        return self._running

    # ==================== 生命周期 ====================

    @abstractmethod
    async def start(self) -> None:
        """
        启动适配器

        建立连接、启动 webhook 等
        """
        pass

    @abstractmethod
    async def stop(self) -> None:
        """
        停止适配器

        断开连接、清理资源
        """
        pass

    # ==================== 消息收发 ====================

    @abstractmethod
    async def send_message(self, message: OutgoingMessage) -> str:
        """
        发送消息

        Args:
            message: 要发送的消息

        Returns:
            发送后的消息 ID
        """
        pass

    async def send_text(
        self,
        chat_id: str,
        text: str,
        reply_to: str | None = None,
        **kwargs,
    ) -> str:
        """发送纯文本消息（便捷方法）"""
        message = OutgoingMessage.text(chat_id, text, reply_to=reply_to, **kwargs)
        return await self.send_message(message)

    async def send_image(
        self,
        chat_id: str,
        image_path: str,
        caption: str | None = None,
        reply_to: str | None = None,
        **kwargs,
    ) -> str:
        """发送图片消息（便捷方法）"""
        message = OutgoingMessage.with_image(
            chat_id, image_path, caption, reply_to=reply_to, **kwargs
        )
        return await self.send_message(message)

    # ==================== 媒体处理 ====================

    @abstractmethod
    async def download_media(self, media: MediaFile) -> Path:
        """
        下载媒体文件到本地

        Args:
            media: 媒体文件信息

        Returns:
            本地文件路径
        """
        pass

    @abstractmethod
    async def upload_media(self, path: Path, mime_type: str) -> MediaFile:
        """
        上传媒体文件

        Args:
            path: 本地文件路径
            mime_type: MIME 类型

        Returns:
            上传后的媒体文件信息
        """
        pass

    # ==================== 回调注册 ====================

    def on_message(self, callback: MessageCallback) -> None:
        """
        注册消息回调

        当收到消息时调用
        """
        self._message_callback = callback
        logger.debug(f"{self.channel_name}: message callback registered")

    def on_event(self, callback: EventCallback) -> None:
        """
        注册事件回调

        当收到平台事件时调用（如成员变更、群组更新等）
        """
        self._event_callback = callback
        logger.debug(f"{self.channel_name}: event callback registered")

    async def _emit_message(self, message: UnifiedMessage) -> None:
        """触发消息回调"""
        if self._message_callback:
            try:
                await self._message_callback(message)
            except Exception as e:
                logger.error(f"{self.channel_name}: message callback error: {e}")

    async def _emit_event(self, event_type: str, data: dict) -> None:
        """触发事件回调"""
        if self._event_callback:
            try:
                await self._event_callback(event_type, data)
            except Exception as e:
                logger.error(f"{self.channel_name}: event callback error: {e}")

    # ==================== 可选功能 ====================

    async def get_chat_info(self, chat_id: str) -> dict | None:
        """
        获取聊天信息

        Returns:
            {id, type, title, members_count, ...}
        """
        return None

    async def get_user_info(self, user_id: str) -> dict | None:
        """
        获取用户信息

        Returns:
            {id, username, display_name, avatar_url, ...}
        """
        return None

    async def delete_message(self, chat_id: str, message_id: str) -> bool:
        """删除消息"""
        return False

    async def edit_message(
        self,
        chat_id: str,
        message_id: str,
        new_content: str,
    ) -> bool:
        """编辑消息"""
        return False

    async def send_file(
        self,
        chat_id: str,
        file_path: str,
        caption: str | None = None,
    ) -> str:
        """
        发送文件（可选能力，子类覆盖实现）

        Args:
            chat_id: 目标聊天 ID
            file_path: 本地文件路径
            caption: 附加文字说明

        Returns:
            发送后的消息 ID

        Raises:
            NotImplementedError: 当前平台不支持发送文件
        """
        raise NotImplementedError(f"{self.channel_name} does not support send_file")

    async def send_voice(
        self,
        chat_id: str,
        voice_path: str,
        caption: str | None = None,
    ) -> str:
        """
        发送语音（可选能力，子类覆盖实现）

        Args:
            chat_id: 目标聊天 ID
            voice_path: 本地语音文件路径
            caption: 附加文字说明

        Returns:
            发送后的消息 ID

        Raises:
            NotImplementedError: 当前平台不支持发送语音
        """
        raise NotImplementedError(f"{self.channel_name} does not support send_voice")

    async def send_typing(self, chat_id: str) -> None:
        """发送正在输入状态"""
        # 可选能力：默认实现为 no-op（部分平台不支持 typing 或无需实现）
        logger.debug(f"{self.channel_name}: typing (noop) chat_id={chat_id}")

    # ==================== 辅助方法 ====================

    def _log_message(self, message: UnifiedMessage) -> None:
        """记录消息日志"""
        logger.info(
            f"{self.channel_name}: received message from {message.channel_user_id} "
            f"in {message.chat_id}: {message.text}"
            if message.text
            else f"{self.channel_name}: received {message.message_type.value}"
        )


class CLIAdapter(ChannelAdapter):
    """
    命令行适配器

    将现有的 CLI 交互封装为通道适配器
    """

    channel_name = "cli"

    def __init__(self):
        super().__init__()
        self._media_dir = Path("data/media/cli")
        self._media_dir.mkdir(parents=True, exist_ok=True)

    async def start(self) -> None:
        """启动（CLI 无需特殊启动）"""
        self._running = True
        logger.info("CLI adapter started")

    async def stop(self) -> None:
        """停止"""
        self._running = False
        logger.info("CLI adapter stopped")

    async def send_message(self, message: OutgoingMessage) -> str:
        """
        发送消息（打印到控制台）
        """
        from rich.console import Console
        from rich.markdown import Markdown

        console = Console()

        if message.content.text:
            # 尝试以 Markdown 格式渲染
            try:
                md = Markdown(message.content.text)
                console.print(md)
            except Exception:
                console.print(message.content.text)

        # 显示媒体文件信息
        for media in message.content.all_media:
            console.print(f"[附件: {media.filename}]")

        return f"cli_msg_{id(message)}"

    async def download_media(self, media: MediaFile) -> Path:
        """
        下载媒体（CLI 模式下通常已是本地文件）
        """
        if media.local_path:
            return Path(media.local_path)
        raise ValueError("CLI adapter: media has no local path")

    async def upload_media(self, path: Path, mime_type: str) -> MediaFile:
        """
        上传媒体（CLI 模式下直接使用本地路径）
        """
        return MediaFile.create(
            filename=path.name,
            mime_type=mime_type,
        )
