"""Channel adapter abstractions for IM channel plugins.

This mirrors ``openakita.channels.base.ChannelAdapter`` so plugin authors can
subclass without installing the full runtime.  The runtime adapter has more
helpers and internal wiring, but this SDK version gives you the complete
interface contract that the Gateway calls.

Key design notes:

- **Streaming**: The Gateway calls ``stream_token`` / ``finalize_stream`` when
  the adapter's ``capabilities["streaming"]`` is ``True``.  If your platform
  doesn't support real-time message edits (e.g. WhatsApp), accumulate tokens in
  ``stream_token`` and send one message in ``finalize_stream``.

- **Group chat / @mentions**: The Gateway reads ``UnifiedMessage.chat_type``,
  ``is_mentioned``, and ``is_direct_message`` to decide how to respond in
  groups.  Your adapter **must** set these correctly in ``_emit_message()``.

- **Typing**: The Gateway calls ``send_typing`` every ~4 s while the agent is
  thinking.  Implement it if your platform has a "typing indicator" API.

- **Media**: ``download_media`` / ``upload_media`` are called when the user
  sends or receives images/files/voice/video.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, ClassVar


class ChannelAdapter(ABC):
    """Abstract base for IM channel adapters.

    Subclass this in your plugin and implement all abstract methods.
    Override ``capabilities`` to declare what your adapter supports.
    """

    channel_name: str = "unknown"

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

    # --- Lifecycle ---

    @abstractmethod
    async def start(self) -> None:
        """Start the adapter (connect, set up webhooks, etc.)."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop the adapter and release resources."""

    # --- Messaging ---

    @abstractmethod
    async def send_message(self, message: Any) -> str:
        """Send an ``OutgoingMessage``. Return the platform message ID."""

    async def send_text(self, chat_id: str, text: str, **kwargs: Any) -> str:
        """Convenience: send a plain text message.

        The default builds an ``OutgoingMessage`` and calls ``send_message``.
        Override if you have a simpler send path.
        """
        try:
            from openakita.channels.types import OutgoingMessage
            return await self.send_message(OutgoingMessage.text(chat_id, text, **kwargs))
        except ImportError:
            raise NotImplementedError(
                "Default send_text requires the full OpenAkita runtime. "
                "Override send_text() in your adapter."
            )

    # --- Streaming ---

    def is_streaming_enabled(self, is_group: bool = False) -> bool:
        """Whether streaming is enabled for this adapter instance.

        Called by Gateway before ``_call_agent_streaming``.
        Return ``True`` to receive ``stream_token`` / ``finalize_stream`` calls.
        """
        return self.capabilities.get("streaming", False)

    async def stream_token(
        self,
        chat_id: str,
        token: str,
        *,
        thread_id: str | None = None,
        is_group: bool = False,
    ) -> None:
        """Called by Gateway for each text delta during streaming.

        For platforms that support message editing (Telegram), you can edit
        a placeholder message.  For others (WhatsApp), accumulate tokens
        and send in ``finalize_stream``.
        """

    async def finalize_stream(
        self,
        chat_id: str,
        final_text: str,
        *,
        thread_id: str | None = None,
    ) -> bool:
        """Called when streaming completes.

        Return ``True`` if the full reply was already sent (streamed),
        so the Gateway skips its normal ``_send_response``.
        Return ``False`` to let the Gateway handle sending.
        """
        return False

    # --- Typing ---

    async def send_typing(self, chat_id: str, thread_id: str | None = None) -> None:
        """Send a typing indicator. Called every ~4s while agent is processing."""

    async def clear_typing(self, chat_id: str, thread_id: str | None = None) -> None:
        """Clear the typing indicator."""

    # --- Media ---

    async def download_media(self, media: Any) -> Path:
        """Download a ``MediaFile`` to a local temp path and return it."""
        raise NotImplementedError

    async def upload_media(self, path: Path, mime_type: str) -> Any:
        """Upload a local file and return a ``MediaFile``."""
        raise NotImplementedError

    # --- Message management ---

    async def delete_message(self, chat_id: str, message_id: str) -> bool:
        """Delete a message. Return True on success."""
        return False

    async def edit_message(
        self, chat_id: str, message_id: str, new_text: str
    ) -> bool:
        """Edit a message. Return True on success."""
        return False

    # --- Info ---

    async def get_chat_info(self, chat_id: str) -> dict | None:
        """Return chat metadata, or None if not supported."""
        return None

    async def get_user_info(self, user_id: str) -> dict | None:
        """Return user metadata, or None if not supported."""
        return None

    def format_final_footer(
        self, chat_id: str, thread_id: str | None = None
    ) -> str | None:
        """Optional footer appended to the last text chunk before send."""
        return None

    def has_capability(self, name: str) -> bool:
        return self.capabilities.get(name, False)


class ChannelPluginMixin:
    """Convenience mixin for channel plugins.

    Provides a standard ``register`` helper that calls
    ``api.register_channel(type_name, factory)`` during ``on_load``.

    Usage::

        class Plugin(PluginBase, ChannelPluginMixin):
            channel_type = "whatsapp"

            def on_load(self, api):
                self.register(api, self.create_adapter)

            def create_adapter(self, creds, *, channel_name, bot_id, agent_profile_id):
                return WhatsAppAdapter(...)
    """

    channel_type: str = ""

    def register(self, api: Any, factory: Any) -> None:
        if not self.channel_type:
            raise ValueError("Set channel_type before calling register()")
        api.register_channel(self.channel_type, factory)
