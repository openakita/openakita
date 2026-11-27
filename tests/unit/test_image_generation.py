from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from openakita.core.policy_v2 import PolicyContext, reset_current_context, set_current_context
from openakita.llm.endpoint_manager import EndpointManager
from openakita.llm.image_generation import (
    ImageGenerationError,
    ImageGenerationResult,
    build_image_request,
    image_endpoint_url,
    load_image_endpoints,
    parse_image_response,
    request_image,
    select_image_endpoints,
)
from openakita.llm.types import EndpointConfig
from openakita.tools.handlers.system import SystemHandler


def _endpoint(**overrides) -> EndpointConfig:
    values = {
        "name": "images",
        "provider": "openai",
        "api_type": "openai_images",
        "base_url": "https://api.openai.com/v1",
        "api_key": "sk-test",
        "model": "gpt-image-1",
        "priority": 10,
        "capabilities": ["image_generation"],
    }
    values.update(overrides)
    return EndpointConfig(**values)


def test_image_endpoint_urls_support_base_and_full_urls() -> None:
    assert image_endpoint_url(_endpoint()) == "https://api.openai.com/v1/images/generations"
    assert (
        image_endpoint_url(
            _endpoint(base_url="https://relay.example/v1/images/generations")
        )
        == "https://relay.example/v1/images/generations"
    )
    assert image_endpoint_url(
        _endpoint(
            api_type="dashscope",
            provider="dashscope",
            base_url="https://dashscope.aliyuncs.com",
        )
    ).endswith("/api/v1/services/aigc/multimodal-generation/generation")


def test_build_openai_images_request_normalizes_options() -> None:
    endpoint = _endpoint(extra_params={"default_quality": "high"})
    body = build_image_request(
        endpoint,
        prompt="a red kite",
        negative_prompt="text",
        size="1024*1536",
    )

    assert body == {
        "model": "gpt-image-1",
        "prompt": "a red kite\nAvoid: text",
        "n": 1,
        "size": "1024x1536",
        "quality": "high",
    }


def test_build_dashscope_request_uses_native_shape() -> None:
    endpoint = _endpoint(
        provider="dashscope",
        api_type="dashscope",
        model="qwen-image-max",
        extra_params={"default_size": "1664*928"},
    )
    body = build_image_request(
        endpoint,
        prompt="山水画",
        negative_prompt="文字",
        seed=42,
        watermark=True,
    )

    assert body["model"] == "qwen-image-max"
    assert body["input"]["messages"][0]["content"] == [{"text": "山水画"}]
    assert body["parameters"] == {
        "prompt_extend": True,
        "watermark": True,
        "size": "1664*928",
        "negative_prompt": "文字",
        "seed": 42,
    }


def test_parse_openai_base64_response() -> None:
    payload = b"not-a-real-png"
    result = parse_image_response(
        _endpoint(),
        {"id": "img-1", "data": [{"b64_json": base64.b64encode(payload).decode()}]},
    )

    assert result.request_id == "img-1"
    assert result.image_bytes == payload
    assert result.image_url is None


