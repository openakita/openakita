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


@pytest.mark.asyncio
async def test_wan27_i2v_packs_media_array_not_url_fields(monkeypatch):
    """wan2.7-i2v official 2026-04 spec uses ``input.media[]`` instead
    of ``input.first_frame_url`` / ``last_frame_url`` and rejects any
    ``parameters.task_type`` selector. Regression guard for the
    historical url_fields request that 422'd on every submit."""
    c = HappyhorseDashScopeClient(_read_settings_factory(api_key="sk-x"))
    captured: dict[str, object] = {}

    async def fake_submit_async(path, body):
        captured["path"] = path
        captured["body"] = body
        return "task-123"

    monkeypatch.setattr(c, "_submit_async", fake_submit_async)

    task_id = await c.submit_video_synth(
        mode="i2v_end",
        model_id="wan2.7-i2v",
        prompt="a cinematic shot",
        first_frame_url="https://example.test/first.png",
        last_frame_url="https://example.test/last.png",
        resolution="720P",
        duration=5,
    )

    assert task_id == "task-123"
    body = captured["body"]
    inp = body["input"]
    assert "first_frame_url" not in inp
    assert "last_frame_url" not in inp
    media = inp["media"]
    assert {"type": "first_frame", "url": "https://example.test/first.png"} in media
    assert {"type": "last_frame", "url": "https://example.test/last.png"} in media
    assert "task_type" not in body["parameters"]
    assert body["parameters"]["duration"] == 5
    assert body["parameters"]["resolution"] == "720P"


@pytest.mark.asyncio
async def test_wan27_i2v_rejects_reference_urls(monkeypatch):
    c = HappyhorseDashScopeClient(_read_settings_factory(api_key="sk-x"))
    monkeypatch.setattr(c, "_submit_async", lambda *a, **kw: None)
    with pytest.raises(VendorError) as ei:
        await c.submit_video_synth(
            mode="i2v",
            model_id="wan2.7-i2v",
            prompt="x",
            first_frame_url="https://example.test/f.png",
            reference_urls=["https://example.test/r.png"],
        )
    assert "reference_urls" in str(ei.value)


@pytest.mark.asyncio
async def test_wan26_t2v_keeps_legacy_url_fields(monkeypatch):
    """Wan 2.6 (and HappyHorse 1.0) keep the legacy url_fields contract —
    they MUST NOT be wrapped in a media[] array or DashScope rejects
    them. This pins the dispatch boundary."""
    c = HappyhorseDashScopeClient(_read_settings_factory(api_key="sk-x"))
    captured: dict[str, object] = {}

    async def fake_submit_async(path, body):
        captured["body"] = body
        return "task-26"

    monkeypatch.setattr(c, "_submit_async", fake_submit_async)
    await c.submit_video_synth(
        mode="i2v",
        model_id="wan2.6-i2v",
        prompt="x",
        first_frame_url="https://example.test/f.png",
        resolution="720P",
        duration=5,
    )
    body = captured["body"]
    assert "media" not in body["input"]
    assert body["input"]["first_frame_url"] == "https://example.test/f.png"


@pytest.mark.asyncio
async def test_video_edit_packs_media_v2v_with_optional_refs(monkeypatch):
    """happyhorse-1.0-video-edit's official spec is ``input.media``
    containing exactly one {type:"video"} plus 0-5 optional
    {type:"image"} reference frames — NOT input.video_url. Lock the
    shape down so the historical url_fields request can't reappear."""
    c = HappyhorseDashScopeClient(_read_settings_factory(api_key="sk-x"))
    captured: dict[str, object] = {}

    async def fake_submit_async(path, body):
        captured["body"] = body
        return "t-edit"

    monkeypatch.setattr(c, "_submit_async", fake_submit_async)

    await c.submit_video_synth(
        mode="video_edit",
        model_id="happyhorse-1.0-video-edit",
        prompt="replace the sky with sunset",
        source_video_url="https://example.test/in.mp4",
        reference_urls=[
            "https://example.test/ref1.png",
            "https://example.test/ref2.png",
        ],
        resolution="720P",
    )
    body = captured["body"]
    assert "video_url" not in body["input"]
    assert body["input"]["media"] == [
        {"type": "video", "url": "https://example.test/in.mp4"},
        {"type": "image", "url": "https://example.test/ref1.png"},
        {"type": "image", "url": "https://example.test/ref2.png"},
    ]
    assert "task_type" not in body["parameters"]


@pytest.mark.asyncio
async def test_video_edit_rejects_more_than_5_references(monkeypatch):
    c = HappyhorseDashScopeClient(_read_settings_factory(api_key="sk-x"))
    monkeypatch.setattr(c, "_submit_async", lambda *a, **kw: None)
    with pytest.raises(VendorError) as ei:
        await c.submit_video_synth(
            mode="video_edit",
            model_id="happyhorse-1.0-video-edit",
            prompt="x",
            source_video_url="https://example.test/in.mp4",
            reference_urls=[f"https://example.test/r{i}.png" for i in range(6)],
        )
    assert "at most 5" in str(ei.value)


@pytest.mark.asyncio
async def test_video_synth_duration_is_int_for_vendor(monkeypatch):
    """DashScope ``parameters.duration`` must be integer. The earlier
    bug converted it to ``float(3.0)`` and triggered a 422 — pin the
    serialization shape here so it never regresses."""
    c = HappyhorseDashScopeClient(_read_settings_factory(api_key="sk-x"))
    captured: dict[str, object] = {}

    async def fake_submit_async(path, body):
        captured["body"] = body
        return "t"

    monkeypatch.setattr(c, "_submit_async", fake_submit_async)
    await c.submit_video_synth(
        mode="t2v",
        model_id="happyhorse-1.0-t2v",
        prompt="x",
        duration="3",  # legacy string-shaped path
    )
    assert captured["body"]["parameters"]["duration"] == 3
    assert isinstance(captured["body"]["parameters"]["duration"], int)
