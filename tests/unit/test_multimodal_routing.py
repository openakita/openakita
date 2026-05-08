import pytest

from openakita.core.tool_executor import ToolExecutor
from openakita.llm.client import LLMClient
from openakita.llm.converters.messages import convert_messages_to_openai
from openakita.llm.types import (
    ImageBlock,
    ImageContent,
    LLMError,
    LLMRequest,
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)


class _AllowDecision:
    behavior = "allow"
    reason = ""
    metadata = {}


class _ListResultRegistry:
    def has_tool(self, tool_name: str) -> bool:
        return tool_name == "view_image"

    def get_handler_name_for_tool(self, tool_name: str) -> str | None:
        return None

    async def execute_by_tool(self, tool_name: str, tool_input: dict):
        return [
            {"type": "text", "text": "loaded"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
        ]


class _OpenToolExecutor(ToolExecutor):
    def check_permission(self, tool_name: str, tool_input: dict):
        return _AllowDecision()


class _DummyProvider:
    name = "custom-minimax-m2.7"


@pytest.mark.asyncio
async def test_tool_executor_preserves_multimodal_tool_result_list():
    executor = _OpenToolExecutor(_ListResultRegistry())

    results, executed, _receipts = await executor.execute_batch(
        [{"id": "call_1", "name": "view_image", "input": {"path": "x.png"}}]
    )

    assert executed == ["view_image"]
    assert isinstance(results[0]["content"], list)
    assert results[0]["content"][1]["type"] == "image_url"


def test_tool_result_image_is_degraded_for_non_vision_openai_endpoint():
    messages = [
        Message(
            role="assistant",
            content=[ToolUseBlock(id="call_1", name="view_image", input={"path": "x.png"})],
        ),
        Message(
            role="user",
            content=[
                ToolResultBlock(
                    tool_use_id="call_1",
                    content=[
                        {"type": "text", "text": "loaded"},
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/png;base64,abc"},
                        },
                    ],
                )
            ],
        ),
    ]

    converted = convert_messages_to_openai(
        messages,
        provider="minimax",
        model="minimax-m2.7",
        vision_available=False,
    )

    tool_msg = converted[1]
    assert tool_msg["role"] == "tool"
    assert "image_url" not in str(tool_msg["content"])
    assert "当前模型不支持视觉" in str(tool_msg["content"])


def test_image_url_rejection_self_heals_by_disabling_provider_image_payloads():
    client = LLMClient(endpoints=[])
    request = LLMRequest(
        messages=[
            Message(
                role="user",
                content=[
                    TextBlock(text="看图"),
                    ImageBlock(image=ImageContent(media_type="image/png", data="abc")),
                ],
            )
        ]
    )
    provider = _DummyProvider()
    error = LLMError(
        "云端模型调用失败 (HTTP 400)",
        raw_body='{"msg":"Bad Request: [message type \'image_url\' is not supported]"}',
    )

    assert client._try_self_heal(error, request, provider) is True
    assert provider._vision_payload_unsupported is True
    assert request._vision_payload_stripped is True