@pytest.mark.asyncio
async def test_request_image_calls_openai_images_protocol() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.openai.com/v1/images/generations"
        assert request.headers["authorization"] == "Bearer sk-test"
        body = json.loads(request.content)
        assert body["model"] == "gpt-image-1"
        assert body["prompt"] == "draw a lighthouse"
        return httpx.Response(200, json={"data": [{"url": "https://cdn.example/image.png"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await request_image(client, _endpoint(), prompt="draw a lighthouse")

    assert result.endpoint_name == "images"
    assert result.image_url == "https://cdn.example/image.png"


def test_image_endpoints_are_loaded_by_priority(tmp_path) -> None:
    manager = EndpointManager(tmp_path)
    manager.save_endpoint(
        {
            "name": "backup",
            "provider": "openai",
            "api_type": "openai_images",
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-image-1",
            "priority": 20,
            "capabilities": ["image_generation"],
        },
        api_key="backup-key",
        endpoint_type="image_endpoints",
    )
    manager.save_endpoint(
        {
            "name": "primary",
            "provider": "dashscope",
            "api_type": "dashscope",
            "base_url": "https://dashscope.aliyuncs.com",
            "model": "qwen-image-max",
            "priority": 10,
            "capabilities": ["image_generation"],
        },
        api_key="primary-key",
        endpoint_type="image_endpoints",
    )

    endpoints = load_image_endpoints(tmp_path)

    assert [endpoint.name for endpoint in endpoints] == ["primary", "backup"]
    assert [endpoint.name for endpoint in select_image_endpoints(endpoints, "backup")] == [
        "backup"
    ]


@pytest.mark.asyncio
async def test_generate_image_falls_back_and_saves_base64_result(tmp_path, monkeypatch) -> None:
    primary = _endpoint(name="primary", priority=10)
    backup = _endpoint(name="backup", priority=20)

    async def fake_request(_client, endpoint, **_kwargs):
        if endpoint.name == "primary":
            raise ImageGenerationError("temporary provider failure")
        return ImageGenerationResult(
            endpoint_name="backup",
            model="gpt-image-1",
            request_id="img-fallback",
            image_bytes=b"png-bytes",
        )

    monkeypatch.setattr(
        "openakita.llm.image_generation.load_image_endpoints",
        lambda _workspace: [primary, backup],
    )
    monkeypatch.setattr("openakita.llm.image_generation.request_image", fake_request)

    context = PolicyContext(
        session_id="image-generation-test",
        working_directory=tmp_path,
        workspace_roots=(tmp_path,),
    )
    token = set_current_context(context)
    try:
        result = json.loads(
            await SystemHandler(SimpleNamespace()).handle(
                "generate_image", {"prompt": "fallback test"}
            )
        )
    finally:
        reset_current_context(token)

    assert result["ok"] is True
    assert result["endpoint"] == "backup"
    saved_path = Path(result["saved_to"])
    assert saved_path == (tmp_path / "gpt-image-1_img-fallback.png").resolve()
    assert saved_path.read_bytes() == b"png-bytes"


@pytest.mark.parametrize(
    ("region", "model"),
    [("io", "MiniMax-H3-Image"), ("cn", "MiniMax-H3-Image-Highspeed")],
)
@pytest.mark.asyncio
async def test_generate_h3_image(tmp_path, monkeypatch, region: str, model: str) -> None:
    from openakita.llm.capabilities import is_image_generation_model

    assert is_image_generation_model(model)
    base = f"https://api.minimax.{region}/v1"
    endpoint = _endpoint(
        provider="custom",
        base_url=base,
        model=model,
        timeout=30,
        extra_params={"request_params": {"response_format": "b64_json"}},
    )
    payload = b"generated png"
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        assert request.method == "POST"
        assert request.headers["authorization"] == "Bearer sk-test"
        assert request.extensions["timeout"]["read"] == 600
        body = json.loads(request.content)
        expected = {
            "model": model,
            "prompt": "a red kite",
            "n": 1,
            "size": "1024x1024",
            "output_format": "png",
            "response_format": "b64_json",
            "disable_prompt_enhanced": True,
        }
        if region == "cn":
            expected["aigc_watermark"] = True
        assert body == expected
        return httpx.Response(
            200,
            json={"request_id": "h3-1", "data": [{"b64_json": base64.b64encode(payload).decode()}]},
        )

    monkeypatch.setattr("openakita.llm.image_generation.load_image_endpoints", lambda _: [endpoint])
    monkeypatch.setattr(
        "openakita.llm.providers.proxy_utils.get_httpx_client_kwargs",
        lambda **_: {"transport": httpx.MockTransport(handler)},
    )
    ctx = PolicyContext(
        session_id="image-h3", working_directory=tmp_path, workspace_roots=(tmp_path,)
    )
    token = set_current_context(ctx)
    try:
        result = json.loads(
            await SystemHandler(SimpleNamespace()).handle(
                "generate_image",
                {"prompt": "a red kite", "prompt_extend": False, "watermark": True},
            )
        )
    finally:
        reset_current_context(token)
    assert result["ok"]
    assert result["model"] == model
    assert Path(result["saved_to"]).read_bytes() == payload
    assert seen == [f"{base}/images/generations"]


def test_parse_h3_partial_success() -> None:
    endpoint = _endpoint(model="MiniMax-H3-Image")
    failed = {"error": {"code": "1027", "message": "image rejected"}}
    result = parse_image_response(
        endpoint, {"data": [failed, {"url": "https://cdn.example/art.png"}]}
    )
    assert result.image_url == "https://cdn.example/art.png"
    with pytest.raises(ImageGenerationError, match="image rejected"):
        parse_image_response(endpoint, {"data": [failed]})


@pytest.mark.parametrize("failure", ["timeout", "http_error", "redirect"])
@pytest.mark.asyncio
async def test_h3_failure_does_not_submit_another_generation(monkeypatch, failure: str) -> None:
    endpoint = _endpoint(name="h3", model="MiniMax-H3-Image", base_url="https://api.minimax.io/v1")
    backup = _endpoint(name="backup")
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if failure == "timeout":
            raise httpx.ReadTimeout("generation timed out", request=request)
        if failure == "redirect":
            return httpx.Response(307, headers={"Location": "https://api.minimax.io/redirect"})
        return httpx.Response(
            422,
            json={
                "type": "error",
                "error": {"type": "unprocessable_entity_error", "message": "image rejected"},
            },
        )

    monkeypatch.setattr(
        "openakita.llm.image_generation.load_image_endpoints", lambda _: [endpoint, backup]
    )
    monkeypatch.setattr(
        "openakita.llm.providers.proxy_utils.get_httpx_client_kwargs",
        lambda **_: {"transport": httpx.MockTransport(handler)},
    )
    output = await SystemHandler(SimpleNamespace()).handle(
        "generate_image", {"prompt": "a red kite"}
    )
    if failure == "http_error":
        assert "image rejected" in output
    assert requests == ["https://api.minimax.io/v1/images/generations"]
