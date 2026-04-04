"""
通道适配器基类

定义 IM 通道适配器的抽象接口:
- 启动/停止
- 消息收发
- 媒体处理
- 事件回调
"""

import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import ClassVar

from .types import MediaFile, OutgoingMessage, UnifiedMessage

logger = logging.getLogger(__name__)

# Windows 文件名非法字符 (: * ? " < > |)
_UNSAFE_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_filename(name: str) -> str:
    """将文件名中的非法字符替换为下划线，确保跨平台兼容。"""
    safe = _UNSAFE_FILENAME_RE.sub("_", name)
    return safe.strip(". ") or "download"


# 回调类型定义
MessageCallback = Callable[[UnifiedMessage], Awaitable[None]]
EventCallback = Callable[[str, dict], Awaitable[None]]
FailureCallback = Callable[[str, str], None]  # (adapter_name, reason)


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

    STALE_MESSAGE_THRESHOLD_S: ClassVar[int] = 120

    capabilities: ClassVar[dict[str, bool]] = {
        "streaming": False,
        "send_image": False,
        "send_file": False,
        "send_voice": False,
        "delete_message": False,
        "edit_message": False,
        "get_chat_info": False,
        "get_user_info": False,
        "get_chat_members": False,
        "get_recent_messages": False,
        "markdown": False,
        "add_reaction": False,
    }

    def __init__(self, *, channel_name: str | None = None, bot_id: str | None = None, agent_profile_id: str = "default"):
        self._message_callback: MessageCallback | None = None
        self._event_callback: EventCallback | None = None
        self._failure_callback: FailureCallback | None = None
        self._running = False
        if channel_name is not None:
            self.channel_name = channel_name
        if bot_id is not None:
            self.bot_id = bot_id
        else:
            self.bot_id = self.channel_name
        self.agent_profile_id = agent_profile_id

    def has_capability(self, name: str) -> bool:
        return self.capabilities.get(name, False)

    @property
    def channel_type(self) -> str:
        """Base channel platform type (e.g. 'feishu', 'qqbot').

        When channel_name is a multi-bot instance like 'feishu:my-bot',
        this returns 'feishu'.  For simple names it returns channel_name as-is.
        """
        return self.channel_name.split(":")[0]

    @property
    def is_running(self) -> bool:
        """是否运行中"""
        return self._running

    def collect_warnings(self) -> list[str]:
        """检查配置和运行状态，返回安全/配置告警列表。

        子类可覆写此方法以添加平台特有的检查。
        基类提供通用检查：
        - 必填凭证是否疑似占位符
        - 端口范围检查
        """
        warnings: list[str] = []
        config = getattr(self, "config", None)
        if config is None:
            return warnings

        placeholder_hints = ("your_", "xxx", "placeholder", "changeme", "test123")
        for field_name in ("app_id", "app_key", "app_secret", "token", "secret", "bot_id"):
            value = getattr(config, field_name, None)
            if isinstance(value, str) and value:
                lower = value.lower()
                for hint in placeholder_hints:
                    if lower.startswith(hint) or lower == hint:
                        warnings.append(
                            f"[{self.channel_name}] {field_name} 疑似占位符值 '{value[:20]}'，"
                            f"请检查配置是否正确。"
                        )
                        break

        port = getattr(config, "callback_port", None) or getattr(config, "webhook_port", None)
        if isinstance(port, int) and port < 1024:
            warnings.append(
                f"[{self.channel_name}] 端口 {port} < 1024，"
                f"可能需要 root 权限或 setcap 配置。"
            )

        return warnings

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

    def format_final_footer(self, chat_id: str, thread_id: str | None = None) -> str | None:
        """返回追加到最终回复末尾的 footer 文本（如耗时统计）。

        默认返回 None（不追加）。子类可覆写此方法，返回的文本会被 gateway
        拼接到最后一条分片消息末尾，并在调用后自动重置内部计时器。
        """
        return None

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

    def on_failure(self, callback: FailureCallback) -> None:
        """注册致命失败回调，由网关设置以更新状态面板。"""
        self._failure_callback = callback

    def _report_failure(self, reason: str) -> None:
        """通知网关本适配器已致命失败（认证错误等），使状态面板正确反映离线。"""
        if self._failure_callback:
            try:
                self._failure_callback(self.channel_name, reason)
            except Exception as e:
                logger.error(f"{self.channel_name}: failure callback error: {e}")

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

    async def get_chat_members(self, chat_id: str) -> list[dict]:
        """获取群聊成员列表"""
        return []

    async def get_recent_messages(self, chat_id: str, limit: int = 20) -> list[dict]:
        """获取最近消息列表"""
        return []

    def get_pending_events(self, chat_id: str) -> list[dict]:
        """获取并清空待处理的重要事件（如群公告变更、@所有人等）"""
        return []

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

    async def add_reaction(
        self,
        chat_id: str,
        message_id: str,
        emoji: str = "👀",
    ) -> bool:
        """为消息添加 emoji 表情反应（可选能力）。

        Args:
            chat_id: 聊天 ID
            message_id: 目标消息 ID
            emoji: 表情符号或平台特定标识

        Returns:
            是否成功
        """
        return False

    async def send_typing(self, chat_id: str, thread_id: str | None = None) -> None:
        """发送正在输入状态"""
        # 可选能力：默认实现为 no-op（部分平台不支持 typing 或无需实现）
        logger.debug(f"{self.channel_name}: typing (noop) chat_id={chat_id}")

    async def clear_typing(self, chat_id: str, thread_id: str | None = None) -> None:
        """清除 typing 状态提示（如有）。默认 no-op。"""

    # ==================== 辅助方法 ====================

    def _log_message(self, message: UnifiedMessage) -> None:
        """记录消息日志"""
        text_preview = message.text[:80] if message.text else f"({message.message_type.value})"
        logger.info(
            f"{self.channel_name}: received message from {message.channel_user_id} "
            f"in {message.chat_id}: {text_preview}"
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
