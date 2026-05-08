from openakita.llm.capabilities import infer_capabilities
from openakita.llm.providers.openai import OpenAIProvider
from openakita.llm.types import EndpointConfig, LLMRequest, Message


def _request(depth: str) -> LLMRequest:
    return LLMRequest(
        messages=[Message(role="user", content="hi")],
        enable_thinking=True,
        thinking_depth=depth,
        max_tokens=128,
    )


def _provider(*, provider: str, model: str, base_url: str) -> OpenAIProvider:
    return OpenAIProvider(
        EndpointConfig(
            name="test",
            provider=provider,
            api_type="openai",
            base_url=base_url,
            api_key="sk-test",
            model=model,
            capabilities=["text", "thinking"],
        )
    )


def test_deepseek_v4_pro_max_uses_reasoning_effort_max():
    provider = _provider(
        provider="deepseek",
        model="deepseek-v4-pro",
        base_url="https://api.deepseek.com/v1",
    )

    body = provider._build_request_body(_request("max"))

    assert body["thinking"] == {"type": "enabled"}
    assert body["reasoning_effort"] == "max"


def test_deepseek_v4_pro_xhigh_alias_uses_reasoning_effort_max():
    provider = _provider(
        provider="deepseek",
        model="deepseek-v4-pro",
        base_url="https://api.deepseek.com/v1",
    )

    body = provider._build_request_body(_request("xhigh"))

    assert body["reasoning_effort"] == "max"


def test_custom_deepseek_base_url_allows_v4_pro_max_effort():
    provider = _provider(
        provider="custom",
        model="deepseek-v4-pro",
        base_url="https://api.deepseek.com/v1",
    )

    body = provider._build_request_body(_request("max"))

    assert body["reasoning_effort"] == "max"


def test_deepseek_v4_pro_lower_depths_map_to_high_for_api_compatibility():
    provider = _provider(
        provider="deepseek",
        model="deepseek-v4-pro",
        base_url="https://api.deepseek.com/v1",
    )

    for depth in ("low", "medium", "high"):
        body = provider._build_request_body(_request(depth))
        assert body["reasoning_effort"] == "high"


def test_generic_openai_compatible_max_does_not_leak_unsupported_effort():
    provider = _provider(
        provider="custom",
        model="some-thinking-model",
        base_url="https://example.com/v1",
    )

    body = provider._build_request_body(_request("max"))

    assert body["thinking"] == {"type": "enabled"}
    assert body["reasoning_effort"] == "high"


def test_deepseek_v4_pro_is_inferred_as_thinking_model():
    caps = infer_capabilities("deepseek-v4-pro", provider_slug="deepseek")

    assert caps["thinking"] is True
    assert caps["tools"] is True
