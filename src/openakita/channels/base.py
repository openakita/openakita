"""
Channel adapter base class

Defines the abstract interface for IM channel adapters:
- Start/stop
- Message send/receive
- Media handling
- Event callbacks
"""

import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import ClassVar

from .types import MediaFile, OutgoingMessage, UnifiedMessage

logger = logging.getLogger(__name__)

# Illegal Windows filename characters (: * ? " < > |)
_UNSAFE_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_filename(name: str) -> str:
    """Replace illegal characters in a filename with underscores for cross-platform compatibility."""
    safe = _UNSAFE_FILENAME_RE.sub("_", name)
    return safe.strip(". ") or "download"


# Callback type definitions
MessageCallback = Callable[[UnifiedMessage], Awaitable[None]]
EventCallback = Callable[[str, dict], Awaitable[None]]
FailureCallback = Callable[[str, str], None]  # (adapter_name, reason)


class ChannelAdapter(ABC):
    """
    Base class for IM channel adapters

    Each platform adapter must implement this interface:
    - Telegram
    - Feishu
    - WeCom
    - DingTalk
    - OneBot (generic protocol)
    - QQ official bot
    """

    # Channel name (subclasses must override)
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
    }

    def __init__(
        self,
        *,
        channel_name: str | None = None,
        bot_id: str | None = None,
        agent_profile_id: str = "default",
    ):
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
        """Whether the adapter is running"""
        return self._running

    def collect_warnings(self) -> list[str]:
        """Check configuration and runtime state, returning a list of safety/config warnings.

        Subclasses may override this method to add platform-specific checks.
        The base class provides generic checks:
        - Whether required credentials look like placeholders
        - Port range check
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
                            f"[{self.channel_name}] {field_name} looks like a placeholder value '{value[:20]}'; "
                            f"please verify the configuration."
                        )
                        break

        port = getattr(config, "callback_port", None) or getattr(config, "webhook_port", None)
        if isinstance(port, int) and port < 1024:
            warnings.append(
                f"[{self.channel_name}] port {port} < 1024; may require root privileges or setcap configuration."
            )

        return warnings

    # ==================== Lifecycle ====================

    @abstractmethod
    async def start(self) -> None:
        """
        Start the adapter

        Establish connections, start webhooks, etc.
        """
        pass

    @abstractmethod
    async def stop(self) -> None:
        """
        Stop the adapter

        Close connections, clean up resources
        """
        pass

    # ==================== Message send/receive ====================

    @abstractmethod
    async def send_message(self, message: OutgoingMessage) -> str:
        """
        Send a message

        Args:
            message: the message to send

        Returns:
            the ID of the sent message
        """
        pass

    async def send_text(
        self,
        chat_id: str,
        text: str,
        reply_to: str | None = None,
        **kwargs,
    ) -> str:
        """Send a plain text message (convenience method)"""
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
        """Send an image message (convenience method)"""
        message = OutgoingMessage.with_image(
            chat_id, image_path, caption, reply_to=reply_to, **kwargs
        )
        return await self.send_message(message)

    def format_final_footer(self, chat_id: str, thread_id: str | None = None) -> str | None:
        """Return footer text (e.g., elapsed time stats) to append to the end of the final reply.

        Returns None by default (no append). Subclasses may override; the returned text
        will be appended to the last chunked message by the gateway, and internal timers
        will be reset automatically after the call.
        """
        return None

    # ==================== Media handling ====================

    @abstractmethod
    async def download_media(self, media: MediaFile) -> Path:
        """
        Download a media file locally

        Args:
            media: media file info

        Returns:
            local file path
        """
        pass

    @abstractmethod
    async def upload_media(self, path: Path, mime_type: str) -> MediaFile:
        """
        Upload a media file

        Args:
            path: local file path
            mime_type: MIME type

        Returns:
            uploaded media file info
        """
        pass

    # ==================== Callback registration ====================

    def on_message(self, callback: MessageCallback) -> None:
        """
        Register a message callback

        Called when a message is received
        """
        self._message_callback = callback
        logger.debug(f"{self.channel_name}: message callback registered")

    def on_event(self, callback: EventCallback) -> None:
        """
        Register an event callback

        Called when a platform event is received (e.g., member changes, group updates)
        """
        self._event_callback = callback
        logger.debug(f"{self.channel_name}: event callback registered")

    def on_failure(self, callback: FailureCallback) -> None:
        """Register a fatal-failure callback, set by the gateway to update the status panel."""
        self._failure_callback = callback

    def _report_failure(self, reason: str) -> None:
        """Notify the gateway that this adapter has fatally failed (e.g., auth error), so the status panel correctly reflects offline."""
        if self._failure_callback:
            try:
                self._failure_callback(self.channel_name, reason)
            except Exception as e:
                logger.error(f"{self.channel_name}: failure callback error: {e}")

    async def _emit_message(self, message: UnifiedMessage) -> None:
        """Trigger the message callback"""
        if not self._running:
            return
        if self._message_callback:
            try:
                await self._message_callback(message)
            except Exception as e:
                logger.error(f"{self.channel_name}: message callback error: {e}")

    async def _emit_event(self, event_type: str, data: dict) -> None:
        """Trigger the event callback"""
        if self._event_callback:
            try:
                await self._event_callback(event_type, data)
            except Exception as e:
                logger.error(f"{self.channel_name}: event callback error: {e}")

    # ==================== Optional features ====================

    async def get_chat_info(self, chat_id: str) -> dict | None:
        """
        Get chat info

        Returns:
            {id, type, title, members_count, ...}
        """
        return None

    async def get_user_info(self, user_id: str) -> dict | None:
        """
        Get user info

        Returns:
            {id, username, display_name, avatar_url, ...}
        """
        return None

    async def get_chat_members(self, chat_id: str) -> list[dict]:
        """Get the member list of a group chat"""
        return []

    async def get_recent_messages(self, chat_id: str, limit: int = 20) -> list[dict]:
        """Get the list of recent messages"""
        return []

    def get_pending_events(self, chat_id: str) -> list[dict]:
        """Fetch and clear pending important events (e.g., group announcement changes, @everyone)"""
        return []

    async def delete_message(self, chat_id: str, message_id: str) -> bool:
        """Delete a message"""
        return False

    async def edit_message(
        self,
        chat_id: str,
        message_id: str,
        new_content: str,
    ) -> bool:
        """Edit a message"""
        return False

    async def send_file(
        self,
        chat_id: str,
        file_path: str,
        caption: str | None = None,
    ) -> str:
        """
        Send a file (optional capability; subclasses override to implement)

        Args:
            chat_id: target chat ID
            file_path: local file path
            caption: additional text caption

        Returns:
            the ID of the sent message

        Raises:
            NotImplementedError: the current platform does not support sending files
        """
        raise NotImplementedError(f"{self.channel_name} does not support send_file")

    async def send_voice(
        self,
        chat_id: str,
        voice_path: str,
        caption: str | None = None,
    ) -> str:
        """
        Send a voice message (optional capability; subclasses override to implement)

        Args:
            chat_id: target chat ID
            voice_path: local voice file path
            caption: additional text caption

        Returns:
            the ID of the sent message

        Raises:
            NotImplementedError: the current platform does not support sending voice
        """
        raise NotImplementedError(f"{self.channel_name} does not support send_voice")

    async def send_typing(self, chat_id: str, thread_id: str | None = None) -> None:
        """Send a typing indicator"""
        # Optional capability: default implementation is a no-op (some platforms do not support typing or do not need it)
        logger.debug(f"{self.channel_name}: typing (noop) chat_id={chat_id}")

    async def clear_typing(self, chat_id: str, thread_id: str | None = None) -> None:
        """Clear the typing indicator (if any). No-op by default."""

    # ==================== Helper methods ====================

    def _log_message(self, message: UnifiedMessage) -> None:
        """Log an incoming message"""
        text_preview = message.text[:80] if message.text else f"({message.message_type.value})"
        logger.info(
            f"{self.channel_name}: received message from {message.channel_user_id} "
            f"in {message.chat_id}: {text_preview}"
        )


class CLIAdapter(ChannelAdapter):
    """
    Command-line adapter

    Wraps the existing CLI interaction as a channel adapter
    """

    channel_name = "cli"

    def __init__(self):
        super().__init__()
        self._media_dir = Path("data/media/cli")
        self._media_dir.mkdir(parents=True, exist_ok=True)

    async def start(self) -> None:
        """Start (CLI needs no special startup)"""
        self._running = True
        logger.info("CLI adapter started")

    async def stop(self) -> None:
        """Stop"""
        self._running = False
        logger.info("CLI adapter stopped")

    async def send_message(self, message: OutgoingMessage) -> str:
        """
        Send a message (print to the console)
        """
        from rich.console import Console
        from rich.markdown import Markdown

        console = Console()

        if message.content.text:
            # Attempt to render as Markdown
            try:
                md = Markdown(message.content.text)
                console.print(md)
            except Exception:
                console.print(message.content.text)

        # Show media file info
        for media in message.content.all_media:
            console.print(f"[Attachment: {media.filename}]")

        return f"cli_msg_{id(message)}"

    async def download_media(self, media: MediaFile) -> Path:
        """
        Download media (in CLI mode this is typically already a local file)
        """
        if media.local_path:
            return Path(media.local_path)
        raise ValueError("CLI adapter: media has no local path")

    async def upload_media(self, path: Path, mime_type: str) -> MediaFile:
        """
        Upload media (in CLI mode the local path is used directly)
        """
        return MediaFile.create(
            filename=path.name,
            mime_type=mime_type,
        )
