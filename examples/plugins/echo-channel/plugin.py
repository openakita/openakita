"""echo-channel: registers a functional IM adapter that echoes messages back."""

from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path

from openakita.channels.base import ChannelAdapter
from openakita.channels.types import MediaFile, OutgoingMessage
from openakita.plugins.api import PluginAPI, PluginBase

logger = logging.getLogger(__name__)


class EchoAdapter(ChannelAdapter):
    """Adapter that echoes received messages back to the sender."""

    capabilities = {
        **ChannelAdapter.capabilities,
        "streaming": False,
        "send_image": False,
        "send_file": False,
    }

    def __init__(
        self,
        creds: dict,
        *,
        channel_name: str,
        bot_id: str,
        agent_profile_id: str,
    ) -> None:
        super().__init__(channel_name=channel_name, bot_id=bot_id, agent_profile_id=agent_profile_id)
        self._creds = creds
        self._sent_messages: list[dict] = []

    async def start(self) -> None:
        self._running = True
        logger.info("EchoAdapter started, creds_keys=%s", list(self._creds.keys()))

    async def stop(self) -> None:
        self._running = False
        logger.info("EchoAdapter stopped")

    async def send_message(self, message: OutgoingMessage) -> str:
        text = message.content.text if message.content else ""
        msg_id = f"echo-msg-{len(self._sent_messages)}"
        self._sent_messages.append({
            "id": msg_id,
            "chat_id": message.chat_id,
            "text": text,
        })
        logger.info("EchoAdapter sent message id=%s chat_id=%s text=%s", msg_id, message.chat_id, text[:200])
        return msg_id

    async def download_media(self, media: MediaFile) -> Path:
        logger.info("EchoAdapter download_media id=%s filename=%s", media.id, media.filename)
        return Path(tempfile.gettempdir()) / f"echo-dl-{media.id}.bin"

    async def upload_media(self, path: Path, mime_type: str) -> MediaFile:
        logger.info("EchoAdapter upload_media path=%s mime=%s", path, mime_type)
        return MediaFile.create(path.name, mime_type)

    def get_sent_messages(self) -> list[dict]:
        """Test helper: return list of messages sent by this adapter."""
        return list(self._sent_messages)


def _echo_factory(
    creds: dict,
    *,
    channel_name: str,
    bot_id: str,
    agent_profile_id: str,
) -> EchoAdapter:
    return EchoAdapter(
        creds,
        channel_name=channel_name,
        bot_id=bot_id,
        agent_profile_id=agent_profile_id,
    )


class Plugin(PluginBase):
    def on_load(self, api: PluginAPI) -> None:
        self._api = api
        api.register_channel("echo", _echo_factory)
        api.register_hook("on_message_received", self._on_message)
        api.log("Echo channel plugin loaded — will echo all received messages")

    async def _on_message(self, **kwargs) -> None:
        """Echo the received message back to the sender."""
        message = kwargs.get("message")
        if message is None:
            return
        channel = getattr(message, "channel", None)
        chat_id = getattr(message, "chat_id", None)
        text = getattr(message, "text", "")
        if channel and chat_id and text:
            try:
                await self._api.send_message(channel, chat_id, f"[Echo] {text}")
            except Exception as e:
                self._api.log_error(f"Echo send failed: {e}")

    def on_unload(self) -> None:
        pass

