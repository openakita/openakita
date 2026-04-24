"""Unit tests for ``idea_dashscope_client``.

Each call goes through an ``httpx.MockTransport`` so we exercise the
real HTTP layer (auth headers, async-task header, JSON parsing, error
classification) without ever touching the network.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from idea_dashscope_client import (
    PARAFORMER_TASKS_PATH,
    TEXT_PATH,
    VLM_PATH,
    DashScopeClient,
    select_asr_backend,
)
from idea_research_inline.vendor_client import (
    VendorAuthError,
    VendorError,
    VendorFormatError,
    VendorQuotaError,
    VendorRateLimitError,
)


def _client_with(handler: Any) -> DashScopeClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, base_url="https://test")
    return DashScopeClient(client=http, api_key="sk-xxx", default_timeout_s=5.0)


def _ok(payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json=payload)


# --------------------------------------------------------------------------- #
# select_asr_backend                                                           #
# --------------------------------------------------------------------------- #


def test_select_asr_backend_explicit_overrides() -> None:
    assert select_asr_backend(None, "local") == "local"
    assert select_asr_backend(None, "cloud") == "cloud"


def test_select_asr_backend_auto_picks_cloud_for_long(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("idea_dashscope_client.faster_whisper_available", lambda: True)
    assert select_asr_backend(None, "auto", duration_s=720.0) == "cloud"


def test_select_asr_backend_auto_picks_local_for_short(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("idea_dashscope_client.faster_whisper_available", lambda: True)
    assert select_asr_backend(None, "auto", duration_s=120.0) == "local"


def test_select_asr_backend_falls_back_to_cloud_when_no_fw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("idea_dashscope_client.faster_whisper_available", lambda: False)
    assert select_asr_backend(None, "auto", duration_s=10.0) == "cloud"


# --------------------------------------------------------------------------- #
# chat_completion                                                              #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_chat_completion_returns_content() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        return _ok(
            {
                "output": {"choices": [{"message": {"role": "assistant", "content": "hello"}}]},
                "usage": {"input_tokens": 12, "output_tokens": 4},
            }
        )

    client = _client_with(handler)
    res = await client.chat_completion(system="be brief", user="hi")
    assert res.content == "hello"
    assert res.usage["input_tokens"] == 12
    assert seen["url"].endswith(TEXT_PATH)
    assert seen["headers"]["authorization"] == "Bearer sk-xxx"
    assert seen["body"]["model"] == "qwen-max"
    assert seen["body"]["input"]["messages"][0]["content"] == "be brief"


@pytest.mark.asyncio
async def test_chat_completion_parses_json_with_expected_keys() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok(
            {
                "output": {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": '{"hook": {"type": "悬念"}}',
                            }
                        }
                    ]
                }
            }
        )

    client = _client_with(handler)
    res = await client.chat_completion(
        system="",
        user="x",
        response_json=True,
        expected_keys=["hook"],
    )
    assert res.parsed_json == {"hook": {"type": "悬念"}}


@pytest.mark.asyncio
async def test_chat_completion_invalid_json_raises_format(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok({"output": {"choices": [{"message": {"content": "not json at all"}}]}})

    client = _client_with(handler)
    with pytest.raises(VendorFormatError):
        await client.chat_completion(
            system="",
            user="x",
            response_json=True,
            expected_keys=["hook"],
            retries=0,
        )


@pytest.mark.asyncio
async def test_chat_completion_maps_status_codes() -> None:
    def handler_for(status: int, payload: Any | None = None) -> Any:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status, json=payload or {"message": "x"})

        return handler

    client_401 = _client_with(handler_for(401))
    with pytest.raises(VendorAuthError):
        await client_401.chat_completion(system="", user="x")

    client_403 = _client_with(handler_for(403))
    with pytest.raises(VendorQuotaError):
        await client_403.chat_completion(system="", user="x")

    # 429 retries once then raises rate_limit
    client_429 = _client_with(handler_for(429))
    with pytest.raises(VendorRateLimitError):
        await client_429.chat_completion(system="", user="x", retries=0)


@pytest.mark.asyncio
async def test_chat_completion_business_error_payload_classified() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok({"code": "Throttling", "message": "slow down"})

    client = _client_with(handler)
    with pytest.raises(VendorRateLimitError):
        await client.chat_completion(system="", user="x", retries=0)


# --------------------------------------------------------------------------- #
# describe_image                                                               #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_describe_image_parses_json_response(tmp_path: Path) -> None:
    img = tmp_path / "frame.jpg"
    img.write_bytes(b"\xff\xd8\xff fake-jpg")
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        body = json.loads(request.content)
        seen["body"] = body
        return _ok(
            {
                "output": {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "desc": "person waving",
                                        "has_text": False,
                                        "text_extracted": "",
                                        "brand_visible": "Nike",
                                    }
                                )
                            }
                        }
                    ]
                }
            }
        )

    client = _client_with(handler)
    desc = await client.describe_image(img)
    assert desc.desc == "person waving"
    assert desc.brand_visible == "Nike"
    assert seen["url"].endswith(VLM_PATH)
    payload_msg = seen["body"]["input"]["messages"][0]["content"]
    image_part = next(p for p in payload_msg if "image" in p)
    assert image_part["image"].startswith("data:image/jpeg;base64,")
    decoded = base64.b64decode(image_part["image"].split(",", 1)[1])
    assert decoded == img.read_bytes()


@pytest.mark.asyncio
async def test_describe_image_missing_file_raises() -> None:
    client = _client_with(lambda r: _ok({}))
    with pytest.raises(VendorError) as exc:
        await client.describe_image(Path("does-not-exist.jpg"))
    assert exc.value.error_kind == "format"


# --------------------------------------------------------------------------- #
# transcribe_audio (cloud)                                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_transcribe_audio_cloud_polls_until_success(tmp_path: Path) -> None:
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"00000000")
    state = {"polls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and PARAFORMER_TASKS_PATH in str(request.url):
            assert request.headers.get("X-DashScope-Async") == "enable"
            return _ok({"output": {"task_id": "task-1"}})
        if request.method == "GET" and "/tasks/task-1" in str(request.url):
            state["polls"] += 1
            if state["polls"] < 2:
                return _ok({"output": {"task_status": "RUNNING"}})
            return _ok(
                {
                    "output": {
                        "task_status": "SUCCEEDED",
                        "results": [
                            {
                                "sentences": [
                                    {
                                        "begin_time": 0,
                                        "end_time": 1500,
                                        "text": "你好",
                                    },
                                    {
                                        "begin_time": 1500,
                                        "end_time": 3200,
                                        "text": "世界",
                                    },
                                ]
                            }
                        ],
                    },
                    "usage": {"cost_cny": 0.012},
                }
            )
        return httpx.Response(404)

    client = _client_with(handler)
    res = await client.transcribe_audio(
        audio,
        backend="cloud",
        poll_interval_s=0.0,
        poll_timeout_s=5.0,
    )
    assert res.backend == "cloud"
    assert res.text == "你好 世界"
    assert len(res.segments) == 2
    assert res.cost_cny == 0.012
    assert state["polls"] >= 2


@pytest.mark.asyncio
async def test_transcribe_audio_cloud_failed_raises(tmp_path: Path) -> None:
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"x")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return _ok({"output": {"task_id": "tid"}})
        return _ok({"output": {"task_status": "FAILED", "message": "bad audio"}})

    client = _client_with(handler)
    with pytest.raises(VendorError) as exc:
        await client.transcribe_audio(
            audio, backend="cloud", poll_interval_s=0.0, poll_timeout_s=2.0
        )
    assert "bad audio" in str(exc.value)


@pytest.mark.asyncio
async def test_no_api_key_raises_auth_at_call_time() -> None:
    transport = httpx.MockTransport(lambda r: _ok({}))
    http = httpx.AsyncClient(transport=transport, base_url="https://test")
    client = DashScopeClient(client=http, api_key=None)
    with pytest.raises(VendorAuthError):
        await client.chat_completion(system="", user="x")
