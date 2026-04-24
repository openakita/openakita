"""L3 Integration Tests: MessageGateway message routing and processing."""

from unittest.mock import AsyncMock, MagicMock

import pytest

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

