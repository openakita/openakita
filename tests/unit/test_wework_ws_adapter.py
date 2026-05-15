"""
企业微信 WebSocket 长连接适配器 单元测试

覆盖:
- 帧路由 (_route_frame)
- 消息解析 (_parse_content / _handle_msg_callback)
- 事件处理 (_handle_event_callback)
- 流式回复 (send_message → stream)
- 文件解密 (_decrypt_file)
- response_url 回退
- 消息去重
- 连接生命周期
"""

import asyncio
import base64
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openakita.channels.adapters.wework_ws import (
    CMD_CALLBACK,
    CMD_EVENT_CALLBACK,
    CMD_HEARTBEAT,
    CMD_RESPONSE,
    CMD_SEND_MSG,
    CMD_SUBSCRIBE,
    WeWorkWsAdapter,
    WeWorkWsConfig,
    _decrypt_file,
    _generate_req_id,
)
from openakita.channels.types import MediaFile, OutgoingMessage


# ==================== Fixtures ====================


@pytest.fixture
def adapter():
    """Create adapter instance without starting."""
    a = WeWorkWsAdapter(
        bot_id="test_bot_id",
        secret="test_secret",
        ws_url="wss://test.example.com",
    )
    return a


@pytest.fixture
def connected_adapter(adapter):
    """Create adapter with a mocked WebSocket."""
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.close = AsyncMock()
    adapter._ws = ws
    adapter._running = True
    adapter._authenticated.set()
    return adapter


# ==================== req_id generation ====================


class TestReqIdGeneration:
    def test_format(self):
        rid = _generate_req_id("test_prefix")
        parts = rid.split("_", 2)
        assert parts[0] == "test"
        assert parts[1] == "prefix"

    def test_uniqueness(self):
        ids = {_generate_req_id("x") for _ in range(100)}
        assert len(ids) == 100

    def test_prefix_preserved(self):
        rid = _generate_req_id(CMD_SUBSCRIBE)
        assert rid.startswith(CMD_SUBSCRIBE + "_")


# ==================== Config ====================


class TestConfig:
    def test_defaults(self):
        cfg = WeWorkWsConfig(bot_id="b", secret="s")
        assert cfg.ws_url == "wss://openws.work.weixin.qq.com"
        assert cfg.heartbeat_interval == 30.0
        assert cfg.max_missed_pong == 2
        assert cfg.max_reconnect_attempts == -1
        assert cfg.reconnect_base_delay == 1.0
        assert cfg.reconnect_max_delay == 30.0
        assert cfg.reply_ack_timeout == 15.0

    def test_custom(self):
        cfg = WeWorkWsConfig(
            bot_id="b",
            secret="s",
            ws_url="wss://custom",
            heartbeat_interval=10.0,
            max_reconnect_attempts=5,
        )
        assert cfg.ws_url == "wss://custom"
        assert cfg.heartbeat_interval == 10.0
        assert cfg.max_reconnect_attempts == 5


# ==================== Frame routing ====================


