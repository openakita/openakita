"""L3 Integration Tests: IM channel adapter protocol compliance."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

from openakita.channels.base import ChannelAdapter
from openakita.channels.types import MessageContent, OutgoingMessage


class TestChannelAdapterInterface:
    """Verify the base adapter protocol defines all required methods."""

    def test_abstract_methods_defined(self):
        import inspect

        abstract_methods = set()
        for name, method in inspect.getmembers(ChannelAdapter):
            if getattr(method, "__isabstractmethod__", False):
                abstract_methods.add(name)

        required = {"start", "stop", "send_message", "download_media", "upload_media"}
        assert required.issubset(abstract_methods)

    def test_channel_name_attribute(self):
        assert hasattr(ChannelAdapter, "channel_name")

    def test_convenience_methods_exist(self):
        assert hasattr(ChannelAdapter, "send_text")
        assert hasattr(ChannelAdapter, "send_image")


class TestTelegramAdapterInit:
    """Test TelegramAdapter can be instantiated with config."""

    def test_import_succeeds(self):
        from openakita.channels.adapters.telegram import TelegramAdapter

        assert TelegramAdapter.channel_name == "telegram"

    def test_init_with_token(self):
        from openakita.channels.adapters.telegram import TelegramAdapter

        adapter = TelegramAdapter(
            bot_token="123:TEST_TOKEN",
            require_pairing=False,
        )
        assert adapter.channel_name == "telegram"


class TestFeishuAdapterInit:
    def test_import_succeeds(self):
        from openakita.channels.adapters.feishu import FeishuAdapter

        assert FeishuAdapter.channel_name == "feishu"

    def test_init_with_credentials(self):
        from openakita.channels.adapters.feishu import FeishuAdapter

        adapter = FeishuAdapter(
            app_id="test-app-id",
            app_secret="test-secret",
        )
        assert adapter.channel_name == "feishu"


class TestDingTalkAdapterInit:
    def test_import_succeeds(self):
        from openakita.channels.adapters.dingtalk import DingTalkAdapter

        assert DingTalkAdapter.channel_name == "dingtalk"

    def test_init_with_credentials(self):
        from openakita.channels.adapters.dingtalk import DingTalkAdapter

        adapter = DingTalkAdapter(
            app_key="test-key",
            app_secret="test-secret",
        )
        assert adapter.channel_name == "dingtalk"


class TestOneBotAdapterInit:
    def test_import_succeeds(self):
        from openakita.channels.adapters.onebot import OneBotAdapter

        assert OneBotAdapter.channel_name == "onebot"

    def test_init_defaults(self):
        from openakita.channels.adapters.onebot import OneBotAdapter

        adapter = OneBotAdapter()
        assert adapter.channel_name == "onebot"
        assert adapter.config.mode == "reverse"

    def test_init_forward_mode(self):
        from openakita.channels.adapters.onebot import OneBotAdapter

        adapter = OneBotAdapter(mode="forward", ws_url="ws://localhost:9999")
        assert adapter.config.mode == "forward"
        assert adapter.config.ws_url == "ws://localhost:9999"

    def test_init_reverse_mode(self):
        from openakita.channels.adapters.onebot import OneBotAdapter

        adapter = OneBotAdapter(mode="reverse", reverse_port=7700, access_token="test-token")
        assert adapter.config.mode == "reverse"
        assert adapter.config.reverse_port == 7700
        assert adapter.config.access_token == "test-token"

    def test_cq_code_entity_decoding(self):
        from openakita.channels.adapters.onebot import OneBotAdapter

        adapter = OneBotAdapter()
        segments = adapter._parse_cq_code("hello&#44; world&#91;test&#93;&amp;ok")
        assert segments[0]["data"]["text"] == "hello, world[test]&ok"

    def test_message_dedup(self):
        from openakita.channels.adapters.onebot import OneBotAdapter

        adapter = OneBotAdapter()
        assert "123" not in adapter._seen_message_ids
        adapter._seen_message_ids["123"] = None
        assert "123" in adapter._seen_message_ids


class TestWeWorkAdapterInit:
    def test_import_succeeds(self):
        from openakita.channels.adapters.wework_bot import WeWorkBotAdapter

        assert WeWorkBotAdapter.channel_name == "wework"

    def test_init_with_credentials(self):
        from openakita.channels.adapters.wework_bot import WeWorkBotAdapter

        adapter = WeWorkBotAdapter(
            corp_id="test-corp",
            token="test-token",
            encoding_aes_key="test-aes-key-" + "x" * 30,
        )
        assert adapter.channel_name == "wework"


class TestQQBotAdapterInit:
    def test_import_succeeds(self):
        from openakita.channels.adapters.qq_official import QQBotAdapter

        assert QQBotAdapter.channel_name == "qqbot"

    def test_init_with_credentials(self):
        from openakita.channels.adapters.qq_official import QQBotAdapter

        adapter = QQBotAdapter(
            app_id="test-app-id",
            app_secret="test-secret",
        )
        assert adapter.channel_name == "qqbot"


class TestOutgoingMessageConstruction:
    def test_text_message(self):
        msg = OutgoingMessage(
            chat_id="123",
            content=MessageContent(text="Hello"),
        )
        assert msg.chat_id == "123"
        assert msg.content.text == "Hello"

    def test_message_with_reply(self):
        msg = OutgoingMessage(
            chat_id="123",
            content=MessageContent(text="Reply"),
            reply_to="msg-456",
        )
        assert msg.reply_to == "msg-456"

    def test_silent_message(self):
        msg = OutgoingMessage(
            chat_id="123",
            content=MessageContent(text="Shh"),
            silent=True,
        )
        assert msg.silent is True
