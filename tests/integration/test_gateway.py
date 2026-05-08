"""L3 Integration Tests: MessageGateway message routing and processing."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import openakita.channels.gateway as gateway_module
from openakita.channels.gateway import MessageGateway
from openakita.channels.types import (
    MediaFile,
    MediaStatus,
    MessageContent,
    MessageType,
    OutgoingMessage,
)
from openakita.sessions import SessionManager
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


class TestMessageGatewayAgentBinding:
    def test_applies_adapter_bound_agent_to_new_session(self):
        session = create_test_session(channel="feishu:writer", chat_id="chat-1", user_id="user-1")
        session_manager = MagicMock()
        session_manager.mark_dirty = MagicMock()

        gateway = MessageGateway(session_manager=session_manager)
        gateway._adapters["feishu:writer"] = SimpleNamespace(agent_profile_id="writer-agent")

        gateway._apply_bot_agent_profile(session, "feishu:writer")

        assert session.get_metadata("_bot_default_agent") == "writer-agent"
        assert session.context.agent_profile_id == "writer-agent"
        session_manager.mark_dirty.assert_called_once()

    def test_preserves_manual_agent_switch_when_applying_bot_default(self):
        session = create_test_session(channel="feishu:writer", chat_id="chat-1", user_id="user-1")
        session.context.agent_profile_id = "reviewer-agent"
        session.context.agent_switch_history.append(
            {"from": "writer-agent", "to": "reviewer-agent", "at": "2026-05-08T00:00:00"}
        )
        session_manager = MagicMock()
        session_manager.mark_dirty = MagicMock()

        gateway = MessageGateway(session_manager=session_manager)
        gateway._adapters["feishu:writer"] = SimpleNamespace(agent_profile_id="writer-agent")

        gateway._apply_bot_agent_profile(session, "feishu:writer")

        assert session.get_metadata("_bot_default_agent") == "writer-agent"
        assert session.context.agent_profile_id == "reviewer-agent"
        session_manager.mark_dirty.assert_not_called()


class TestMessageGatewayDesktopMirror:
    def test_mirrors_im_turns_into_desktop_conversation(self, tmp_path):
        session_manager = SessionManager(storage_path=tmp_path / "sessions")
        gateway = MessageGateway(session_manager=session_manager)
        im_session = session_manager.get_session(
            "feishu:writer",
            "chat-1",
            "user-1",
            chat_type="private",
            display_name="用户甲",
            chat_name="飞书私聊",
        )
        im_session.context.agent_profile_id = "writer-agent"

        gateway._mirror_im_message_to_desktop(
            im_session,
            role="user",
            content="帮我检查服务器",
            source_message_id="om-1",
        )
        gateway._mirror_im_message_to_desktop(
            im_session,
            role="assistant",
            content="已完成检查",
            chain_summary=[{"iteration": 1, "tools": []}],
            tool_summary="checked",
        )

        mirror_id = gateway._desktop_mirror_id_for_im(im_session)
        mirror = session_manager.get_session(
            "desktop",
            mirror_id,
            "desktop_user",
            create_if_missing=False,
        )

        assert mirror is not None
        assert mirror.context.agent_profile_id == "writer-agent"
        assert mirror.get_metadata("source_channel") == "feishu:writer"
        assert mirror.context.messages[0]["role"] == "user"
        assert mirror.context.messages[0]["content"].startswith("[来自飞书")
        assert "帮我检查服务器" in mirror.context.messages[0]["content"]
        assert mirror.context.messages[1]["role"] == "assistant"
        assert mirror.context.messages[1]["tool_summary"] == "checked"


class TestMessageGatewayAgentTimeout:
    @pytest.mark.asyncio
    async def test_call_agent_has_no_default_wall_clock_timeout(self, monkeypatch):
        monkeypatch.delenv("AGENT_HANDLER_TIMEOUT", raising=False)

        async def fail_wait_for(*args, **kwargs):
            raise AssertionError("default IM agent handling must not use wait_for")

        monkeypatch.setattr(gateway_module.asyncio, "wait_for", fail_wait_for)

        async def agent_handler(session, text):
            return f"handled: {text}"

        gateway = MessageGateway(session_manager=MagicMock(), agent_handler=agent_handler)
        session = create_test_session(channel="feishu:writer", chat_id="chat-1", user_id="user-1")
        message = create_channel_message(
            channel="feishu:writer",
            chat_id="chat-1",
            user_id="user-1",
            text="写一篇长文章",
        )

        response, streamed_ok = await gateway._call_agent(session, message)

        assert response == "handled: 写一篇长文章"
        assert streamed_ok is False

    @pytest.mark.asyncio
    async def test_call_agent_respects_explicit_wall_clock_timeout(self, monkeypatch):
        monkeypatch.setenv("AGENT_HANDLER_TIMEOUT", "0.01")

        async def agent_handler(session, text):
            await asyncio.sleep(1)
            return "too late"

        gateway = MessageGateway(session_manager=MagicMock(), agent_handler=agent_handler)
        session = create_test_session(channel="feishu:writer", chat_id="chat-1", user_id="user-1")
        message = create_channel_message(
            channel="feishu:writer",
            chat_id="chat-1",
            user_id="user-1",
            text="写一篇长文章",
        )

        response, streamed_ok = await gateway._call_agent(session, message)

        assert streamed_ok is False
        assert "超过配置的处理时长上限" in response
        assert "AGENT_HANDLER_TIMEOUT" in response

    @pytest.mark.asyncio
    async def test_streaming_agent_has_no_default_wall_clock_timeout(self, monkeypatch):
        monkeypatch.delenv("AGENT_HANDLER_TIMEOUT", raising=False)

        async def fail_wait_for(*args, **kwargs):
            raise AssertionError("default IM streaming must not use wait_for")

        monkeypatch.setattr(gateway_module.asyncio, "wait_for", fail_wait_for)

        async def agent_handler_stream(session, text):
            yield {"type": "text_delta", "content": "长任务结果"}
            yield {"type": "done"}

        gateway = MessageGateway(session_manager=MagicMock())
        gateway.agent_handler_stream = agent_handler_stream
        session = create_test_session(channel="feishu:writer", chat_id="chat-1", user_id="user-1")
        message = create_channel_message(
            channel="feishu:writer",
            chat_id="chat-1",
            user_id="user-1",
            text="写一篇长文章",
        )
        adapter = SimpleNamespace(
            stream_token=AsyncMock(),
            finalize_stream=AsyncMock(return_value=True),
            _make_session_key=lambda chat_id, thread_id: chat_id,
            _streaming_buffers={},
        )

        response, streamed_ok = await gateway._call_agent_streaming(
            session,
            "写一篇长文章",
            message,
            adapter,
        )

        assert response == "长任务结果"
        assert streamed_ok is True
        adapter.stream_token.assert_awaited_once()
        adapter.finalize_stream.assert_awaited_once()

