"""Tests for PptAssetProvider image backends and icon resolver."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from ppt_asset_provider import (
    ATLASCLOUD_IMAGE_SUBMIT_PATH,
    ATLASCLOUD_POLL_DELAYS,
    ATLASCLOUD_PREDICTION_PATH,
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
async def test_resolve_image_dashscope_succeeds_after_polling(tmp_path, monkeypatch) -> None:
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
    provider = _provider(tmp_path, image_provider="dashscope", dashscope_api_key="ds-key")

    path = await provider.resolve_image(query="cyberpunk city", project_id="p3")

    assert path and path.endswith(".png")
    assert state["poll_count"] >= 2


@pytest.mark.asyncio
async def test_resolve_image_atlascloud_posts_once_then_polls(tmp_path, monkeypatch) -> None:
    download_url = "https://atlas-result.example/img.png"
    state = {"post_count": 0, "poll_count": 0, "delays": []}

    async def handler(method, url, *, params, headers, json):
        if method == "POST" and url.endswith(ATLASCLOUD_IMAGE_SUBMIT_PATH):
            state["post_count"] += 1
            assert headers and headers["Authorization"] == "Bearer atlas-key"
            assert json == {
                "model": "z-image/turbo",
                "prompt": "editorial illustration",
                "size": "1536*1024",
            }
            return _make_response(payload={"data": {"id": "prediction-1"}})
        if method == "GET" and url.endswith(
            ATLASCLOUD_PREDICTION_PATH.format(prediction_id="prediction-1")
        ):
            state["poll_count"] += 1
            if state["poll_count"] == 1:
                return _make_response(payload={"data": {"status": "processing"}})
            return _make_response(
                payload={
                    "data": {
                        "status": "completed",
                        "outputs": [download_url],
                    }
                }
            )
        if url == download_url:
            return _make_response(content=b"atlas-png")
        raise AssertionError(f"unexpected url {url}")

    async def fast_sleep(seconds):
        state["delays"].append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)
    _patch_async_client(monkeypatch, handler)
    provider = _provider(
        tmp_path,
        image_provider="atlascloud",
        atlascloud_api_key="atlas-key",
    )

    path = await provider.resolve_image(query="editorial illustration", project_id="p5")

    assert path and path.endswith(".png")
    assert state["post_count"] == 1
    assert state["poll_count"] == 2
    assert state["delays"] == list(ATLASCLOUD_POLL_DELAYS[:2])


@pytest.mark.asyncio
async def test_resolve_image_atlascloud_stops_after_bounded_polling(tmp_path, monkeypatch) -> None:
    state = {"post_count": 0, "poll_count": 0}

    async def handler(method, url, *, params, headers, json):
        if method == "POST":
            state["post_count"] += 1
            return _make_response(payload={"data": {"id": "prediction-2"}})
        state["poll_count"] += 1
        return _make_response(payload={"data": {"status": "processing"}})

    async def fast_sleep(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)
    _patch_async_client(monkeypatch, handler)
    provider = _provider(
        tmp_path,
        image_provider="atlascloud",
        atlascloud_api_key="atlas-key",
    )

    assert (await provider.resolve_image(query="x", project_id="p6")) is None
    assert state["post_count"] == 1
    assert state["poll_count"] == len(ATLASCLOUD_POLL_DELAYS)


@pytest.mark.asyncio
async def test_resolve_image_swallows_exceptions(tmp_path, monkeypatch) -> None:
    async def handler(method, url, *, params, headers, json):
        raise RuntimeError("boom")

    _patch_async_client(monkeypatch, handler)
    provider = _provider(tmp_path, image_provider="pexels", pexels_api_key="key")

    assert (await provider.resolve_image(query="x", project_id="p4")) is None
