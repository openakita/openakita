from openakita.llm.capabilities import infer_capabilities
from openakita.llm.providers.anthropic import AnthropicProvider
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


def test_minimax_m27_max_maps_top_level_thinking_depth_to_high():
    provider = _provider(
        provider="minimax",
        model="MiniMax-M2.7",
        base_url="https://api.minimax.chat/v1",
    )

    body = provider._build_request_body(_request("max"))

    assert body["thinking_depth"] == "high"


def test_official_minimax_cn_m27_max_maps_to_high():
    """Official MiniMax China endpoint (minimax-cn slug, api.minimaxi.com)."""
    provider = _provider(
        provider="minimax-cn",
        model="MiniMax-M2.7",
        base_url="https://api.minimaxi.com/v1",
    )

    body = provider._build_request_body(_request("max"))

    assert body["thinking_depth"] == "high"


def test_official_minimax_cn_m27_full_body_screenshot_config():
    """Reproduce the exact end-user config in the bug screenshot.

    - provider slug: minimax-cn
    - api_type: openai
    - base_url: https://api.minimaxi.com/v1
    - model: MiniMax-M2.7
    - capabilities: text, thinking, vision, video, tools

    With "最大思考模式" (thinking_depth=max) the outgoing body MUST NOT carry
    the user-facing literal "max" anywhere that MiniMax validates against its
    low/medium/high enum.
    """
    provider = OpenAIProvider(
        EndpointConfig(
            name="minimax-cn-MiniMax-M2.7",
            provider="minimax-cn",
            api_type="openai",
            base_url="https://api.minimaxi.com/v1",
            api_key="sk-test",
            model="MiniMax-M2.7",
            capabilities=["text", "thinking", "vision", "video", "tools"],
        )
    )

    body = provider._build_request_body(_request("max"))

    assert body["thinking_depth"] == "high"
    assert body.get("reasoning_effort") in (None, "high")
    assert body.get("thinking_depth") != "max"
    if "reasoning_effort" in body:
        assert body["reasoning_effort"] != "max"


def test_official_minimax_intl_m27_xhigh_maps_to_high():
    """Official MiniMax International endpoint (minimax-int slug, api.minimax.io)."""
    provider = _provider(
        provider="minimax-int",
        model="MiniMax-M2.7",
        base_url="https://api.minimax.io/v1",
    )

    body = provider._build_request_body(_request("xhigh"))

    assert body["thinking_depth"] == "high"


def test_minimax_m27_extra_params_max_is_sanitized_to_high():
    provider = OpenAIProvider(
        EndpointConfig(
            name="test",
            provider="minimax",
            api_type="openai",
            base_url="https://api.minimax.chat/v1",
            api_key="sk-test",
            model="MiniMax-M2.7",
            capabilities=["text", "thinking"],
            extra_params={"thinking_depth": "max"},
        )
    )

    body = provider._build_request_body(_request("medium"))

    assert body["thinking_depth"] == "medium"


def test_dashscope_hosted_minimax_m27_max_maps_thinking_depth_to_high():
    provider = _provider(
        provider="dashscope",
        model="MiniMax-M2.7",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )

    body = provider._build_request_body(_request("xhigh"))

    assert body["thinking_depth"] == "high"


def test_anthropic_compatible_minimax_m27_extra_params_max_is_sanitized_to_high():
    provider = AnthropicProvider(
        EndpointConfig(
            name="test",
            provider="minimax",
            api_type="anthropic",
            base_url="https://api.minimaxi.com/v1",
            api_key="sk-test",
            model="MiniMax-M2.7",
            capabilities=["text", "thinking"],
            extra_params={"thinking_depth": "max"},
        )
    )

    body = provider._build_request_body(_request("max"))

    assert body["thinking_depth"] == "high"


def test_anthropic_compatible_minimax_m27_request_max_sets_thinking_depth_high():
    """Common path: UI sends thinking_depth=max via request; no extra_params override."""
    provider = AnthropicProvider(
        EndpointConfig(
            name="test",
            provider="minimax-int",
            api_type="anthropic",
            base_url="https://api.minimax.io/anthropic",
            api_key="sk-test",
            model="MiniMax-M2.7",
            capabilities=["text", "thinking"],
        )
    )

    body = provider._build_request_body(_request("max"))

    assert body["thinking_depth"] == "high"


def test_anthropic_compatible_minimax_m27_request_xhigh_sets_thinking_depth_high():
    provider = AnthropicProvider(
        EndpointConfig(
            name="test",
            provider="minimax-cn",
            api_type="anthropic",
            base_url="https://api.minimaxi.com/anthropic",
            api_key="sk-test",
            model="MiniMax-M2.7",
            capabilities=["text", "thinking"],
        )
    )

    body = provider._build_request_body(_request("xhigh"))

    assert body["thinking_depth"] == "high"


def test_anthropic_compatible_minimax_m27_no_thinking_depth_omits_field():
    """When the user did not request any depth, do not inject thinking_depth ourselves."""
    provider = AnthropicProvider(
        EndpointConfig(
            name="test",
            provider="minimax",
            api_type="anthropic",
            base_url="https://api.minimaxi.com/anthropic",
            api_key="sk-test",
            model="MiniMax-M2.7",
            capabilities=["text", "thinking"],
        )
    )

    request = LLMRequest(
        messages=[Message(role="user", content="hi")],
        enable_thinking=True,
        thinking_depth=None,
        max_tokens=128,
    )

    body = provider._build_request_body(request)

    assert "thinking_depth" not in body


def test_deepseek_v4_pro_is_inferred_as_thinking_model():
    caps = infer_capabilities("deepseek-v4-pro", provider_slug="deepseek")

    assert caps["thinking"] is True
    assert caps["tools"] is True
