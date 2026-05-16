"""HappyhorseDashScopeClient.synth_voice — HTTP fallback for relays.

The SDK path only works against ``dashscope.aliyuncs.com``. Relay
stations (oneapi / new-api / yunwu / private gateways) cannot
mediate the proprietary DashScope SDK protocol, so we route TTS
through the OpenAI-compatible ``POST /v1/audio/speech`` whenever
``base_url`` is not one of the two official DashScope hosts.

These tests freeze:

1. Transport selection: native host -> SDK, anything else -> HTTP.
2. URL construction tolerates the three real-world base_url shapes
   the user might paste (``…/v1``, ``…/compatible-mode/v1``, bare host).
3. Successful HTTP call returns the same ``{audio_bytes, format, duration_sec}``
   shape the SDK path returns, so the pipeline does not care.
4. Auth headers carry the Bearer token; payload has the expected
   model / voice / input / response_format fields.
5. Error classification: 401 -> auth, 404 -> dependency (relay does
   not carry the model), 5xx -> retryable server, headerless audio
   is wrapped as WAV so downstream ffmpeg never trips.
"""

from __future__ import annotations

import asyncio
import struct
import sys
from types import SimpleNamespace

import httpx
import pytest
from happyhorse_dashscope_client import (
    DASHSCOPE_BASE_URL_BJ,
    HappyhorseDashScopeClient,
    make_default_settings,
)
from happyhorse_inline.vendor_client import VendorError


def _read_settings_factory(**overrides):
    def _read():
        s = make_default_settings()
        s.update(overrides)
        return s
    return _read


def _wav_bytes(payload: bytes = b"\x00" * 20) -> bytes:
    """Minimal valid WAV header so the sniffer reports 'wav'."""
    header = b"RIFF" + struct.pack("<I", 36 + len(payload)) + b"WAVE"
    fmt = b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, 22050, 44100, 2, 16)
    data = b"data" + struct.pack("<I", len(payload)) + payload
    return header + fmt + data


def _mp3_bytes(payload: bytes = b"\x00" * 16) -> bytes:
    return b"ID3\x03\x00\x00\x00\x00\x00\x00" + payload


def _install_capture_transport(monkeypatch, *, status=200, body=b"", content_type="audio/mp3", capture=None):
    """Patch httpx.AsyncClient with a MockTransport that records the request."""
    def make_handler():
        def handler(request: httpx.Request) -> httpx.Response:
            if capture is not None:
                capture["url"] = str(request.url)
                capture["headers"] = dict(request.headers)
                capture["body"] = request.content.decode("utf-8") if request.content else ""
            return httpx.Response(status, content=body, headers={"content-type": content_type})
        return handler

    transport = httpx.MockTransport(make_handler())

    real_async_client = httpx.AsyncClient

    class _PatchedClient(real_async_client):
        def __init__(self, *args, **kwargs):
            kwargs.setdefault("transport", transport)
            super().__init__(*args, **kwargs)

    # synth_voice imports httpx locally inside the function body, so
    # patching the module-global is the only intercept point. The
    # client uses ``import httpx`` then references ``httpx.AsyncClient``,
    # which means we have to swap the attribute on the live module
    # object — not on any happyhorse_dashscope_client binding.
    monkeypatch.setattr(httpx, "AsyncClient", _PatchedClient)


# ─── 1. Transport selection ─────────────────────────────────────────


def test_native_host_still_uses_sdk(monkeypatch):
    """Don't accidentally regress the SDK path when no relay is set."""
    c = HappyhorseDashScopeClient(_read_settings_factory(api_key="sk-x"))
    assert c.base_url == DASHSCOPE_BASE_URL_BJ
    # Force the SDK branch to fail loudly so we can confirm we even
    # went down it (without actually installing dashscope).
    monkeypatch.setitem(sys.modules, "dashscope", None)
    monkeypatch.setitem(sys.modules, "dashscope.audio.tts_v2", None)
    with pytest.raises(VendorError) as ei:
        asyncio.run(c.synth_voice(text="hi", voice_id="longwan"))
    assert "dashscope SDK" in str(ei.value)


def test_relay_base_url_routes_to_http(monkeypatch):
    capture: dict = {}
    _install_capture_transport(
        monkeypatch,
        status=200,
        body=_mp3_bytes(),
        capture=capture,
    )
    c = HappyhorseDashScopeClient(
        _read_settings_factory(
            api_key="sk-relay",
            base_url="https://yunwu.example.com/v1",
        )
    )
    result = asyncio.run(c.synth_voice(text="hello", voice_id="longwan"))
    assert result["format"] == "mp3"
    assert result["audio_bytes"].startswith(b"ID3")
    assert capture["url"] == "https://yunwu.example.com/v1/audio/speech"
    assert capture["headers"]["authorization"] == "Bearer sk-relay"


# ─── 2. URL construction tolerance ──────────────────────────────────


