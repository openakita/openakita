import httpx
import pytest

from openakita.llm.providers.base import LLMProvider
from openakita.llm.providers.openai import OpenAIProvider
from openakita.llm.types import EndpointConfig, LLMRequest, LLMResponse, StopReason, Usage


def _provider() -> OpenAIProvider:
    return OpenAIProvider(
        EndpointConfig(
            name="test",
            provider="openai",
            api_type="openai",
            base_url="https://api.example.com/v1",
            api_key="sk-test",
            model="gpt-test",
        )
    )


def _empty_content_response(**message_extra: object) -> dict:
    return {
        "id": "chatcmpl-test",
        "model": "gpt-test",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": None, **message_extra},
            }
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8},
    }


def test_parse_response_recovers_visible_text_from_message_output_without_reasoning():
    response = _provider()._parse_response(
        _empty_content_response(output=[{"type": "output_text", "text": "你好，我在。"}])
    )

    assert response.content[0].text == "你好，我在。"
    assert response.usage.output_tokens == 5


def test_parse_response_recovers_visible_text_from_top_level_output_with_choices():
    data = _empty_content_response()
    data["output"] = [
        {"type": "message", "content": [{"type": "output_text", "text": "可见回复"}]}
    ]

    response = _provider()._parse_response(data)

    assert response.content[0].text == "可见回复"


def test_parse_response_plain_message_content_is_not_marked_lost():
    provider = _provider()

    response = provider._parse_response(
        {
            "id": "chatcmpl-test",
            "model": "gpt-test",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "Hello from plain content"},
                }
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8},
        }
    )

    assert response.content[0].text == "Hello from plain content"
    assert provider._last_raw_diagnostic is None


def test_parse_response_records_diagnostic_when_tokens_have_no_recoverable_text():
    provider = _provider()

    response = provider._parse_response(_empty_content_response())

    assert response.content == []
    assert provider._last_raw_diagnostic is not None
    assert provider._last_raw_diagnostic["usage"]["completion_tokens"] == 5


class _TokenOnlyProvider(LLMProvider):
    async def chat(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(
            id="resp",
            content=[],
            stop_reason=StopReason.END_TURN,
            usage=Usage(input_tokens=1, output_tokens=2),
            model="bad-proxy",
        )

    async def chat_stream(self, request: LLMRequest):
        if False:
            yield {}


@pytest.mark.asyncio
async def test_health_check_rejects_token_only_empty_content_response():
    provider = _TokenOnlyProvider(
        EndpointConfig(
            name="token-only",
            provider="openai",
            api_type="openai",
            base_url="https://api.example.com/v1",
            api_key="sk-test",
            model="bad-proxy",
        )
    )

    assert await provider.health_check() is False
    assert provider.error_category == "structural"


@pytest.mark.asyncio
async def test_openai_redirect_request_hook_restores_authorization_header():
    provider = _provider()
    client = await provider._get_client()
    try:
        redirected_request = httpx.Request(
            "POST",
            "https://redirected.example.com/v1/chat/completions",
        )

        for hook in client.event_hooks["request"]:
            await hook(redirected_request)

        assert redirected_request.headers["Authorization"] == "Bearer sk-test"
    finally:
        await provider.close()
