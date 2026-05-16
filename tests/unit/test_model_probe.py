"""model_probe — relay endpoint catalog discovery.

These tests stub httpx with a transport so we never touch the network.
Coverage:

- URL selection per (api_type, provider) — OpenAI vs Anthropic vs
  DashScope's compat path.
- Parser tolerance for the half-dozen real-world response shapes
  (OpenAI / Anthropic-shim / OneAPI / bare list).
- Error classification: 401/403 → auth, 404 → unsupported, HTML body
  → unsupported, JSON error body → ProbeError.
- Auth header set: Bearer + x-api-key sent together so we cover both
  Anthropic and OpenAI relays without per-provider branching.
"""

from __future__ import annotations

import json

import httpx
import pytest

from openakita.llm.model_probe import (
    ProbeAuthError,
    ProbeError,
    ProbeNetworkError,
    ProbeUnsupported,
    probe_models,
)


def _make_transport(
    *,
    status: int = 200,
    body: str | bytes = b"",
    content_type: str = "application/json",
    capture: dict | None = None,
):
    """Build a mock transport that records the last request into capture."""

    def handler(request: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture["url"] = str(request.url)
            capture["headers"] = dict(request.headers)
            capture["method"] = request.method
        payload = body if isinstance(body, (bytes, bytearray)) else body.encode("utf-8")
        return httpx.Response(status, content=payload, headers={"content-type": content_type})

    return httpx.MockTransport(handler)


@pytest.fixture(autouse=True)
def patch_httpx(monkeypatch):
    """Replace httpx.get with one that routes through the test transport.

    We install a fresh stub per test via the ``capture`` dict the test
    needs; tests that want the default 200 OpenAI shape can use the
    ``openai_ok`` fixture, others build their own transport.
    """
    state: dict = {"transport": None}

    def fake_get(url, **kwargs):
        if state["transport"] is None:
            raise AssertionError("Test did not register a transport via set_transport")
        with httpx.Client(transport=state["transport"]) as client:
            return client.get(url, **{k: v for k, v in kwargs.items() if k != "transport"})

    monkeypatch.setattr("openakita.llm.model_probe.httpx.get", fake_get)
    state["set"] = lambda t: state.__setitem__("transport", t)
    return state


# ─── URL selection per (api_type, provider) ──────────────────────────


def test_openai_compat_relay_hits_v1_models(patch_httpx):
    capture: dict = {}
    patch_httpx["set"](
        _make_transport(
            body=json.dumps({"data": [{"id": "gpt-4o"}]}),
            capture=capture,
        )
    )
    models = probe_models(
        api_type="openai",
        base_url="https://relay.example.com/v1",
        api_key="sk-x",
    )
    assert models == ["gpt-4o"]
    assert capture["url"] == "https://relay.example.com/v1/models"
    assert capture["headers"]["authorization"] == "Bearer sk-x"
    assert capture["headers"]["x-api-key"] == "sk-x"


def test_dashscope_hits_compatible_mode_path(patch_httpx):
    capture: dict = {}
    patch_httpx["set"](
        _make_transport(
            body=json.dumps({"data": [{"id": "qwen-max"}]}),
            capture=capture,
        )
    )
    models = probe_models(
        api_type="openai",
        base_url="https://dashscope.aliyuncs.com",
        provider="dashscope",
        api_key="sk-y",
    )
    assert models == ["qwen-max"]
    assert capture["url"] == (
        "https://dashscope.aliyuncs.com/compatible-mode/v1/models"
    )


def test_unknown_api_type_raises_unsupported(patch_httpx):
    patch_httpx["set"](_make_transport(body=b""))
    with pytest.raises(ProbeUnsupported):
        probe_models(
            api_type="not-a-real-type",
            base_url="https://x.example.com/v1",
        )


def test_normalize_strips_chat_completions_suffix(patch_httpx):
    """A user pastes /v1/chat/completions verbatim; we still hit /models."""
    capture: dict = {}
    patch_httpx["set"](
        _make_transport(
            body=json.dumps({"data": [{"id": "m"}]}),
            capture=capture,
        )
    )
    probe_models(
        api_type="openai",
        base_url="https://relay.example.com/v1/chat/completions",
        api_key="sk-x",
    )
    assert capture["url"] == "https://relay.example.com/v1/models"


# ─── Payload-shape tolerance ─────────────────────────────────────────


@pytest.mark.parametrize(
    "payload,expected",
    [
        # OpenAI canonical
        ({"data": [{"id": "a"}, {"id": "b"}]}, ["a", "b"]),
        # Bare list of strings (some OneAPI deployments)
        ({"data": ["a", "b"]}, ["a", "b"]),
        # Anthropic-ish shim
        ({"models": [{"name": "c"}]}, ["c"]),
        # Flat list at root
        ([{"id": "a"}, {"id": "b"}], ["a", "b"]),
        ([{"name": "a"}, "b"], ["a", "b"]),
        # Empty catalog is a valid result (NOT an error)
        ({"data": []}, []),
        # Duplicates collapsed but order preserved
        ({"data": [{"id": "a"}, {"id": "a"}, {"id": "b"}]}, ["a", "b"]),
    ],
)
def test_parser_accepts_real_world_shapes(patch_httpx, payload, expected):
    patch_httpx["set"](_make_transport(body=json.dumps(payload)))
    assert probe_models(
        api_type="openai",
        base_url="https://relay.example.com/v1",
    ) == expected


# ─── Error classification ────────────────────────────────────────────


def test_401_classified_as_auth_error(patch_httpx):
    patch_httpx["set"](
        _make_transport(status=401, body=json.dumps({"error": "bad key"}))
    )
    with pytest.raises(ProbeAuthError) as ei:
        probe_models(
            api_type="openai",
            base_url="https://relay.example.com/v1",
            api_key="sk-bad",
        )
    assert ei.value.status == 401
    assert "Key" in ei.value.user_message


def test_404_classified_as_unsupported(patch_httpx):
    patch_httpx["set"](_make_transport(status=404, body=b"not found"))
    with pytest.raises(ProbeUnsupported) as ei:
        probe_models(
            api_type="openai",
            base_url="https://relay.example.com/v1",
        )
    assert ei.value.status == 404


def test_html_body_classified_as_unsupported(patch_httpx):
    patch_httpx["set"](
        _make_transport(
            status=200,
            body=b"<!DOCTYPE html><html><body>Login required</body></html>",
            content_type="text/html",
        )
    )
    with pytest.raises(ProbeUnsupported) as ei:
        probe_models(
            api_type="openai",
            base_url="https://relay.example.com/v1",
        )
    assert "HTML" in ei.value.user_message or "登录" in ei.value.user_message


def test_200_with_error_body_classified_as_probe_error(patch_httpx):
    patch_httpx["set"](
        _make_transport(
            status=200, body=json.dumps({"error": "quota exceeded"})
        )
    )
    with pytest.raises(ProbeError) as ei:
        probe_models(
            api_type="openai",
            base_url="https://relay.example.com/v1",
        )
    assert "quota" in ei.value.user_message


def test_invalid_json_raises_probe_error(patch_httpx):
    patch_httpx["set"](_make_transport(status=200, body=b"definitely not json"))
    with pytest.raises(ProbeError):
        probe_models(
            api_type="openai",
            base_url="https://relay.example.com/v1",
        )


def test_timeout_classified_as_network_error(monkeypatch):
    def fake_get(url, **kwargs):
        raise httpx.TimeoutException("simulated")

    monkeypatch.setattr("openakita.llm.model_probe.httpx.get", fake_get)
    with pytest.raises(ProbeNetworkError) as ei:
        probe_models(
            api_type="openai",
            base_url="https://relay.example.com/v1",
        )
    assert "超时" in ei.value.user_message


def test_anthropic_only_sends_bearer_and_xapikey(patch_httpx):
    """Both header families set so neither Anthropic shim nor OpenAI
    shim relays reject the request for missing auth."""
    capture: dict = {}
    patch_httpx["set"](
        _make_transport(
            body=json.dumps({"data": [{"id": "claude"}]}),
            capture=capture,
        )
    )
    probe_models(
        api_type="anthropic",
        base_url="https://relay.example.com/v1",
        api_key="sk-anth",
    )
    assert capture["headers"]["authorization"] == "Bearer sk-anth"
    assert capture["headers"]["x-api-key"] == "sk-anth"
    assert capture["headers"]["anthropic-version"] == "2023-06-01"
