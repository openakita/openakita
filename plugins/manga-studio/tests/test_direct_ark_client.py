"""Phase 2.2 — direct_ark_client.py: hot-reload settings, URL extraction, body shape."""

from __future__ import annotations

from typing import Any

import pytest

from direct_ark_client import (
    ARK_BASE_URL,
    DEFAULT_SEEDANCE_I2V,
    DEFAULT_SEEDANCE_T2V,
    MangaArkClient,
)
from manga_inline.vendor_client import VendorError


@pytest.fixture
def client_factory():
    """Helper to build a client backed by a mutable dict for hot-reload tests."""

    def _make(initial: dict[str, Any] | None = None) -> tuple[MangaArkClient, dict]:
        store: dict[str, Any] = dict(initial or {})
        c = MangaArkClient(read_settings=lambda: dict(store))
        return c, store

    return _make


# ─── auth_headers reads settings dynamically ─────────────────────────────


def test_auth_headers_uses_settings_callable(client_factory) -> None:
    c, store = client_factory({"ark_api_key": "sk-abcd"})
    assert c.auth_headers() == {"Authorization": "Bearer sk-abcd"}
    # Hot reload — change the setting and re-read; no plugin reload needed.
    store["ark_api_key"] = "sk-newer"
    assert c.auth_headers() == {"Authorization": "Bearer sk-newer"}


def test_auth_headers_raises_vendor_error_when_key_missing(client_factory) -> None:
    c, _ = client_factory({})
    with pytest.raises(VendorError) as exc_info:
        c.auth_headers()
    assert exc_info.value.kind == "auth"


def test_explicit_api_key_overrides_settings(client_factory) -> None:
    c, store = client_factory({"ark_api_key": "sk-from-settings"})
    c.update_api_key("sk-explicit")
    assert c.auth_headers()["Authorization"] == "Bearer sk-explicit"
    # update_api_key wins even after settings change.
    store["ark_api_key"] = "sk-newer"
    assert c.auth_headers()["Authorization"] == "Bearer sk-explicit"


def test_settings_callable_failure_is_swallowed(caplog) -> None:
    def _broken() -> dict[str, Any]:
        raise RuntimeError("settings backing store offline")

    c = MangaArkClient(read_settings=_broken)
    with pytest.raises(VendorError) as exc_info:
        c.auth_headers()
    assert exc_info.value.kind == "auth"
    # Failure was swallowed (logged at warning level), not propagated.
    assert any("read_settings failed" in rec.message for rec in caplog.records)


def test_endpoint_falls_back_to_default(client_factory) -> None:
    c, _ = client_factory({"ark_api_key": "sk-x"})
    assert c._current_endpoint("ep-default") == "ep-default"


def test_endpoint_overridden_by_settings(client_factory) -> None:
    c, _ = client_factory({"ark_api_key": "sk-x", "ark_endpoint_id": "ep-custom-123"})
    assert c._current_endpoint("ep-default") == "ep-custom-123"


# ─── Default endpoints + base URL constants ─────────────────────────────


def test_default_endpoints_are_strings_with_seedance_marker() -> None:
    assert "seedance" in DEFAULT_SEEDANCE_I2V.lower()
    assert "seedance" in DEFAULT_SEEDANCE_T2V.lower()
    assert "i2v" in DEFAULT_SEEDANCE_I2V.lower()
    assert "t2v" in DEFAULT_SEEDANCE_T2V.lower()


def test_base_url_is_volcengine() -> None:
    assert ARK_BASE_URL.startswith("https://ark.cn-beijing.volces.com")


# ─── submit_seedance_* input validation ─────────────────────────────────


async def test_submit_i2v_requires_prompt(client_factory) -> None:
    c, _ = client_factory({"ark_api_key": "sk-x"})
    with pytest.raises(ValueError, match="prompt is required"):
        await c.submit_seedance_i2v(prompt="", image_url="https://x/a.png")


async def test_submit_i2v_requires_image_url(client_factory) -> None:
    c, _ = client_factory({"ark_api_key": "sk-x"})
    with pytest.raises(ValueError, match="image_url is required"):
        await c.submit_seedance_i2v(prompt="x", image_url="")


