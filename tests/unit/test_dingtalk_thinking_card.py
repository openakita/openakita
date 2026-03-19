"""Unit tests for DingTalk adapter thinking card (send_typing / clear_typing).

Validates all paths identified in the plan:
- Normal path: card created -> consumed by send_message
- Error path: card consumed by _send_error -> send_message
- Double-failure: card cleaned up by clear_typing
- Interrupt: card recreated after consumption, cleaned by clear_typing
- Split message: first fragment consumes card, rest sent normally
- Fast response: no card created
- Media message: card updated to "处理完成", media sent normally
- AI Card: upgrade path with fallback to StandardCard
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from openakita.channels.adapters.dingtalk import DingTalkAdapter, _CardState
from openakita.channels.types import MessageContent, OutgoingMessage, MediaFile


def _sk(chat_id: str, thread_id: str | None = None) -> str:
    """Build session key matching DingTalkAdapter._make_session_key"""
    return f"{chat_id}:{thread_id or ''}"


@pytest.fixture
def adapter():
    a = DingTalkAdapter(app_key="test-key", app_secret="test-secret")
    a._access_token = "mock-token"
    a._token_expires_at = 9999999999
    a._http_client = AsyncMock()
    a._conversation_types["conv_group"] = "2"
    a._conversation_types["conv_private"] = "1"
    a._conversation_users["conv_private"] = "staff123"
    a._conversation_users["conv_group"] = "staff456"
    # Default: force StandardCard path for backward compat tests
    a._ai_card_available = False
    return a


def _mock_card_response(success=True):
    resp = MagicMock()
    if success:
        resp.json.return_value = {"processQueryKey": "pqk_123"}
    else:
        resp.json.return_value = {"errcode": 400, "errmsg": "bad request"}
    resp.status_code = 200 if success else 400
    return resp


def _mock_ai_card_create_response():
    resp = MagicMock()
    resp.json.return_value = {"outTrackId": "ai_mock123", "success": True}
    return resp


def _mock_ai_card_deliver_response():
    resp = MagicMock()
    resp.json.return_value = {"spaceId": "space_123", "success": True}
    return resp


def _mock_ai_card_stream_response():
    resp = MagicMock()
    resp.json.return_value = {"success": True}
    return resp


def _mock_webhook_response():
    resp = MagicMock()
    resp.json.return_value = {"errcode": 0, "errmsg": "ok"}
    return resp


class TestSendTyping:
    @pytest.mark.asyncio
    async def test_creates_card_on_first_call(self, adapter):
        adapter._http_client.post = AsyncMock(return_value=_mock_card_response())
        await adapter.send_typing("conv_group")

        sk = _sk("conv_group")
        assert sk in adapter._thinking_cards
        card_state = adapter._thinking_cards[sk]
        assert isinstance(card_state, _CardState)
        assert card_state.is_ai_card is False
        adapter._http_client.post.assert_called_once()
        body = adapter._http_client.post.call_args.kwargs["json"]
        assert body["cardTemplateId"] == "StandardCard"
        assert body["openConversationId"] == "conv_group"

    @pytest.mark.asyncio
    async def test_idempotent_second_call(self, adapter):
        adapter._http_client.post = AsyncMock(return_value=_mock_card_response())
        await adapter.send_typing("conv_group")
        await adapter.send_typing("conv_group")

        assert adapter._http_client.post.call_count == 1

    @pytest.mark.asyncio
    async def test_single_chat_uses_singleChatReceiver(self, adapter):
        adapter._http_client.post = AsyncMock(return_value=_mock_card_response())
        await adapter.send_typing("conv_private")

        body = adapter._http_client.post.call_args.kwargs["json"]
        assert "singleChatReceiver" in body
        receiver = json.loads(body["singleChatReceiver"])
        assert receiver["userId"] == "staff123"
        assert "openConversationId" not in body

    @pytest.mark.asyncio
    async def test_encrypted_sender_id_skips(self, adapter):
        adapter._conversation_users["conv_private"] = "$:LWCP_v1:$encrypted"
        adapter._http_client.post = AsyncMock(return_value=_mock_card_response())
        await adapter.send_typing("conv_private")

        sk = _sk("conv_private")
        assert sk not in adapter._thinking_cards

    @pytest.mark.asyncio
    async def test_api_failure_rolls_back(self, adapter):
        adapter._http_client.post = AsyncMock(return_value=_mock_card_response(success=False))
        await adapter.send_typing("conv_group")

        sk = _sk("conv_group")
        assert sk not in adapter._thinking_cards

    @pytest.mark.asyncio
    async def test_network_error_rolls_back(self, adapter):
        adapter._http_client.post = AsyncMock(side_effect=Exception("timeout"))
        await adapter.send_typing("conv_group")

        sk = _sk("conv_group")
        assert sk not in adapter._thinking_cards

    @pytest.mark.asyncio
    async def test_carddata_is_json_string(self, adapter):
        adapter._http_client.post = AsyncMock(return_value=_mock_card_response())
        await adapter.send_typing("conv_group")

        body = adapter._http_client.post.call_args.kwargs["json"]
        card_data = body["cardData"]
        assert isinstance(card_data, str)
        parsed = json.loads(card_data)
        assert "config" in parsed
        assert "contents" in parsed
        assert parsed["contents"][0]["type"] == "markdown"


class TestAICardSendTyping:
    """AI Card specific tests."""

    @pytest.mark.asyncio
    async def test_ai_card_create_and_deliver(self, adapter):
        adapter._ai_card_available = True
        create_resp = _mock_ai_card_create_response()
        deliver_resp = _mock_ai_card_deliver_response()
        adapter._http_client.post = AsyncMock(side_effect=[create_resp, deliver_resp])

        await adapter.send_typing("conv_group")

        sk = _sk("conv_group")
        assert sk in adapter._thinking_cards
        card_state = adapter._thinking_cards[sk]
        assert card_state.is_ai_card is True
        assert adapter._http_client.post.call_count == 2

    @pytest.mark.asyncio
    async def test_ai_card_fallback_to_standard(self, adapter):
        adapter._ai_card_available = True
        ai_fail = MagicMock()
        ai_fail.json.return_value = {"errcode": 403, "errmsg": "no permission"}
        standard_ok = _mock_card_response()
        adapter._http_client.post = AsyncMock(side_effect=[ai_fail, standard_ok])

        await adapter.send_typing("conv_group")

        sk = _sk("conv_group")
        assert sk in adapter._thinking_cards
        assert adapter._thinking_cards[sk].is_ai_card is False
        assert adapter._ai_card_available is False


class TestClearTyping:
    @pytest.mark.asyncio
    async def test_noop_when_no_card(self, adapter):
        adapter._http_client.put = AsyncMock()
        await adapter.clear_typing("conv_group")

        adapter._http_client.put.assert_not_called()

    @pytest.mark.asyncio
    async def test_updates_stale_card(self, adapter):
        sk = _sk("conv_group")
        adapter._thinking_cards[sk] = _CardState(card_id="biz_stale", is_ai_card=False)
        adapter._http_client.put = AsyncMock(return_value=_mock_card_response())
        await adapter.clear_typing("conv_group")

        assert sk not in adapter._thinking_cards
        adapter._http_client.put.assert_called_once()
        body = adapter._http_client.put.call_args.kwargs["json"]
        assert body["cardBizId"] == "biz_stale"
        card_data = json.loads(body["cardData"])
        assert "处理完成" in card_data["contents"][0]["text"]

    @pytest.mark.asyncio
    async def test_update_failure_silent(self, adapter):
        sk = _sk("conv_group")
        adapter._thinking_cards[sk] = _CardState(card_id="biz_stale", is_ai_card=False)
        adapter._http_client.put = AsyncMock(side_effect=Exception("network"))
        await adapter.clear_typing("conv_group")

        assert sk not in adapter._thinking_cards

    @pytest.mark.asyncio
    async def test_ai_card_clear_finishes_card(self, adapter):
        sk = _sk("conv_group")
        adapter._thinking_cards[sk] = _CardState(card_id="ai_track_123", is_ai_card=True)
        adapter._http_client.put = AsyncMock(return_value=_mock_ai_card_stream_response())
        await adapter.clear_typing("conv_group")

        assert sk not in adapter._thinking_cards
        adapter._http_client.put.assert_called_once()
        body = adapter._http_client.put.call_args.kwargs["json"]
        assert body["outTrackId"] == "ai_track_123"
        assert body["cardData"]["cardParamMap"]["flowStatus"] == "FINISHED"


class TestSendMessageConsumesCard:
    @pytest.mark.asyncio
    async def test_normal_text_updates_card(self, adapter):
        sk = _sk("conv_group")
        adapter._thinking_cards[sk] = _CardState(card_id="biz_001", is_ai_card=False)
        adapter._http_client.put = AsyncMock(return_value=_mock_card_response())
        msg = OutgoingMessage.text("conv_group", "Hello response")

        result = await adapter.send_message(msg)

        assert result == "card_biz_001"
        assert sk not in adapter._thinking_cards
        body = adapter._http_client.put.call_args.kwargs["json"]
        card_data = json.loads(body["cardData"])
        assert card_data["contents"][0]["text"] == "Hello response"

    @pytest.mark.asyncio
    async def test_card_update_failure_falls_through(self, adapter):
        sk = _sk("conv_group")
        adapter._thinking_cards[sk] = _CardState(card_id="biz_002", is_ai_card=False)
        adapter._http_client.put = AsyncMock(return_value=_mock_card_response(success=False))
        adapter._http_client.post = AsyncMock(return_value=_mock_webhook_response())
        adapter._session_webhooks["conv_group"] = "https://fake-webhook"

        msg = OutgoingMessage.text("conv_group", "fallback text")
        result = await adapter.send_message(msg)

        assert result.startswith("webhook_")
        assert sk not in adapter._thinking_cards

    @pytest.mark.asyncio
    async def test_media_message_updates_card_to_done(self, adapter):
        sk = _sk("conv_group")
        adapter._thinking_cards[sk] = _CardState(card_id="biz_003", is_ai_card=False)
        put_mock = AsyncMock(return_value=_mock_card_response())
        post_mock = AsyncMock(return_value=_mock_webhook_response())
        adapter._http_client.put = put_mock
        adapter._http_client.post = post_mock
        adapter._session_webhooks["conv_group"] = "https://fake-webhook"

        content = MessageContent(
            text="look at this",
            images=[MediaFile(id="img1", filename="img.png", mime_type="image/png", file_id="@lAL123")],
        )
        msg = OutgoingMessage(chat_id="conv_group", content=content)

        await adapter.send_message(msg)

        assert sk not in adapter._thinking_cards
        put_body = put_mock.call_args.kwargs["json"]
        card_data = json.loads(put_body["cardData"])
        assert "处理完成" in card_data["contents"][0]["text"]

    @pytest.mark.asyncio
    async def test_no_card_normal_flow(self, adapter):
        adapter._http_client.post = AsyncMock(return_value=_mock_webhook_response())
        adapter._session_webhooks["conv_group"] = "https://fake-webhook"
        msg = OutgoingMessage.text("conv_group", "no card here")

        result = await adapter.send_message(msg)

        assert result.startswith("webhook_")

    @pytest.mark.asyncio
    async def test_ai_card_consumed_by_send_message(self, adapter):
        sk = _sk("conv_group")
        adapter._thinking_cards[sk] = _CardState(card_id="ai_track_001", is_ai_card=True)
        adapter._http_client.put = AsyncMock(return_value=_mock_ai_card_stream_response())
        msg = OutgoingMessage.text("conv_group", "AI response")

        result = await adapter.send_message(msg)

        assert result == "card_ai_track_001"
        assert sk not in adapter._thinking_cards
        body = adapter._http_client.put.call_args.kwargs["json"]
        assert body["outTrackId"] == "ai_track_001"
        assert body["cardData"]["cardParamMap"]["flowStatus"] == "FINISHED"
        assert body["cardData"]["cardParamMap"]["msgContent"] == "AI response"


class TestTypingLifecycle:
    """End-to-end lifecycle tests simulating Gateway behavior."""

    @pytest.mark.asyncio
    async def test_normal_lifecycle(self, adapter):
        """send_typing -> send_message -> clear_typing (no-op)"""
        adapter._http_client.post = AsyncMock(return_value=_mock_card_response())
        adapter._http_client.put = AsyncMock(return_value=_mock_card_response())

        await adapter.send_typing("conv_group")
        sk = _sk("conv_group")
        assert sk in adapter._thinking_cards

        msg = OutgoingMessage.text("conv_group", "final answer")
        result = await adapter.send_message(msg)
        assert result.startswith("card_")
        assert sk not in adapter._thinking_cards

        await adapter.clear_typing("conv_group")
        assert adapter._http_client.put.call_count == 1

    @pytest.mark.asyncio
    async def test_double_failure_lifecycle(self, adapter):
        """send_typing -> (both agent and error fail) -> clear_typing cleans up"""
        adapter._http_client.post = AsyncMock(return_value=_mock_card_response())
        adapter._http_client.put = AsyncMock(return_value=_mock_card_response())

        await adapter.send_typing("conv_group")
        sk = _sk("conv_group")
        card_state = adapter._thinking_cards[sk]
        assert card_state is not None

        await adapter.clear_typing("conv_group")
        assert sk not in adapter._thinking_cards
        put_body = adapter._http_client.put.call_args.kwargs["json"]
        assert put_body["cardBizId"] == card_state.card_id
        assert "处理完成" in json.loads(put_body["cardData"])["contents"][0]["text"]

    @pytest.mark.asyncio
    async def test_card_recreation_after_consumption(self, adapter):
        """Simulates interrupt scenario: card consumed, then recreated by typing."""
        adapter._http_client.post = AsyncMock(return_value=_mock_card_response())
        adapter._http_client.put = AsyncMock(return_value=_mock_card_response())

        await adapter.send_typing("conv_group")
        sk = _sk("conv_group")
        first_card = adapter._thinking_cards[sk]

        msg = OutgoingMessage.text("conv_group", "main response")
        await adapter.send_message(msg)
        assert sk not in adapter._thinking_cards

        await adapter.send_typing("conv_group")
        second_card = adapter._thinking_cards[sk]
        assert second_card.card_id != first_card.card_id

        msg2 = OutgoingMessage.text("conv_group", "interrupt response")
        await adapter.send_message(msg2)
        assert sk not in adapter._thinking_cards

        await adapter.clear_typing("conv_group")

    @pytest.mark.asyncio
    async def test_fast_response_no_card(self, adapter):
        """Agent responds before send_typing runs."""
        adapter._http_client.post = AsyncMock(return_value=_mock_webhook_response())
        adapter._session_webhooks["conv_group"] = "https://fake-webhook"

        msg = OutgoingMessage.text("conv_group", "instant response")
        result = await adapter.send_message(msg)
        assert result.startswith("webhook_")

        await adapter.clear_typing("conv_group")

    @pytest.mark.asyncio
    async def test_no_staff_id_silent_degradation(self, adapter):
        """Single chat without staffId: no card, no error."""
        adapter._conversation_users.pop("conv_private", None)
        adapter._http_client.post = AsyncMock(return_value=_mock_webhook_response())
        adapter._session_webhooks["conv_private"] = "https://fake-webhook"

        await adapter.send_typing("conv_private")
        sk = _sk("conv_private")
        assert sk not in adapter._thinking_cards

        msg = OutgoingMessage.text("conv_private", "normal text")
        result = await adapter.send_message(msg)
        assert result.startswith("webhook_")


class TestPatchCardContent:
    """Test _patch_card_content for thinking-to-card progress patching."""

    @pytest.mark.asyncio
    async def test_standard_card_patch(self, adapter):
        card_state = _CardState(card_id="biz_patch_01", is_ai_card=False)
        adapter._http_client.put = AsyncMock(return_value=_mock_card_response())

        result = await adapter._patch_card_content(card_state, "💭 思考中...")
        assert result is True
        adapter._http_client.put.assert_called_once()
        body = adapter._http_client.put.call_args.kwargs["json"]
        assert body["cardBizId"] == "biz_patch_01"
        card_data = json.loads(body["cardData"])
        assert "💭 思考中..." in card_data["contents"][0]["text"]

    @pytest.mark.asyncio
    async def test_ai_card_patch(self, adapter):
        card_state = _CardState(card_id="ai_patch_01", is_ai_card=True)
        adapter._http_client.put = AsyncMock(return_value=_mock_ai_card_stream_response())

        result = await adapter._patch_card_content(card_state, "💭 深度推理中...")
        assert result is True
        adapter._http_client.put.assert_called_once()
        body = adapter._http_client.put.call_args.kwargs["json"]
        assert body["outTrackId"] == "ai_patch_01"

    @pytest.mark.asyncio
    async def test_patch_failure_returns_false(self, adapter):
        card_state = _CardState(card_id="biz_fail", is_ai_card=False)
        adapter._http_client.put = AsyncMock(side_effect=Exception("network"))

        result = await adapter._patch_card_content(card_state, "text")
        assert result is False

    @pytest.mark.asyncio
    async def test_none_card_state_returns_false(self, adapter):
        result = await adapter._patch_card_content(None, "text")
        assert result is False

    @pytest.mark.asyncio
    async def test_empty_card_id_returns_false(self, adapter):
        card_state = _CardState(card_id="", is_ai_card=False)
        result = await adapter._patch_card_content(card_state, "text")
        assert result is False


class TestStopMonkeyPatch:
    """Test that stop() prevents SDK reconnection."""

    @pytest.mark.asyncio
    async def test_stop_patches_open_connection(self, adapter):
        mock_client = MagicMock()
        mock_client.websocket = None
        original_open = mock_client.open_connection
        adapter._stream_client = mock_client
        adapter._running = True

        await adapter.stop()

        assert mock_client.open_connection is not original_open
        assert mock_client.open_connection() is None

    @pytest.mark.asyncio
    async def test_stop_clears_main_loop(self, adapter):
        loop = MagicMock()
        adapter._main_loop = loop
        adapter._running = True

        await adapter.stop()

        assert adapter._main_loop is None

    @pytest.mark.asyncio
    async def test_stop_sets_running_false(self, adapter):
        adapter._running = True
        await adapter.stop()
        assert adapter._running is False


class TestTextChunking:
    """Test _chunk_markdown_text."""

    def test_short_text_no_split(self):
        chunks = DingTalkAdapter._chunk_markdown_text("hello world")
        assert chunks == ["hello world"]

    def test_exact_limit(self):
        text = "a" * 4000
        chunks = DingTalkAdapter._chunk_markdown_text(text, 4000)
        assert len(chunks) == 1

    def test_long_text_splits(self):
        text = ("paragraph one\n\n" * 200).strip()
        chunks = DingTalkAdapter._chunk_markdown_text(text, 100)
        for c in chunks:
            assert len(c) <= 100
        assert "".join(chunks) == text

    def test_preserves_code_blocks(self):
        text = "before\n\n```python\n" + "x = 1\n" * 500 + "```\n\nafter"
        chunks = DingTalkAdapter._chunk_markdown_text(text, 200)
        for c in chunks:
            assert len(c) <= 200
        full = "".join(chunks)
        fence_count = full.count("```")
        assert fence_count % 2 == 0

    def test_empty_text(self):
        chunks = DingTalkAdapter._chunk_markdown_text("")
        assert chunks == [""]
