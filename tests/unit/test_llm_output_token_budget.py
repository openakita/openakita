import pytest

from openakita.llm.client import LLMClient
from openakita.llm.providers.openai import OpenAIProvider
from openakita.llm.providers.openai_responses import OpenAIResponsesProvider
from openakita.llm.retry import extract_output_token_upper_bound
from openakita.llm.types import EndpointConfig, LLMError, LLMRequest, Message


def _endpoint(*, model: str, provider: str = "deepseek", max_tokens: int = 0) -> EndpointConfig:
    return EndpointConfig(
        name="test",
        provider=provider,
        api_type="openai",
        base_url="https://api.example.com/v1",
        api_key="sk-test",
        model=model,
        max_tokens=max_tokens,
    )


def _request(max_tokens: int = 0) -> LLMRequest:
    return LLMRequest(messages=[Message(role="user", content="hi")], max_tokens=max_tokens)


def test_deepseek_default_uses_model_budget_not_historical_fallback():
    provider = OpenAIProvider(_endpoint(model="deepseek-chat"))

    body = provider._build_request_body(_request())

    assert body["max_tokens"] == 4096


def test_known_model_explicit_budget_is_capped_to_model_limit():
    provider = OpenAIProvider(_endpoint(model="deepseek-chat"))

    body = provider._build_request_body(_request(max_tokens=99_999))

    assert body["max_tokens"] == 8192


def test_unknown_model_keeps_wide_fallback_and_explicit_budget():
    provider = OpenAIProvider(_endpoint(model="custom-proxy-model", provider="proxy"))

    default_body = provider._build_request_body(_request())
    explicit_body = provider._build_request_body(_request(max_tokens=50_000))

    assert default_body["max_tokens"] == 16_384
    assert explicit_body["max_tokens"] == 50_000


def test_responses_api_uses_same_output_budget_policy():
    provider = OpenAIResponsesProvider(_endpoint(model="deepseek-chat"))

    body = provider._build_request_body(_request())

    assert body["max_output_tokens"] == 4096


def test_extract_output_token_upper_bound_from_upstream_range():
    error = LLMError(
        "云端模型调用失败 (HTTP 400)",
        status_code=400,
        raw_body=(
            '{"error":{"message":"Invalid max_tokens value, '
            'the valid range of max_tokens is [1, 8192]"}}'
        ),
    )

    assert extract_output_token_upper_bound(error) == 8192


@pytest.mark.asyncio
async def test_retry_adjusts_max_tokens_to_upstream_range_once():
    client = LLMClient(endpoints=[])
    request = _request(max_tokens=99_999)
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise LLMError(
                "云端模型调用失败 (HTTP 400)",
                status_code=400,
                raw_body=(
                    '{"error":{"message":"Invalid max_tokens value, '
                    'the valid range of max_tokens is [1, 8192]"}}'
                ),
            )
        return request.max_tokens

    result = await client._try_with_retry(
        operation,
        max_attempts=2,
        request=request,
        provider_name="deepseek",
    )

    assert result == 8192
    assert request.max_tokens == 8192
    assert calls == 2