class TestFrameRouting:
    @pytest.mark.asyncio
    async def test_msg_callback_dispatched(self, connected_adapter):
        adapter = connected_adapter
        adapter._handle_msg_callback = AsyncMock()
        frame = {
            "cmd": CMD_CALLBACK,
            "headers": {"req_id": "cb_123"},
            "body": {"msgid": "m1", "msgtype": "text", "text": {"content": "hi"}},
        }
        await adapter._route_frame(frame)
        await asyncio.sleep(0.05)
        adapter._handle_msg_callback.assert_called_once()

    @pytest.mark.asyncio
    async def test_event_callback_dispatched(self, connected_adapter):
        adapter = connected_adapter
        adapter._handle_event_callback = AsyncMock()
        frame = {
            "cmd": CMD_EVENT_CALLBACK,
            "headers": {"req_id": "ev_123"},
            "body": {"event": {"eventtype": "enter_chat"}},
        }
        await adapter._route_frame(frame)
        await asyncio.sleep(0.05)
        adapter._handle_event_callback.assert_called_once()

    @pytest.mark.asyncio
    async def test_auth_response_success(self, adapter):
        adapter._authenticated.clear()
        req_id = f"{CMD_SUBSCRIBE}_1234_abcd"
        frame = {"headers": {"req_id": req_id}, "errcode": 0, "errmsg": "ok"}
        await adapter._route_frame(frame)
        assert adapter._authenticated.is_set()

    @pytest.mark.asyncio
    async def test_auth_response_failure(self, adapter):
        adapter._authenticated.clear()
        req_id = f"{CMD_SUBSCRIBE}_1234_abcd"
        frame = {"headers": {"req_id": req_id}, "errcode": 40001, "errmsg": "bad"}
        await adapter._route_frame(frame)
        assert not adapter._authenticated.is_set()

    @pytest.mark.asyncio
    async def test_heartbeat_response_resets_pong(self, adapter):
        adapter._missed_pong = 2
        req_id = f"{CMD_HEARTBEAT}_1234_abcd"
        frame = {"headers": {"req_id": req_id}, "errcode": 0}
        await adapter._route_frame(frame)
        assert adapter._missed_pong == 0

    @pytest.mark.asyncio
    async def test_reply_ack_resolves_future(self, adapter):
        loop = asyncio.get_event_loop()
        fut = loop.create_future()
        adapter._pending_acks["req_abc"] = fut
        frame = {"headers": {"req_id": "req_abc"}, "errcode": 0}
        await adapter._route_frame(frame)
        assert fut.done()
        assert fut.result() == frame


# ==================== Message parsing ====================


class TestMessageParsing:
    def test_text_message(self, adapter):
        body = {"msgtype": "text", "text": {"content": "hello world"}}
        content, media = adapter._parse_content(body, "text")
        assert content.text == "hello world"
        assert len(media) == 0

    def test_image_message(self, adapter):
        body = {
            "msgtype": "image",
            "image": {"url": "https://img.example.com/a.jpg", "aeskey": "abc123"},
        }
        content, media = adapter._parse_content(body, "image")
        assert len(content.images) == 1
        assert content.images[0].url == "https://img.example.com/a.jpg"
        assert content.images[0].extra["aeskey"] == "abc123"
        assert len(media) == 1

    def test_mixed_message(self, adapter):
        body = {
            "msgtype": "mixed",
            "mixed": {
                "msg_item": [
                    {"msgtype": "text", "text": {"content": "caption"}},
                    {"msgtype": "image", "image": {"url": "https://a.jpg"}},
                    {"msgtype": "text", "text": {"content": "end"}},
                ]
            },
        }
        content, media = adapter._parse_content(body, "mixed")
        assert content.text == "caption\nend"
        assert len(content.images) == 1
        assert len(media) == 1

    def test_voice_message(self, adapter):
        body = {"msgtype": "voice", "voice": {"content": "你好"}}
        content, media = adapter._parse_content(body, "voice")
        assert content.text == "你好"
        assert len(media) == 0

    def test_file_message(self, adapter):
        body = {
            "msgtype": "file",
            "file": {
                "url": "https://dl.example.com/f.pdf",
                "aeskey": "key123",
                "filename": "report.pdf",
            },
        }
        content, media = adapter._parse_content(body, "file")
        assert len(content.files) == 1
        assert content.files[0].filename == "report.pdf"
        assert content.files[0].extra["aeskey"] == "key123"
        assert len(media) == 1

    def test_unknown_type(self, adapter):
        body = {"msgtype": "sticker"}
        content, media = adapter._parse_content(body, "sticker")
        assert "不支持" in content.text
        assert len(media) == 0


# ==================== Full message handling ====================