@pytest.mark.parametrize(
    "base_url,expected_url",
    [
        ("https://yunwu.example.com/v1", "https://yunwu.example.com/v1/audio/speech"),
        ("https://dashscope.aliyuncs.com/compatible-mode/v1",
         "https://dashscope.aliyuncs.com/compatible-mode/v1/audio/speech"),
        ("https://onpremise.example.com", "https://onpremise.example.com/v1/audio/speech"),
        # Trailing slash must be stripped before /v1 detection
        ("https://relay.example.com/v1/", "https://relay.example.com/v1/audio/speech"),
    ],
)
def test_url_construction_for_base_url_shapes(monkeypatch, base_url, expected_url):
    capture: dict = {}
    _install_capture_transport(monkeypatch, body=_mp3_bytes(), capture=capture)
    c = HappyhorseDashScopeClient(_read_settings_factory(api_key="sk-x", base_url=base_url))
    asyncio.run(c.synth_voice(text="x", voice_id="v"))
    assert capture["url"] == expected_url


def test_payload_shape_matches_openai_audio_speech(monkeypatch):
    """Body must be the OpenAI /audio/speech contract: model, input,
    voice, response_format. Anything else risks 422 on strict relays."""
    import json

    capture: dict = {}
    _install_capture_transport(monkeypatch, body=_mp3_bytes(), capture=capture)
    c = HappyhorseDashScopeClient(
        _read_settings_factory(api_key="sk-x", base_url="https://relay.example.com/v1")
    )
    asyncio.run(c.synth_voice(text="你好", voice_id="longwan", format="wav"))
    payload = json.loads(capture["body"])
    assert payload == {
        "model": "cosyvoice-v2",
        "input": "你好",
        "voice": "longwan",
        "response_format": "wav",
    }


# ─── 3. Result shape parity with SDK path ───────────────────────────


def test_http_result_matches_sdk_shape(monkeypatch):
    _install_capture_transport(monkeypatch, body=_wav_bytes())
    c = HappyhorseDashScopeClient(
        _read_settings_factory(api_key="sk-x", base_url="https://relay.example.com/v1")
    )
    result = asyncio.run(c.synth_voice(text="x", voice_id="v", format="wav"))
    assert set(result.keys()) == {"audio_bytes", "format", "duration_sec"}
    assert result["format"] == "wav"
    assert result["duration_sec"] is None


def test_headerless_pcm_audio_is_wrapped_as_wav(monkeypatch):
    """Some relays return raw PCM even when response_format=mp3 was
    requested. The wrapper avoids surprising the ffmpeg concat step."""
    raw_pcm = b"\x10\x00\x20\x00" * 20  # not a valid header
    _install_capture_transport(monkeypatch, body=raw_pcm, content_type="audio/mpeg")
    c = HappyhorseDashScopeClient(
        _read_settings_factory(api_key="sk-x", base_url="https://relay.example.com/v1")
    )
    result = asyncio.run(c.synth_voice(text="x", voice_id="v"))
    assert result["format"] == "wav"
    assert result["audio_bytes"].startswith(b"RIFF")


# ─── 4. Error classification ────────────────────────────────────────


def test_http_401_raises_auth_error(monkeypatch):
    _install_capture_transport(monkeypatch, status=401, body=b'{"error":"bad key"}')
    c = HappyhorseDashScopeClient(
        _read_settings_factory(api_key="sk-x", base_url="https://relay.example.com/v1")
    )
    with pytest.raises(VendorError) as ei:
        asyncio.run(c.synth_voice(text="x", voice_id="v"))
    assert ei.value.status == 401
    assert ei.value.retryable is False


def test_http_404_raises_dependency_error(monkeypatch):
    """Relay doesn't carry the model; user should switch relay, not
    spam retries."""
    _install_capture_transport(monkeypatch, status=404, body=b"no such model")
    c = HappyhorseDashScopeClient(
        _read_settings_factory(api_key="sk-x", base_url="https://relay.example.com/v1")
    )
    with pytest.raises(VendorError) as ei:
        asyncio.run(c.synth_voice(text="x", voice_id="v"))
    assert ei.value.status == 404
    assert ei.value.retryable is False


def test_http_500_is_retryable(monkeypatch):
    _install_capture_transport(monkeypatch, status=502, body=b"bad gateway")
    c = HappyhorseDashScopeClient(
        _read_settings_factory(api_key="sk-x", base_url="https://relay.example.com/v1")
    )
    with pytest.raises(VendorError) as ei:
        asyncio.run(c.synth_voice(text="x", voice_id="v"))
    assert ei.value.retryable is True


def test_http_empty_body_raises_dependency(monkeypatch):
    _install_capture_transport(monkeypatch, status=200, body=b"")
    c = HappyhorseDashScopeClient(
        _read_settings_factory(api_key="sk-x", base_url="https://relay.example.com/v1")
    )
    with pytest.raises(VendorError) as ei:
        asyncio.run(c.synth_voice(text="x", voice_id="v"))
    assert "empty audio" in str(ei.value)


def test_empty_api_key_raises_before_http(monkeypatch):
    """No need to round-trip a guaranteed-401 just to learn the key
    is empty."""
    _install_capture_transport(monkeypatch, body=_mp3_bytes())
    c = HappyhorseDashScopeClient(
        _read_settings_factory(api_key="", base_url="https://relay.example.com/v1")
    )
    with pytest.raises(VendorError) as ei:
        asyncio.run(c.synth_voice(text="x", voice_id="v"))
    assert "API Key" in str(ei.value)
