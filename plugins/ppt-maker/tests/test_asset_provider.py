"""Tests for PptAssetProvider (Pexels / Pixabay / DashScope + icon resolver)."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from ppt_asset_provider import (
    DASHSCOPE_T2I_SUBMIT,
    PEXELS_ENDPOINT,
    PIXABAY_ENDPOINT,
    PptAssetProvider,
)


def _provider(tmp_path, **settings) -> PptAssetProvider:
    return PptAssetProvider(settings=settings, data_root=tmp_path)


# ── Icon resolution ───────────────────────────────────────────────────────


def test_resolve_icon_matches_known_keyword(tmp_path) -> None:
    icon = _provider(tmp_path).resolve_icon("growth chart")
    assert icon is not None
    assert icon["keyword"] == "growth"
    assert icon["emoji"]  # non-empty glyph
    # MSO_SHAPE enum should expose at least a numeric value
    assert int(icon["shape"]) > 0


def test_resolve_icon_falls_back_to_default_for_unknown(tmp_path) -> None:
    icon = _provider(tmp_path).resolve_icon("unrelated mystery topic")
    assert icon is not None
    assert icon["keyword"] == "default"


def test_resolve_icon_returns_none_for_empty(tmp_path) -> None:
    assert _provider(tmp_path).resolve_icon("") is None
    assert _provider(tmp_path).resolve_icon(None) is None


# ── Image provider plumbing ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_image_returns_none_when_provider_disabled(tmp_path) -> None:
    provider = _provider(tmp_path, image_provider="none")
    assert (await provider.resolve_image(query="abstract", project_id="p1")) is None


@pytest.mark.asyncio
async def test_resolve_image_returns_none_when_no_query(tmp_path) -> None:
    provider = _provider(tmp_path, image_provider="pexels", pexels_api_key="xxx")
    assert (await provider.resolve_image(query="", project_id="p1")) is None


@pytest.mark.asyncio
async def test_resolve_image_returns_none_when_pexels_key_missing(tmp_path) -> None:
    provider = _provider(tmp_path, image_provider="pexels")
    assert (await provider.resolve_image(query="cat", project_id="p1")) is None


def _patch_async_client(monkeypatch, route_handler) -> None:
    """Replace ``httpx.AsyncClient`` with a tiny in-process fake."""

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, *, params=None, headers=None):
            return await route_handler("GET", url, params=params, headers=headers, json=None)

        async def post(self, url, *, json=None, headers=None):
            return await route_handler("POST", url, params=None, headers=headers, json=json)

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)


def _make_response(*, status: int = 200, payload: Any = None, content: bytes = b"binary") -> Any:
    class FakeResponse:
        def __init__(self, status_code: int, payload: Any, content_bytes: bytes) -> None:
            self.status_code = status_code
            self._payload = payload
            self.content = content_bytes

        def json(self):
            return self._payload

        def raise_for_status(self):
            if self.status_code >= 400:
                raise httpx.HTTPError(f"status={self.status_code}")

    return FakeResponse(status, payload, content)


@pytest.mark.asyncio
async def test_resolve_image_pexels_happy_path(tmp_path, monkeypatch) -> None:
    download_url = "https://images.pexels.com/photos/1/large.jpg"

    async def handler(method, url, *, params, headers, json):
        if url == PEXELS_ENDPOINT:
            assert headers and headers.get("Authorization") == "key123"
            return _make_response(payload={"photos": [{"src": {"large": download_url}}]})
        if url == download_url:
            return _make_response(content=b"jpeg-bytes")
        raise AssertionError(f"unexpected url {url}")

    _patch_async_client(monkeypatch, handler)
    provider = _provider(tmp_path, image_provider="pexels", pexels_api_key="key123")

    path = await provider.resolve_image(query="modern office", project_id="p1")

    assert path and path.endswith(".jpg")
    from pathlib import Path

    assert Path(path).exists()
    assert Path(path).read_bytes() == b"jpeg-bytes"


@pytest.mark.asyncio
async def test_resolve_image_pexels_empty_response_returns_none(tmp_path, monkeypatch) -> None:
    async def handler(method, url, *, params, headers, json):
        return _make_response(payload={"photos": []})

    _patch_async_client(monkeypatch, handler)
    provider = _provider(tmp_path, image_provider="pexels", pexels_api_key="key123")

    assert (await provider.resolve_image(query="x", project_id="p1")) is None


@pytest.mark.asyncio
async def test_resolve_image_pixabay_happy_path(tmp_path, monkeypatch) -> None:
    download_url = "https://pixabay.com/get/large.jpg"

    async def handler(method, url, *, params, headers, json):
        if url == PIXABAY_ENDPOINT:
            assert params and params.get("key") == "px-key"
            return _make_response(payload={"hits": [{"largeImageURL": download_url}]})
        if url == download_url:
            return _make_response(content=b"pix-bytes")
        raise AssertionError(f"unexpected url {url}")

    _patch_async_client(monkeypatch, handler)
    provider = _provider(tmp_path, image_provider="pixabay", pixabay_api_key="px-key")

    path = await provider.resolve_image(query="city skyline", project_id="p2")

    assert path and path.endswith(".jpg")


@pytest.mark.asyncio
async def test_resolve_image_dashscope_succeeds_after_polling(
    tmp_path, monkeypatch
) -> None:
    download_url = "https://dashscope-result.example/img.png"
    state = {"poll_count": 0}

    async def handler(method, url, *, params, headers, json):
        if method == "POST" and url == DASHSCOPE_T2I_SUBMIT:
            assert headers and headers["Authorization"].startswith("Bearer ")
            return _make_response(payload={"output": {"task_id": "task-1"}})
        if "/tasks/task-1" in url:
            state["poll_count"] += 1
            if state["poll_count"] < 2:
                return _make_response(payload={"output": {"task_status": "RUNNING"}})
            return _make_response(
                payload={
                    "output": {
                        "task_status": "SUCCEEDED",
                        "results": [{"url": download_url}],
                    }
                }
            )
        if url == download_url:
            return _make_response(content=b"png-bytes")
        raise AssertionError(f"unexpected url {url}")

    # Skip the real 2-second poll delays in tests.
    async def fast_sleep(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)
    _patch_async_client(monkeypatch, handler)
    provider = _provider(
        tmp_path, image_provider="dashscope", dashscope_api_key="ds-key"
    )

    path = await provider.resolve_image(query="cyberpunk city", project_id="p3")

    assert path and path.endswith(".png")
    assert state["poll_count"] >= 2


@pytest.mark.asyncio
async def test_resolve_image_swallows_exceptions(tmp_path, monkeypatch) -> None:
    async def handler(method, url, *, params, headers, json):
        raise RuntimeError("boom")

    _patch_async_client(monkeypatch, handler)
    provider = _provider(tmp_path, image_provider="pexels", pexels_api_key="key")

    assert (await provider.resolve_image(query="x", project_id="p4")) is None