class TestMessageHandling:
    @pytest.mark.asyncio
    async def test_emits_unified_message(self, connected_adapter):
        adapter = connected_adapter
        received = []
        adapter._message_callback = AsyncMock(side_effect=lambda m: received.append(m))

        frame = {
            "cmd": CMD_CALLBACK,
            "headers": {"req_id": "req_001"},
            "body": {
                "msgid": "msg_001",
                "msgtype": "text",
                "text": {"content": "test content"},
                "chattype": "group",
                "chatid": "chat_abc",
                "from": {"userid": "user_1"},
                "response_url": "https://resp.example.com",
            },
        }
        await adapter._handle_msg_callback(frame)
        assert len(received) == 1
        msg = received[0]
        assert msg.channel == "wework_ws"
        assert msg.text == "test content"
        assert msg.chat_type == "group"
        assert msg.chat_id == "chat_abc"
        assert msg.channel_user_id == "user_1"
        assert msg.is_mentioned is True

    @pytest.mark.asyncio
    async def test_response_url_cached(self, connected_adapter):
        adapter = connected_adapter
        adapter._message_callback = AsyncMock()

        frame = {
            "cmd": CMD_CALLBACK,
            "headers": {"req_id": "req_002"},
            "body": {
                "msgid": "msg_002",
                "msgtype": "text",
                "text": {"content": "hi"},
                "chattype": "single",
                "from": {"userid": "u1"},
                "response_url": "https://resp.example.com/url",
            },
        }
        await adapter._handle_msg_callback(frame)
        assert adapter._response_urls["req_002"] == "https://resp.example.com/url"


# ==================== Message dedup ====================


class TestMessageDedup:
    @pytest.mark.asyncio
    async def test_duplicate_ignored(self, connected_adapter):
        adapter = connected_adapter
        count = 0

        async def mock_cb(m):
            nonlocal count
            count += 1

        adapter._message_callback = mock_cb

        frame = {
            "cmd": CMD_CALLBACK,
            "headers": {"req_id": "req_d1"},
            "body": {
                "msgid": "dup_msg_1",
                "msgtype": "text",
                "text": {"content": "hi"},
                "chattype": "single",
                "from": {"userid": "u1"},
            },
        }
        await adapter._handle_msg_callback(frame)
        await adapter._handle_msg_callback(frame)
        assert count == 1

    @pytest.mark.asyncio
    async def test_different_msgid_not_deduped(self, connected_adapter):
        adapter = connected_adapter
        count = 0

        async def mock_cb(m):
            nonlocal count
            count += 1

        adapter._message_callback = mock_cb

        for i in range(3):
            frame = {
                "cmd": CMD_CALLBACK,
                "headers": {"req_id": f"req_{i}"},
                "body": {
                    "msgid": f"msg_{i}",
                    "msgtype": "text",
                    "text": {"content": f"m{i}"},
                    "chattype": "single",
                    "from": {"userid": "u1"},
                },
            }
            await adapter._handle_msg_callback(frame)
        assert count == 3

    @pytest.mark.asyncio
    async def test_lru_eviction(self, connected_adapter):
        adapter = connected_adapter
        adapter._seen_msg_ids_max = 5
        adapter._message_callback = AsyncMock()

        for i in range(7):
            frame = {
                "cmd": CMD_CALLBACK,
                "headers": {"req_id": f"req_{i}"},
                "body": {
                    "msgid": f"msg_{i}",
                    "msgtype": "text",
                    "text": {"content": str(i)},
                    "chattype": "single",
                    "from": {"userid": "u1"},
                },
            }
            await adapter._handle_msg_callback(frame)

        assert len(adapter._seen_msg_ids) == 5
        # oldest two (msg_0, msg_1) should be evicted
        assert "msg_0" not in adapter._seen_msg_ids
        assert "msg_1" not in adapter._seen_msg_ids
        assert "msg_6" in adapter._seen_msg_ids


# ==================== Event handling ====================


