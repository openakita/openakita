"""L3 Integration Tests: MessageGateway message routing and processing."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from openakita.channels.gateway import MessageGateway
from openakita.channels.types import (
    MediaFile,
    MediaStatus,
    MessageContent,
    MessageType,
    OutgoingMessage,
    UnifiedMessage,
)
from tests.fixtures.factories import create_channel_message, create_test_session


class TestUnifiedMessage:
    def test_create_text_message(self):
        msg = create_channel_message(text="Hello")
        assert msg.content.text == "Hello"
        assert msg.message_type == MessageType.TEXT
        assert msg.channel == "telegram"

    def test_create_image_message(self):
        img = MediaFile(
            id="img1",
            filename="photo.jpg",
            mime_type="image/jpeg",
            status=MediaStatus.READY,
        )
        msg = create_channel_message(
            message_type=MessageType.IMAGE,
            images=[img],
        )
        assert len(msg.content.images) == 1
        assert msg.content.images[0].mime_type == "image/jpeg"

    def test_create_voice_message(self):
        voice = MediaFile(
            id="v1",
            filename="voice.ogg",
            mime_type="audio/ogg",
            duration=5.2,
        )
        msg = create_channel_message(
            message_type=MessageType.VOICE,
            voices=[voice],
        )
        assert len(msg.content.voices) == 1
        assert msg.content.voices[0].duration == 5.2


class TestOutgoingMessage:
    def test_create_outgoing(self):
        msg = OutgoingMessage(
            chat_id="chat-123",
            content=MessageContent(text="Reply"),
        )
        assert msg.chat_id == "chat-123"
        assert msg.content.text == "Reply"
        assert msg.silent is False


class TestMediaFile:
    def test_default_status(self):
        mf = MediaFile(id="f1", filename="test.txt", mime_type="text/plain")
        assert mf.status == MediaStatus.PENDING

    def test_all_statuses(self):
        assert MediaStatus.PENDING.value == "pending"
        assert MediaStatus.DOWNLOADING.value == "downloading"
        assert MediaStatus.READY.value == "ready"
        assert MediaStatus.FAILED.value == "failed"
        assert MediaStatus.PROCESSED.value == "processed"


class TestMessageTypes:
    def test_all_message_types(self):
        types = [
            MessageType.TEXT, MessageType.IMAGE, MessageType.VOICE,
            MessageType.FILE, MessageType.VIDEO, MessageType.LOCATION,
            MessageType.STICKER, MessageType.MIXED, MessageType.COMMAND,
            MessageType.UNKNOWN,
        ]
        assert len(types) == 10

    def test_message_content_defaults(self):
        mc = MessageContent()
        assert mc.text is None
        assert mc.images == []
        assert mc.voices == []
        assert mc.files == []
        assert mc.videos == []


class TestMessageGatewayBroadcast:
    @pytest.mark.asyncio
    async def test_send_rejects_non_text_payload(self):
        session_manager = MagicMock()
        gateway = MessageGateway(session_manager=session_manager)
        adapter = MagicMock()
        adapter.send_text = AsyncMock(return_value="msg-1")
        gateway._adapters["wechat:test"] = adapter

        result = await gateway.send("wechat:test", "chat-1", {"type": "plugin:event"})  # type: ignore[arg-type]

        assert result is None
        adapter.send_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_broadcast_rejects_non_text_payload_before_listing_sessions(self):
        session_manager = MagicMock()
        session_manager.list_sessions = MagicMock(return_value=[])
        gateway = MessageGateway(session_manager=session_manager)

        result = await gateway.broadcast({"type": "plugin:event", "data": {}})  # type: ignore[arg-type]

        assert result == {}
        session_manager.list_sessions.assert_not_called()

    @pytest.mark.asyncio
    async def test_broadcast_sends_normal_text(self):
        session = MagicMock()
        session.id = "session-1"
        session.channel = "wechat:test"
        session.chat_id = "chat-1"
        session.user_id = "user-1"
        session.thread_id = None

        session_manager = MagicMock()
        session_manager.list_sessions = MagicMock(return_value=[session])
        session_manager.mark_dirty = MagicMock()

        gateway = MessageGateway(session_manager=session_manager)
        adapter = MagicMock()
        adapter.send_text = AsyncMock(return_value="msg-1")
        gateway._adapters["wechat:test"] = adapter

        result = await gateway.broadcast("任务已完成")

        assert result == {"wechat:test": 1}
        adapter.send_text.assert_awaited_once()

