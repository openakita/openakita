from openakita.llm.client import LLMClient
from openakita.llm.types import EndpointConfig


def test_resolve_model_context_prefers_matching_endpoint_effective_window():
    client = LLMClient(
        endpoints=[
            EndpointConfig(
                name="text-small",
                provider="openai",
                api_type="openai",
                base_url="https://api.example.com/v1",
                model="gpt-4.1-mini",
                priority=1,
                context_window=200_000,
                effective_context_window=48_000,
                capabilities=["text"],
            ),
            EndpointConfig(
                name="vision-large",
                provider="openai",
                api_type="openai",
                base_url="https://api.example.com/v1",
                model="gpt-4.1",
                priority=10,
                context_window=200_000,
                effective_context_window=128_000,
                capabilities=["text", "vision", "tools"],
                thinking_param_style="openai_reasoning",
            ),
        ]
    )

    resolved = client.resolve_model_context(require_vision=True)

    assert resolved.endpoint_name == "vision-large"
    assert resolved.effective_context_window == 128_000
    assert resolved.thinking_param_style == "openai_reasoning"
    assert resolved.has_tools is True


def test_resolve_model_context_falls_back_to_priority_when_no_match():
    client = LLMClient(
        endpoints=[
            EndpointConfig(
                name="primary",
                provider="anthropic",
                api_type="anthropic",
                base_url="https://api.anthropic.com",
                model="claude-sonnet",
                priority=1,
                context_window=200_000,
                effective_context_window=160_000,
                capabilities=["text", "tools"],
            )
        ]
    )

    resolved = client.resolve_model_context(require_video=True)

    assert resolved.endpoint_name == "primary"
    assert resolved.selected_by == "priority_fallback"


def test_endpoint_config_auto_infers_openai_reasoning_for_openai_compatible_provider():
    endpoint = EndpointConfig(
        name="deepseek-chat",
        provider="deepseek",
        api_type="openai",
        base_url="https://api.deepseek.com/v1",
        model="deepseek-reasoner",
        capabilities=["text", "thinking"],
    )

    assert endpoint.get_thinking_param_style() == "openai_reasoning"