class TestEventHandling:
    @pytest.mark.asyncio
    async def test_enter_chat_event(self, connected_adapter):
        adapter = connected_adapter
        events = []
        adapter._event_callback = AsyncMock(
            side_effect=lambda t, d: events.append((t, d))
        )

        frame = {
            "cmd": CMD_EVENT_CALLBACK,
            "headers": {"req_id": "ev_001"},
            "body": {
                "msgid": "e1",
                "msgtype": "event",
                "aibotid": "bot1",
                "chatid": "chat_1",
                "chattype": "single",
                "from": {"userid": "u1"},
                "event": {"eventtype": "enter_chat"},
            },
        }
        await adapter._handle_event_callback(frame)
        assert len(events) == 1
        assert events[0][0] == "enter_chat"
        assert events[0][1]["userid"] == "u1"

    @pytest.mark.asyncio
    async def test_template_card_event(self, connected_adapter):
        adapter = connected_adapter
        events = []
        adapter._event_callback = AsyncMock(
            side_effect=lambda t, d: events.append((t, d))
        )

        frame = {
            "cmd": CMD_EVENT_CALLBACK,
            "headers": {"req_id": "ev_002"},
            "body": {
                "from": {"userid": "u2"},
                "chatid": "c2",
                "event": {
                    "eventtype": "template_card_event",
                    "event_key": "btn_1",
                    "task_id": "task_001",
                },
            },
        }
        await adapter._handle_event_callback(frame)
        assert len(events) == 1
        assert events[0][0] == "template_card_event"
        assert events[0][1]["event_key"] == "btn_1"

    @pytest.mark.asyncio
    async def test_feedback_event(self, connected_adapter):
        adapter = connected_adapter
        events = []
        adapter._event_callback = AsyncMock(
            side_effect=lambda t, d: events.append((t, d))
        )

        frame = {
            "cmd": CMD_EVENT_CALLBACK,
            "headers": {"req_id": "ev_003"},
            "body": {
                "from": {"userid": "u3"},
                "chatid": "c3",
                "event": {"eventtype": "feedback_event"},
            },
        }
        await adapter._handle_event_callback(frame)
        assert len(events) == 1
        assert events[0][0] == "feedback_event"


# ==================== Stream reply ====================


class TestStreamReply:
    @pytest.mark.asyncio
    async def test_simple_text_reply(self, connected_adapter):
        adapter = connected_adapter
        sent_frames = []

        async def mock_send(frame_json):
            sent_frames.append(json.loads(frame_json))

        adapter._ws.send = mock_send

        # Simulate ack for each send
        async def auto_ack():
            await asyncio.sleep(0.01)
            for req_id, fut in list(adapter._pending_acks.items()):
                if not fut.done():
                    fut.set_result({"headers": {"req_id": req_id}, "errcode": 0})

        msg = OutgoingMessage.text("chat_1", "Hello!")
        msg.metadata = {"req_id": "req_reply_1"}

        ack_task = asyncio.create_task(auto_ack())
        result = await adapter.send_message(msg)
        await ack_task

        assert len(sent_frames) == 1
        f = sent_frames[0]
        assert f["cmd"] == CMD_RESPONSE
        assert f["headers"]["req_id"] == "req_reply_1"
        assert f["body"]["msgtype"] == "stream"
        assert f["body"]["stream"]["content"] == "Hello!"
        assert f["body"]["stream"]["finish"] is True
        assert result  # non-empty stream_id

    @pytest.mark.asyncio
    async def test_active_push_message(self, connected_adapter):
        adapter = connected_adapter
        sent_frames = []

        async def mock_send(frame_json):
            sent_frames.append(json.loads(frame_json))

        adapter._ws.send = mock_send

        async def auto_ack():
            await asyncio.sleep(0.01)
            for req_id, fut in list(adapter._pending_acks.items()):
                if not fut.done():
                    fut.set_result({"headers": {"req_id": req_id}, "errcode": 0})

        msg = OutgoingMessage.text("chat_push", "主动推送")
        msg.metadata = {}  # no req_id → active push

        ack_task = asyncio.create_task(auto_ack())
        await adapter.send_message(msg)
        await ack_task

        assert len(sent_frames) == 1
        f = sent_frames[0]
        assert f["cmd"] == CMD_SEND_MSG
        assert f["body"]["chatid"] == "chat_push"
        assert f["body"]["msgtype"] == "markdown"
        assert f["body"]["markdown"]["content"] == "主动推送"


