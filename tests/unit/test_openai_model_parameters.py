"""Chat Completions parameter compatibility for OpenAI model families."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from openakita.llm.client import LLMClient
from openakita.llm.providers.openai import OpenAIProvider
from openakita.llm.types import EndpointConfig, LLMRequest, Message
from openakita.plugins.llm_facade import PluginLLMFacade


def _endpoint(model: str, **kwargs) -> EndpointConfig:
    return EndpointConfig(
        name="test",
        provider=kwargs.pop("provider", "openai"),
        api_type="openai",
        base_url="https://api.openai.com/v1",
        api_key="sk-test",
        model=model,
        capabilities=kwargs.pop("capabilities", ["text", "tools"]),
        **kwargs,
    )


def _request(**kwargs) -> LLMRequest:
    return LLMRequest(messages=[Message(role="user", content="hi")], **kwargs)


@pytest.mark.parametrize(
    "model",
    [
        "o1",
        "o1-mini",
        "o1-2024-12-17",
        "o3",
        "o3-mini",
        "o4-mini",
        "openai/o1",
        "openai/o3-mini",
        "gpt-5",
        "gpt-5-mini",
        "gpt-5-nano",
        "gpt-5-2025-08-07",
        "gpt-5.1",
        "gpt-5.2",
        "gpt-5.4",
        "openai/gpt-5.4",
        "GPT-5.2",
    ],
)
def test_openai_models_use_completion_token_budget(model):
    provider = OpenAIProvider(_endpoint(model, max_tokens=4096))

    default_body = provider._build_request_body(_request())
    explicit_body = provider._build_request_body(_request(max_tokens=2048))

    assert default_body["max_completion_tokens"] == 4096
    assert explicit_body["max_completion_tokens"] == 2048
    assert "max_tokens" not in default_body
    assert "max_tokens" not in explicit_body


@pytest.mark.parametrize(
    "provider_name,model",
    [
        ("openai", "gpt-4o"),
        ("openai", "gpt-50"),
        ("openai", "custom-o1-model"),
        ("openai", "vendor/o13"),
        ("openrouter", "openai/gpt-5"),
        ("custom", "gpt-5"),
        ("custom", "o3"),
    ],
)
def test_other_providers_and_unrelated_names_keep_legacy_parameters(provider_name, model):
    provider = OpenAIProvider(_endpoint(model, provider=provider_name))

    body = provider._build_request_body(_request(max_tokens=2048, temperature=0.2))

    assert body["max_tokens"] == 2048
    assert "max_completion_tokens" not in body
    assert body["temperature"] == 0.2


@pytest.mark.parametrize(
    "model",
    [
        "o1",
        "o3",
        "o4-mini",
        "openai/o3-mini",
        "gpt-5",
        "gpt-5-mini",
        "gpt-5-nano",
        "gpt-5-2025-08-07",
    ],
)
@pytest.mark.parametrize("extra_temperature", [None, 0.4])
def test_fixed_temperature_models_omit_temperature_even_from_extra_params(model, extra_temperature):
    extras = {"temperature": extra_temperature} if extra_temperature is not None else None
    provider = OpenAIProvider(_endpoint(model, extra_params=extras))

    body = provider._build_request_body(_request(temperature=0.2, extra_params=extras))

    assert "temperature" not in body


@pytest.mark.parametrize("model", ["gpt-5.1", "gpt-5.2", "gpt-5.4"])
@pytest.mark.parametrize("effort", [None, "none", "high"])
def test_later_gpt5_temperature_depends_on_effective_reasoning_effort(model, effort):
    extras = {"reasoning_effort": effort} if effort is not None else None
    provider = OpenAIProvider(_endpoint(model, extra_params=extras))

    body = provider._build_request_body(_request(temperature=0.2))

    if effort == "high":
        assert "temperature" not in body
    else:
        assert body["temperature"] == 0.2


def test_request_reasoning_effort_overrides_endpoint_for_temperature_support():
    provider = OpenAIProvider(_endpoint("gpt-5.2", extra_params={"reasoning_effort": "high"}))

    body = provider._build_request_body(
        _request(temperature=0.2, extra_params={"reasoning_effort": "none"})
    )

    assert body["temperature"] == 0.2
    assert body["reasoning_effort"] == "none"


def test_thinking_depth_controls_temperature_after_extra_params():
    provider = OpenAIProvider(_endpoint("gpt-5.2", capabilities=["text", "thinking"]))

    body = provider._build_request_body(
        _request(
            temperature=0.2,
            enable_thinking=True,
            thinking_depth="high",
            extra_params={"reasoning_effort": "none"},
        )
    )

    assert body["reasoning_effort"] == "high"
    assert "temperature" not in body


@pytest.mark.parametrize(
    "endpoint_extras,request_extras,expected",
    [
        ({"max_tokens": 512}, None, 512),
        ({"max_completion_tokens": 512}, {"max_tokens": 1024}, 1024),
        ({"max_tokens": 512}, {"max_completion_tokens": 1024}, 1024),
        (None, {"max_tokens": 512, "max_completion_tokens": 1024}, 1024),
    ],
)
def test_extra_token_budgets_use_supported_key_and_preserve_precedence(
    endpoint_extras, request_extras, expected
):
    provider = OpenAIProvider(_endpoint("gpt-5.4", extra_params=endpoint_extras))

    body = provider._build_request_body(_request(max_tokens=2048, extra_params=request_extras))

    assert body["max_completion_tokens"] == expected
    assert "max_tokens" not in body


def test_gpt5_chat_variant_keeps_sampling_temperature():
    provider = OpenAIProvider(_endpoint("gpt-5-chat-latest"))

    body = provider._build_request_body(_request(temperature=0.2))

    assert body["temperature"] == 0.2


@pytest.mark.parametrize("model", ["o1", "o3", "gpt-5", "gpt-5-mini", "gpt-5.4"])
@pytest.mark.parametrize("operation", ["chat", "stream", "plugin"])
async def test_actual_requests_succeed_with_strict_mock_endpoint(model, operation, monkeypatch):
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        if "max_tokens" in body:
            error = "Unsupported parameter: 'max_tokens'. Use 'max_completion_tokens' instead."
        elif model != "gpt-5.4" and "temperature" in body:
            error = "Unsupported value: 'temperature'. Only the default (1) value is supported."
        else:
            error = None
        if error:
            return httpx.Response(400, json={"error": {"message": error}})
        if body.get("stream"):
            chunk = {"model": model, "choices": [{"index": 0, "delta": {"content": "ok"}}]}
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n",
            )
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "model": model,
                "choices": [
                    {"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    client = LLMClient(endpoints=[_endpoint(model)])
    provider = client._providers["test"]
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        monkeypatch.setattr(provider, "_get_client", AsyncMock(return_value=http))
        if operation == "chat":
            response = await client.chat(messages=[Message(role="user", content="hi")])
            assert response.text == "ok"
        elif operation == "stream":
            events = [event async for event in provider.chat_stream(_request(temperature=0.2))]
            assert any(event.get("delta", {}).get("text") == "ok" for event in events)
        else:
            facade = PluginLLMFacade("test-plugin", lambda: SimpleNamespace(llm_client=client))
            response = await facade.complete(prompt="hi")
            assert response.text == "ok"

    assert len(requests) == 1
    assert requests[0]["max_completion_tokens"] > 0
    assert "max_tokens" not in requests[0]