async def test_submit_t2v_requires_prompt(client_factory) -> None:
    c, _ = client_factory({"ark_api_key": "sk-x"})
    with pytest.raises(ValueError, match="prompt is required"):
        await c.submit_seedance_t2v(prompt="")


# ─── extract_video_url shape compat ─────────────────────────────────────


def test_extract_video_url_singular() -> None:
    resp = {"content": {"video_url": "https://example.com/a.mp4"}}
    assert MangaArkClient.extract_video_url(resp) == "https://example.com/a.mp4"


def test_extract_video_url_plural_string_list() -> None:
    resp = {"content": {"video_urls": ["https://example.com/a.mp4", "b"]}}
    assert MangaArkClient.extract_video_url(resp) == "https://example.com/a.mp4"


def test_extract_video_url_plural_dict_list_with_url_key() -> None:
    resp = {"content": {"video_urls": [{"url": "https://x/x.mp4", "size": 100}]}}
    assert MangaArkClient.extract_video_url(resp) == "https://x/x.mp4"


def test_extract_video_url_plural_dict_list_with_video_url_key() -> None:
    resp = {"content": {"video_urls": [{"video_url": "https://y.mp4"}]}}
    assert MangaArkClient.extract_video_url(resp) == "https://y.mp4"


def test_extract_video_url_returns_none_for_missing() -> None:
    assert MangaArkClient.extract_video_url({}) is None
    assert MangaArkClient.extract_video_url({"content": {}}) is None
    assert MangaArkClient.extract_video_url({"content": {"video_urls": []}}) is None


# ─── poll_until_done timeout ────────────────────────────────────────────


async def test_poll_until_done_returns_terminal_response(monkeypatch) -> None:
    """Drive ``poll_until_done`` with a fake ``get_task`` that succeeds on
    the second call — the helper should return that payload."""

    c = MangaArkClient(read_settings=lambda: {"ark_api_key": "sk-x"})

    calls: list[str] = []

    async def fake_get_task(task_id: str) -> dict:
        calls.append(task_id)
        if len(calls) < 2:
            return {"status": "running", "progress": 50}
        return {"status": "succeeded", "content": {"video_url": "https://x/y.mp4"}}

    c.get_task = fake_get_task  # type: ignore[assignment]
    final = await c.poll_until_done("task_123", timeout_sec=5.0, poll_interval=0.01)
    assert final["status"] == "succeeded"
    assert calls == ["task_123", "task_123"]


async def test_poll_until_done_raises_timeout_vendor_error() -> None:
    c = MangaArkClient(read_settings=lambda: {"ark_api_key": "sk-x"})

    async def fake_get_task(_task_id: str) -> dict:
        return {"status": "running"}

    c.get_task = fake_get_task  # type: ignore[assignment]
    with pytest.raises(VendorError) as exc:
        await c.poll_until_done("task_123", timeout_sec=0.05, poll_interval=0.01)
    assert exc.value.kind == "timeout"


async def test_poll_until_done_invokes_progress_callback() -> None:
    c = MangaArkClient(read_settings=lambda: {"ark_api_key": "sk-x"})

    progress_payloads: list[dict] = []

    async def progress(p: dict) -> None:
        progress_payloads.append(p)

    async def fake_get_task(_task_id: str) -> dict:
        if len(progress_payloads) < 1:
            return {"status": "running", "progress": 30}
        return {"status": "succeeded"}

    c.get_task = fake_get_task  # type: ignore[assignment]
    await c.poll_until_done(
        "task_123",
        timeout_sec=5.0,
        poll_interval=0.01,
        on_progress=progress,
    )
    assert len(progress_payloads) >= 2
    assert progress_payloads[0]["status"] == "running"


async def test_poll_until_done_swallows_progress_callback_errors() -> None:
    c = MangaArkClient(read_settings=lambda: {"ark_api_key": "sk-x"})

    async def progress(_p: dict) -> None:
        raise RuntimeError("UI thread crashed")

    async def fake_get_task(_task_id: str) -> dict:
        return {"status": "succeeded"}

    c.get_task = fake_get_task  # type: ignore[assignment]
    final = await c.poll_until_done(
        "task_123", timeout_sec=5.0, poll_interval=0.01, on_progress=progress
    )
    # Despite the progress callback raising, the helper must still return
    # the terminal response (Pixelle: progress is best-effort).
    assert final["status"] == "succeeded"