# ==================== Thinking Indicator ====================


class TestThinkingIndicator:
    """Test the pre-send 'thinking' stream frame and stream_id reuse."""

    @pytest.mark.asyncio
    async def test_thinking_indicator_sends_frame(self, connected_adapter):
        """When thinking_indicator is enabled, _handle_msg_callback should
        send an immediate 'thinking' stream frame after emitting the message."""
        adapter = connected_adapter
        sent_frames = []

        async def mock_send(frame_json):
            sent_frames.append(json.loads(frame_json))

        adapter._ws.send = mock_send

        async def auto_ack():
            for _ in range(20):
                await asyncio.sleep(0.01)
                for req_id, fut in list(adapter._pending_acks.items()):
                    if not fut.done():
                        fut.set_result({"headers": {"req_id": req_id}, "errcode": 0})

        ack_task = asyncio.create_task(auto_ack())

        frame = {
            "cmd": CMD_CALLBACK,
            "headers": {"req_id": "req_think_1"},
            "body": {
                "msgid": "think_msg_1",
                "msgtype": "text",
                "chattype": "single",
                "from": {"userid": "user1"},
                "chatid": "chat1",
                "text": {"content": "hello"},
            },
        }

        with patch.object(adapter, "_emit_message", new_callable=AsyncMock):
            with patch("openakita.config.settings") as mock_settings:
                mock_settings.wework_ws_thinking_indicator = True
                await adapter._route_frame(frame)
                await asyncio.sleep(0.1)

        await ack_task

        thinking_frames = [
            f for f in sent_frames
            if f.get("cmd") == CMD_RESPONSE
            and f.get("body", {}).get("stream", {}).get("finish") is False
            and "<think>" in (f.get("body", {}).get("stream", {}).get("content") or "")
        ]
        assert len(thinking_frames) == 1
        assert "req_think_1" in adapter._pre_streams

    @pytest.mark.asyncio
    async def test_thinking_indicator_disabled(self, connected_adapter):
        """When thinking_indicator is disabled, no thinking frame is sent."""
        adapter = connected_adapter
        sent_frames = []

        async def mock_send(frame_json):
            sent_frames.append(json.loads(frame_json))

        adapter._ws.send = mock_send

        frame = {
            "cmd": CMD_CALLBACK,
            "headers": {"req_id": "req_think_2"},
            "body": {
                "msgid": "think_msg_2",
                "msgtype": "text",
                "chattype": "single",
                "from": {"userid": "user1"},
                "chatid": "chat1",
                "text": {"content": "hi"},
            },
        }

        with patch.object(adapter, "_emit_message", new_callable=AsyncMock):
            with patch("openakita.config.settings") as mock_settings:
                mock_settings.wework_ws_thinking_indicator = False
                await adapter._route_frame(frame)
                await asyncio.sleep(0.1)

        assert len(sent_frames) == 0
        assert "req_think_2" not in adapter._pre_streams

    @pytest.mark.asyncio
    async def test_stream_reply_reuses_pre_stream_id(self, connected_adapter):
        """_send_stream_reply should reuse a pre-created stream_id."""
        adapter = connected_adapter
        sent_frames = []

        async def mock_send(frame_json):
            sent_frames.append(json.loads(frame_json))

        adapter._ws.send = mock_send

        async def auto_ack():
            for _ in range(10):
                await asyncio.sleep(0.01)
                for req_id, fut in list(adapter._pending_acks.items()):
                    if not fut.done():
                        fut.set_result({"headers": {"req_id": req_id}, "errcode": 0})

        pre_stream_id = "pre_existing_stream_abc"
        adapter._pre_streams["req_reuse_1"] = pre_stream_id

        msg = OutgoingMessage.text("chat_1", "Hello reply!")
        msg.metadata = {"req_id": "req_reuse_1"}

        ack_task = asyncio.create_task(auto_ack())
        result = await adapter.send_message(msg)
        await ack_task

        assert result == pre_stream_id
        assert "req_reuse_1" not in adapter._pre_streams

        f = sent_frames[0]
        assert f["body"]["stream"]["id"] == pre_stream_id

    @pytest.mark.asyncio
    async def test_stream_reply_new_id_when_no_pre_stream(self, connected_adapter):
        """Without a pre-stream, _send_stream_reply generates a new stream_id."""
        adapter = connected_adapter
        sent_frames = []

        async def mock_send(frame_json):
            sent_frames.append(json.loads(frame_json))

        adapter._ws.send = mock_send

        async def auto_ack():
            for _ in range(10):
                await asyncio.sleep(0.01)
                for req_id, fut in list(adapter._pending_acks.items()):
                    if not fut.done():
                        fut.set_result({"headers": {"req_id": req_id}, "errcode": 0})

        msg = OutgoingMessage.text("chat_1", "No pre stream")
        msg.metadata = {"req_id": "req_new_1"}

        ack_task = asyncio.create_task(auto_ack())
        result = await adapter.send_message(msg)
        await ack_task

        assert result  # non-empty stream_id
        f = sent_frames[0]
        assert f["body"]["stream"]["id"] == result

    @pytest.mark.asyncio
    async def test_stream_split_when_images_with_pre_stream(self, connected_adapter):
        """When images are queued and a thinking-indicator stream exists,
        the old stream should be closed and a new stream_id used for the image reply."""
        adapter = connected_adapter
        sent_frames = []

        async def mock_send(frame_json):
            sent_frames.append(json.loads(frame_json))

        adapter._ws.send = mock_send

        async def auto_ack():
            for _ in range(20):
                await asyncio.sleep(0.01)
                for req_id, fut in list(adapter._pending_acks.items()):
                    if not fut.done():
                        fut.set_result({"headers": {"req_id": req_id}, "errcode": 0})

        pre_stream_id = "thinking_stream_xyz"
        req_id = "req_split_1"
        adapter._pre_streams[req_id] = pre_stream_id

        fake_b64 = "AAAA"
        fake_md5 = "d41d8cd98f00b204e9800998ecf8427e"
        adapter._pending_image_items[req_id] = [
            {"msgtype": "image", "image": {"base64": fake_b64, "md5": fake_md5}}
        ]

        msg = OutgoingMessage.text("chat_1", "Here is the image")
        msg.metadata = {"req_id": req_id}

        ack_task = asyncio.create_task(auto_ack())
        result = await adapter.send_message(msg)
        await ack_task

        assert result != pre_stream_id, "Should use a new stream_id, not the thinking one"
        assert req_id not in adapter._pre_streams
        assert req_id not in adapter._pending_image_items

        close_frame = sent_frames[0]
        assert close_frame["body"]["stream"]["id"] == pre_stream_id
        assert close_frame["body"]["stream"]["finish"] is True
        assert close_frame["body"]["stream"]["content"] == ""

        reply_frame = sent_frames[1]
        assert reply_frame["body"]["stream"]["id"] == result
        assert reply_frame["body"]["stream"]["finish"] is True
        assert reply_frame["body"]["stream"]["content"] == "Here is the image"
        assert len(reply_frame["body"]["stream"]["msg_item"]) == 1
        assert reply_frame["body"]["stream"]["msg_item"][0]["msgtype"] == "image"

    @pytest.mark.asyncio
    async def test_no_stream_split_without_images(self, connected_adapter):
        """When no images are present, the pre_stream_id is reused as-is (no split)."""
        adapter = connected_adapter
        sent_frames = []

        async def mock_send(frame_json):
            sent_frames.append(json.loads(frame_json))

        adapter._ws.send = mock_send

        async def auto_ack():
            for _ in range(10):
                await asyncio.sleep(0.01)
                for req_id, fut in list(adapter._pending_acks.items()):
                    if not fut.done():
                        fut.set_result({"headers": {"req_id": req_id}, "errcode": 0})

        pre_stream_id = "thinking_stream_no_img"
        req_id = "req_no_split_1"
        adapter._pre_streams[req_id] = pre_stream_id

        msg = OutgoingMessage.text("chat_1", "Text only reply")
        msg.metadata = {"req_id": req_id}

        ack_task = asyncio.create_task(auto_ack())
        result = await adapter.send_message(msg)
        await ack_task

        assert result == pre_stream_id, "Without images, should reuse pre_stream_id"
        assert len(sent_frames) == 1
        assert sent_frames[0]["body"]["stream"]["id"] == pre_stream_id


