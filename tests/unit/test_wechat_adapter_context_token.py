from __future__ import annotations

import json
import time

import pytest

from openakita.channels.adapters.wechat import (
    CONTEXT_TOKEN_STALE_S,
    WeChatAdapter,
    _ContextTokenEntry,
)
from openakita.channels.base import ChannelDeliveryUnavailable


def _make_adapter(tmp_path) -> WeChatAdapter:
    adapter = WeChatAdapter(token="token", channel_name="wechat:test", bot_id="test")
    adapter._sync_buf_dir = tmp_path
    return adapter


def test_stale_context_token_is_not_reused(tmp_path):
    adapter = _make_adapter(tmp_path)
    adapter._context_tokens["chat-1"] = _ContextTokenEntry(
        token="old-token",
        captured_at=time.time() - CONTEXT_TOKEN_STALE_S - 1,
    )

    assert adapter._resolve_context_token("chat-1", {"context_token": "old-token"}) == ""

    entry = adapter._context_tokens["chat-1"]
    assert entry.invalidated
    assert entry.invalid_reason == "context_token stale"


def test_legacy_context_token_file_loads_as_invalidated(tmp_path):
    adapter = _make_adapter(tmp_path)
    adapter._context_tokens_path().write_text(
        json.dumps({"chat-1": "legacy-token"}),
        encoding="utf-8",
    )

    adapter._load_context_tokens()

    assert adapter._resolve_context_token("chat-1", {"context_token": "legacy-token"}) == ""
    entry = adapter._context_tokens["chat-1"]
    assert entry.invalidated
    assert entry.invalid_reason == "legacy token without capture timestamp"


@pytest.mark.asyncio
async def test_send_text_ret_minus_two_invalidates_context_token(tmp_path, monkeypatch):
    adapter = _make_adapter(tmp_path)
    adapter._context_tokens["chat-1"] = _ContextTokenEntry(
        token="fresh-token",
        captured_at=time.time(),
    )

    async def fake_api_post(endpoint, body, *, timeout_s=None):
        assert endpoint == "ilink/bot/sendmessage"
        return {"ret": -2, "errmsg": "context rejected"}

    async def no_wait(chat_id):
        return None

    monkeypatch.setattr(adapter, "_api_post", fake_api_post)
    monkeypatch.setattr(adapter, "_rate_limit_wait", no_wait)

    with pytest.raises(ChannelDeliveryUnavailable) as exc_info:
        await adapter._send_text("chat-1", "hello", "fresh-token")

    assert exc_info.value.channel == "wechat:test"
    assert exc_info.value.chat_id == "chat-1"
    entry = adapter._context_tokens["chat-1"]
    assert entry.invalidated
    assert "ret=-2" in entry.invalid_reason
