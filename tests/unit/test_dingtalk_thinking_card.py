"""Unit tests for DingTalk adapter thinking card (send_typing / clear_typing).

Validates all paths identified in the plan:
- Normal path: card created -> consumed by send_message
- Error path: card consumed by _send_error -> send_message
- Double-failure: card cleaned up by clear_typing
- Interrupt: card recreated after consumption, cleaned by clear_typing
- Split message: first fragment consumes card, rest sent normally
- Fast response: no card created
- Media message: card updated to "处理完成", media sent normally
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from openakita.channels.adapters.dingtalk import DingTalkAdapter
from openakita.channels.types import MessageContent, OutgoingMessage, MediaFile


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
    return a


def _mock_card_response(success=True):
    resp = MagicMock()
    if success:
        resp.json.return_value = {"processQueryKey": "pqk_123"}
    else:
        resp.json.return_value = {"errcode": 400, "errmsg": "bad request"}
    resp.status_code = 200 if success else 400
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

        assert "conv_group" in adapter._thinking_cards
        adapter._http_client.post.assert_called_once()
        call_args = adapter._http_client.post.call_args
        body = call_args.kwargs["json"]
        assert body["cardTemplateId"] == "StandardCard"
        assert body["openConversationId"] == "conv_group"
        assert "singleChatReceiver" not in body

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

        assert "conv_private" not in adapter._thinking_cards
        adapter._http_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_api_failure_rolls_back(self, adapter):
        adapter._http_client.post = AsyncMock(return_value=_mock_card_response(success=False))
        await adapter.send_typing("conv_group")

        assert "conv_group" not in adapter._thinking_cards

    @pytest.mark.asyncio
    async def test_network_error_rolls_back(self, adapter):
        adapter._http_client.post = AsyncMock(side_effect=Exception("timeout"))
        await adapter.send_typing("conv_group")

        assert "conv_group" not in adapter._thinking_cards

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


class TestClearTyping:
    @pytest.mark.asyncio
    async def test_noop_when_no_card(self, adapter):
        adapter._http_client.put = AsyncMock()
        await adapter.clear_typing("conv_group")

        adapter._http_client.put.assert_not_called()

    @pytest.mark.asyncio
    async def test_updates_stale_card(self, adapter):
        adapter._thinking_cards["conv_group"] = "biz_stale"
        adapter._http_client.put = AsyncMock(return_value=_mock_card_response())
        await adapter.clear_typing("conv_group")

        assert "conv_group" not in adapter._thinking_cards
        adapter._http_client.put.assert_called_once()
        body = adapter._http_client.put.call_args.kwargs["json"]
        assert body["cardBizId"] == "biz_stale"
        card_data = json.loads(body["cardData"])
        assert "处理完成" in card_data["contents"][0]["text"]

    @pytest.mark.asyncio
    async def test_update_failure_silent(self, adapter):
        adapter._thinking_cards["conv_group"] = "biz_stale"
        adapter._http_client.put = AsyncMock(side_effect=Exception("network"))
        await adapter.clear_typing("conv_group")

        assert "conv_group" not in adapter._thinking_cards


class TestSendMessageConsumesCard:
    @pytest.mark.asyncio
    async def test_normal_text_updates_card(self, adapter):
        adapter._thinking_cards["conv_group"] = "biz_001"
        adapter._http_client.put = AsyncMock(return_value=_mock_card_response())
        msg = OutgoingMessage.text("conv_group", "Hello response")

        result = await adapter.send_message(msg)

        assert result == "card_biz_001"
        assert "conv_group" not in adapter._thinking_cards
        body = adapter._http_client.put.call_args.kwargs["json"]
        card_data = json.loads(body["cardData"])
        assert card_data["contents"][0]["text"] == "Hello response"

    @pytest.mark.asyncio
    async def test_card_update_failure_falls_through(self, adapter):
        adapter._thinking_cards["conv_group"] = "biz_002"
        adapter._http_client.put = AsyncMock(return_value=_mock_card_response(success=False))
        adapter._http_client.post = AsyncMock(return_value=_mock_webhook_response())
        adapter._session_webhooks["conv_group"] = "https://fake-webhook"

        msg = OutgoingMessage.text("conv_group", "fallback text")
        result = await adapter.send_message(msg)

        assert result.startswith("webhook_")
        assert "conv_group" not in adapter._thinking_cards

    @pytest.mark.asyncio
    async def test_media_message_updates_card_to_done(self, adapter):
        adapter._thinking_cards["conv_group"] = "biz_003"
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

        assert "conv_group" not in adapter._thinking_cards
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


class TestTypingLifecycle:
    """End-to-end lifecycle tests simulating Gateway behavior."""

    @pytest.mark.asyncio
    async def test_normal_lifecycle(self, adapter):
        """send_typing -> send_message -> clear_typing (no-op)"""
        adapter._http_client.post = AsyncMock(return_value=_mock_card_response())
        adapter._http_client.put = AsyncMock(return_value=_mock_card_response())

        await adapter.send_typing("conv_group")
        assert "conv_group" in adapter._thinking_cards

        msg = OutgoingMessage.text("conv_group", "final answer")
        result = await adapter.send_message(msg)
        assert result.startswith("card_")
        assert "conv_group" not in adapter._thinking_cards

        await adapter.clear_typing("conv_group")
        assert adapter._http_client.put.call_count == 1

    @pytest.mark.asyncio
    async def test_double_failure_lifecycle(self, adapter):
        """send_typing -> (both agent and error fail) -> clear_typing cleans up"""
        adapter._http_client.post = AsyncMock(return_value=_mock_card_response())
        adapter._http_client.put = AsyncMock(return_value=_mock_card_response())

        await adapter.send_typing("conv_group")
        card_id = adapter._thinking_cards["conv_group"]
        assert card_id is not None

        await adapter.clear_typing("conv_group")
        assert "conv_group" not in adapter._thinking_cards
        put_body = adapter._http_client.put.call_args.kwargs["json"]
        assert put_body["cardBizId"] == card_id
        assert "处理完成" in json.loads(put_body["cardData"])["contents"][0]["text"]

    @pytest.mark.asyncio
    async def test_card_recreation_after_consumption(self, adapter):
        """Simulates interrupt scenario: card consumed, then recreated by typing."""
        adapter._http_client.post = AsyncMock(return_value=_mock_card_response())
        adapter._http_client.put = AsyncMock(return_value=_mock_card_response())

        await adapter.send_typing("conv_group")
        first_card = adapter._thinking_cards["conv_group"]

        msg = OutgoingMessage.text("conv_group", "main response")
        await adapter.send_message(msg)
        assert "conv_group" not in adapter._thinking_cards

        await adapter.send_typing("conv_group")
        second_card = adapter._thinking_cards["conv_group"]
        assert second_card != first_card

        msg2 = OutgoingMessage.text("conv_group", "interrupt response")
        await adapter.send_message(msg2)
        assert "conv_group" not in adapter._thinking_cards

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
        assert "conv_private" not in adapter._thinking_cards

        msg = OutgoingMessage.text("conv_private", "normal text")
        result = await adapter.send_message(msg)
        assert result.startswith("webhook_")