# ==================== File decryption ====================


class TestFileDecryption:
    def test_decrypt_file_roundtrip(self):
        """Test AES-256-CBC encryption and decryption."""
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        key = b"\x01" * 32
        iv = key[:16]
        plaintext = b"Hello, WeWork WS file decryption test!"

        # PKCS#7 pad to 32-byte block
        block_size = 32
        pad_len = block_size - (len(plaintext) % block_size)
        padded = plaintext + bytes([pad_len] * pad_len)

        # encrypt
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        encryptor = cipher.encryptor()
        encrypted = encryptor.update(padded) + encryptor.finalize()

        aes_key_b64 = base64.b64encode(key).decode()
        decrypted = _decrypt_file(encrypted, aes_key_b64)
        assert decrypted == plaintext

    def test_invalid_padding(self):
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        key = b"\x02" * 32
        iv = key[:16]

        # create data with invalid padding (last byte = 0)
        bad_data = b"\x00" * 32
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        encryptor = cipher.encryptor()
        encrypted = encryptor.update(bad_data) + encryptor.finalize()

        aes_key_b64 = base64.b64encode(key).decode()
        with pytest.raises(ValueError, match="padding"):
            _decrypt_file(encrypted, aes_key_b64)

    def test_invalid_key_length(self):
        short_key = base64.b64encode(b"\x01" * 16).decode()
        with pytest.raises(ValueError, match="32 bytes"):
            _decrypt_file(b"data", short_key)


