"""HappyhorseDashScopeClient — registry-driven dispatch tests."""

from __future__ import annotations

import pytest
from happyhorse_dashscope_client import (
    DASHSCOPE_BASE_URL_BJ,
    HappyhorseDashScopeClient,
    make_default_settings,
)
from happyhorse_inline.vendor_client import VendorError


def _read_settings_factory(api_key: str = "", base_url: str = ""):
    def _read():
        s = make_default_settings()
        if api_key:
            s["api_key"] = api_key
        if base_url:
            s["base_url"] = base_url
        return s
    return _read


def test_client_constructs_without_apikey():
    c = HappyhorseDashScopeClient(_read_settings_factory())
    assert c.has_api_key() is False
    assert c.base_url == DASHSCOPE_BASE_URL_BJ


def test_client_picks_up_apikey_from_settings():
    c = HappyhorseDashScopeClient(_read_settings_factory(api_key="sk-xxx"))
    assert c.has_api_key() is True


def test_resolve_model_returns_default_when_none_passed():
    c = HappyhorseDashScopeClient(_read_settings_factory())
    entry = c.resolve_model("t2v", None)
    assert entry.mode == "t2v"
    assert entry.model_id == "happyhorse-1.0-t2v"


def test_resolve_model_explicit_id_wins_when_compatible():
    c = HappyhorseDashScopeClient(_read_settings_factory())
    entry = c.resolve_model("i2v", "happyhorse-1.0-i2v")
    assert entry.model_id == "happyhorse-1.0-i2v"


def test_resolve_model_unknown_id_falls_back_to_default():
    """Unknown model_id must transparently fall back to the per-mode
    default — this is what the pipeline relies on so a stale UI-cached
    model_id doesn't strand the task."""
    c = HappyhorseDashScopeClient(_read_settings_factory())
    entry = c.resolve_model("t2v", "totally-not-a-model")
    assert entry.mode == "t2v"
    assert entry.model_id == "happyhorse-1.0-t2v"


def test_auth_headers_use_settings_api_key():
    c = HappyhorseDashScopeClient(_read_settings_factory(api_key="sk-from-settings"))
    headers = c.auth_headers()
    assert headers["Authorization"].endswith("sk-from-settings")


@pytest.mark.asyncio
async def test_image_multimodal_falls_back_when_async_is_not_allowed(monkeypatch):
    c = HappyhorseDashScopeClient(_read_settings_factory(api_key="sk-from-settings"))
    calls: list[str] = []

    async def fake_submit_async(path, body):
        calls.append("async")
        raise VendorError(
            "HTTP 403: async not allowed",
            status=403,
            body={
                "code": "AccessDenied",
                "message": "current user api does not support asynchronous calls",
            },
            retryable=False,
            kind="auth",
        )

    async def fake_request(method, path, **kwargs):
        calls.append("sync")
        return {"output": {"image_url": "https://example.test/out.png"}}

    monkeypatch.setattr(c, "_submit_async", fake_submit_async)
    monkeypatch.setattr(c, "request", fake_request)

    result = await c.submit_image_multimodal(prompt="一匹小马", async_mode=True)

    assert result["async"] is False
    assert result["output"]["image_url"] == "https://example.test/out.png"
    assert calls == ["async", "sync"]

    second = await c.submit_image_multimodal(prompt="一匹小马", async_mode=True)

    assert second["async"] is False
    assert calls == ["async", "sync", "sync"]
