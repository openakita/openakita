from types import SimpleNamespace

from openakita.core.brain import Brain
from openakita.llm.client import _friendly_error_hint
from openakita.llm.error_types import FailoverReason
from openakita.llm.types import LOCAL_ENDPOINT_DEFAULT_CONTEXT_WINDOW, EndpointConfig
from openakita.tools.handlers.config import ConfigHandler


def test_local_endpoint_missing_context_window_uses_small_safe_default():
    endpoint = EndpointConfig.from_dict(
        {
            "name": "lmstudio-gemma",
            "provider": "lmstudio",
            "api_type": "openai",
            "base_url": "http://127.0.0.1:1234/v1",
            "model": "gemma-4-e2b-it-Q8_0.gguf",
        }
    )

    assert endpoint.context_window == LOCAL_ENDPOINT_DEFAULT_CONTEXT_WINDOW


def test_local_endpoint_legacy_large_default_is_normalized():
    endpoint = EndpointConfig.from_dict(
        {
            "name": "localai-gemma",
            "provider": "localai",
            "api_type": "openai",
            "base_url": "http://localhost:8080/v1",
            "model": "gemma",
            "context_window": 200000,
        }
    )

    assert endpoint.context_window == LOCAL_ENDPOINT_DEFAULT_CONTEXT_WINDOW


def test_local_endpoint_explicit_large_context_window_is_preserved():
    endpoint = EndpointConfig.from_dict(
        {
            "name": "lmstudio-large-context",
            "provider": "lmstudio",
            "api_type": "openai",
            "base_url": "http://127.0.0.1:1234/v1",
            "model": "qwen-large-context",
            "context_window": 262144,
        }
    )

    assert endpoint.context_window == 262144


def test_hosted_endpoint_keeps_large_default_when_context_window_missing():
    endpoint = EndpointConfig.from_dict(
        {
            "name": "qwen",
            "provider": "dashscope",
            "api_type": "openai",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "model": "qwen-plus",
        }
    )

    assert endpoint.context_window == 200000


def test_models_response_context_window_extraction_prefers_matching_model():
    response = SimpleNamespace(
        json=lambda: {
            "data": [
                {"id": "other", "context_length": 8192},
                {"id": "gemma", "metadata": {"n_ctx": 4096}},
            ]
        }
    )

    detected = ConfigHandler._extract_context_window_from_models_response(response, "gemma")

    assert detected == 4096


def test_brain_tool_schema_budget_scales_down_for_small_context(monkeypatch):
    brain = Brain.__new__(Brain)
    endpoint = SimpleNamespace(name="local", context_window=4096)
    brain._llm_client = SimpleNamespace(endpoints=[endpoint])
    brain.get_current_model_info = lambda: {"name": "local"}

    monkeypatch.setattr("openakita.core.brain.settings.api_tools_schema_budget_tokens", 12000)

    tools = [
        {
            "name": f"tool_{idx}",
            "description": "x" * 100,
            "input_schema": {
                "type": "object",
                "properties": {f"field_{idx}": {"type": "string", "description": "y" * 300}},
            },
        }
        for idx in range(10)
    ]

    converted = brain._convert_tools_to_llm(tools)

    assert converted is not None
    assert len(converted) < len(tools)


def test_context_overflow_hint_is_specific_and_friendly():
    provider = SimpleNamespace(
        error_category=FailoverReason.STRUCTURAL,
        _last_error=(
            "API error (400): request (44593 tokens) exceeds the available "
            "context size (4096 tokens)"
        ),
    )

    hint = _friendly_error_hint([provider])

    assert "上下文窗口偏小" in hint
    assert "调大 context size" in hint
    assert "模型兼容性问题" not in hint