# ==================== Download media ====================


class TestDownloadMedia:
    @pytest.mark.asyncio
    async def test_download_plain_file(self, connected_adapter, tmp_path):
        adapter = connected_adapter
        adapter.media_dir = tmp_path

        media = MediaFile.create(
            filename="test.txt",
            mime_type="text/plain",
            url="https://dl.example.com/test.txt",
        )

        from openakita.channels.adapters.wework_ws import _import_httpx
        _import_httpx()

        mock_resp = MagicMock()
        mock_resp.content = b"file content"
        mock_resp.status_code = 200
        mock_resp.headers = {}
        mock_resp.raise_for_status = MagicMock()

        with patch("openakita.channels.adapters.wework_ws.httpx") as mock_httpx:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_httpx.AsyncClient.return_value = mock_client
            mock_httpx.Timeout = MagicMock()

            path = await adapter.download_media(media)

        assert path.exists()
        assert path.read_bytes() == b"file content"
        assert media.status.value == "ready"

    @pytest.mark.asyncio
    async def test_download_with_aes_decryption(self, connected_adapter, tmp_path):
        adapter = connected_adapter
        adapter.media_dir = tmp_path

        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        key = b"\x03" * 32
        iv = key[:16]
        plaintext = b"decrypted image data"
        block_size = 32
        pad_len = block_size - (len(plaintext) % block_size)
        padded = plaintext + bytes([pad_len] * pad_len)
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        encryptor = cipher.encryptor()
        encrypted = encryptor.update(padded) + encryptor.finalize()

        media = MediaFile.create(
            filename="image.jpg",
            mime_type="image/jpeg",
            url="https://dl.example.com/enc.jpg",
        )
        media.extra = {"aeskey": base64.b64encode(key).decode()}

        from openakita.channels.adapters.wework_ws import _import_httpx
        _import_httpx()

        mock_resp = MagicMock()
        mock_resp.content = encrypted
        mock_resp.status_code = 200
        mock_resp.headers = {"content-disposition": "filename*=UTF-8''photo%E5%9B%BE.jpg"}
        mock_resp.raise_for_status = MagicMock()

        with patch("openakita.channels.adapters.wework_ws.httpx") as mock_httpx:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_httpx.AsyncClient.return_value = mock_client
            mock_httpx.Timeout = MagicMock()

            path = await adapter.download_media(media)

        assert path.exists()
        assert path.read_bytes() == plaintext
        assert media.filename == "photo图.jpg"


# ==================== response_url fallback ====================


class TestResponseUrlFallback:
    @pytest.mark.asyncio
    async def test_fallback_success(self, connected_adapter):
        adapter = connected_adapter
        adapter._response_urls["req_fb_1"] = "https://resp.example.com/fb"

        from openakita.channels.adapters.wework_ws import _import_httpx
        _import_httpx()

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("openakita.channels.adapters.wework_ws.httpx") as mock_httpx:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_httpx.AsyncClient.return_value = mock_client
            mock_httpx.Timeout = MagicMock()

            result = await adapter._response_url_fallback("req_fb_1", "fallback text")

        assert result is True

    @pytest.mark.asyncio
    async def test_fallback_no_url(self, connected_adapter):
        adapter = connected_adapter
        result = await adapter._response_url_fallback("no_such_req", "text")
        assert result is False


# ==================== Pending ack rejection ====================


class TestPendingAckRejection:
    def test_reject_all_pending(self, adapter):
        loop = asyncio.new_event_loop()
        try:
            fut1 = loop.create_future()
            fut2 = loop.create_future()
            adapter._pending_acks = {"a": fut1, "b": fut2}
            adapter._reply_locks = {"a": asyncio.Lock()}
            adapter._pre_streams = {"req_1": "stream_abc"}
            adapter._pending_image_items = {"req_1": [{"msgtype": "image"}]}
            adapter._reject_all_pending("test reason")
            assert fut1.done()
            assert fut2.done()
            assert len(adapter._pending_acks) == 0
            assert len(adapter._reply_locks) == 0
            assert len(adapter._pre_streams) == 0
            assert len(adapter._pending_image_items) == 0
        finally:
            loop.close()


# ==================== Adapter properties ====================


class TestAdapterProperties:
    def test_channel_name(self, adapter):
        assert adapter.channel_name == "wework_ws"

    def test_supports_streaming(self, adapter):
        assert adapter.supports_streaming is True

    def test_upload_media_requires_connection(self, adapter):
        with pytest.raises(ConnectionError, match="WebSocket not connected"):
            asyncio.run(
                adapter.upload_media(Path("test.jpg"), "image/jpeg")
            )


# ==================== Lifecycle ====================


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_stop_cleans_up(self, connected_adapter):
        adapter = connected_adapter
        adapter._connection_task = asyncio.create_task(asyncio.sleep(999))
        adapter._heartbeat_task = asyncio.create_task(asyncio.sleep(999))

        loop = asyncio.get_event_loop()
        adapter._pending_acks["test"] = loop.create_future()

        await adapter.stop()
        assert adapter._running is False
        assert adapter._ws is None
        assert len(adapter._pending_acks) == 0
