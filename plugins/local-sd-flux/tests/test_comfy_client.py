"""Unit tests for ``ComfyClient``.

We mock httpx via :class:`unittest.mock.AsyncMock` rather than spinning
up a fake HTTP server — keeps tests fast and removes a port collision
risk on CI runners.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _cc():
    import comfy_client
    return comfy_client


# ── construction & defaults ────────────────────────────────────────────


def test_default_base_url_is_local_comfyui() -> None:
    cc = _cc()
    assert cc.DEFAULT_BASE_URL == "http://127.0.0.1:8188"


def test_client_picks_up_auth_token() -> None:
    cc = _cc()
    c = cc.ComfyClient(auth_token="abc")
    assert c.auth_headers() == {"Authorization": "Bearer abc"}


def test_client_without_auth_returns_empty_headers() -> None:
    cc = _cc()
    c = cc.ComfyClient()
    assert c.auth_headers() == {}


def test_client_id_auto_generated_when_not_supplied() -> None:
    cc = _cc()
    c1 = cc.ComfyClient()
    c2 = cc.ComfyClient()
    assert len(c1.client_id) >= 8
    assert c1.client_id != c2.client_id


def test_client_id_honors_explicit_value() -> None:
    cc = _cc()
    c = cc.ComfyClient(client_id="explicit-id")
    assert c.client_id == "explicit-id"


# ── parse_history_outputs ──────────────────────────────────────────────


def test_parse_history_outputs_flattens_image_lists() -> None:
    cc = _cc()
    history = {
        "outputs": {
            "7": {
                "images": [
                    {"filename": "a.png", "subfolder": "", "type": "output"},
                    {"filename": "b.png", "subfolder": "sub", "type": "output"},
                ],
            },
            "9": {
                "images": [
                    {"filename": "c.png", "subfolder": "", "type": "output"},
                ],
            },
        },
    }
    result = cc.ComfyClient.parse_history_outputs("p1", history)
    assert result.prompt_id == "p1"
    names = [img.filename for img in result.images]
    assert "a.png" in names and "b.png" in names and "c.png" in names


def test_parse_history_outputs_handles_empty_history() -> None:
    cc = _cc()
    result = cc.ComfyClient.parse_history_outputs("p1", {})
    assert result.images == []


def test_parse_history_outputs_skips_node_without_images() -> None:
    cc = _cc()
    history = {"outputs": {"5": {"images": []}, "6": {}, "7": None}}
    result = cc.ComfyClient.parse_history_outputs("p1", history)
    assert result.images == []


def test_is_history_complete_requires_outputs_key() -> None:
    cc = _cc()
    assert cc.ComfyClient.is_history_complete({"outputs": {"7": {}}}) is True
    assert cc.ComfyClient.is_history_complete({"outputs": {}}) is False
    assert cc.ComfyClient.is_history_complete({}) is False


# ── view_url / ComfyOutputImage ────────────────────────────────────────


def test_view_url_includes_filename_subfolder_type() -> None:
    cc = _cc()
    c = cc.ComfyClient(base_url="http://localhost:9000/")
    img = cc.ComfyOutputImage(filename="x y.png", subfolder="sub", type="output")
    url = c.view_url(img)
    assert url.startswith("http://localhost:9000/view?")
    assert "filename=x+y.png" in url or "filename=x%20y.png" in url
    assert "subfolder=sub" in url
    assert "type=output" in url


def test_comfy_output_image_view_query_minimal() -> None:
    cc = _cc()
    img = cc.ComfyOutputImage(filename="a.png", subfolder="", type="output")
    q = img.view_query()
    assert q["filename"] == "a.png"
    assert q["type"] == "output"


# ── submit_prompt (mocked HTTP) ────────────────────────────────────────


@pytest.mark.asyncio
async def test_submit_prompt_returns_prompt_id() -> None:
    cc = _cc()
    c = cc.ComfyClient()

    async def _fake_post(path, body, **kw):
        assert path == "/prompt"
        assert body["client_id"] == c.client_id
        return {"prompt_id": "abc-123", "node_errors": {}}

    with patch.object(c, "post_json", new=AsyncMock(side_effect=_fake_post)):
        pid = await c.submit_prompt({"1": {"class_type": "Foo", "inputs": {}}})
    assert pid == "abc-123"


@pytest.mark.asyncio
async def test_submit_prompt_raises_on_node_errors() -> None:
    cc = _cc()
    c = cc.ComfyClient()
    with patch.object(
        c, "post_json",
        new=AsyncMock(return_value={"prompt_id": "x", "node_errors": {"5": "bad input"}}),
    ):
        with pytest.raises(cc.VendorError) as exc:
            await c.submit_prompt({})
        assert exc.value.kind == "client"


@pytest.mark.asyncio
async def test_submit_prompt_raises_on_empty_prompt_id() -> None:
    cc = _cc()
    c = cc.ComfyClient()
    with patch.object(c, "post_json", new=AsyncMock(return_value={"node_errors": {}})):
        with pytest.raises(cc.VendorError) as exc:
            await c.submit_prompt({})
        assert exc.value.kind == "server"


# ── get_history (mocked) ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_history_unwraps_outer_keying() -> None:
    cc = _cc()
    c = cc.ComfyClient()
    payload = {"abc-123": {"outputs": {"7": {"images": []}}}}
    with patch.object(c, "get_json", new=AsyncMock(return_value=payload)):
        h = await c.get_history("abc-123")
        assert h == {"outputs": {"7": {"images": []}}}


@pytest.mark.asyncio
async def test_get_history_passes_through_when_not_keyed() -> None:
    cc = _cc()
    c = cc.ComfyClient()
    with patch.object(c, "get_json", new=AsyncMock(return_value={"some": "thing"})):
        h = await c.get_history("missing-id")
        assert h == {"some": "thing"}


@pytest.mark.asyncio
async def test_get_history_handles_empty_response() -> None:
    cc = _cc()
    c = cc.ComfyClient()
    with patch.object(c, "get_json", new=AsyncMock(return_value=None)):
        assert await c.get_history("p1") == {}


# ── system_stats / queue / cancel ───────────────────────────────────────


@pytest.mark.asyncio
async def test_system_stats_proxies_to_get_json() -> None:
    cc = _cc()
    c = cc.ComfyClient()
    fake = {"devices": [{"vram_total": 1, "vram_free": 1}]}
    with patch.object(c, "get_json", new=AsyncMock(return_value=fake)):
        assert await c.system_stats() == fake


@pytest.mark.asyncio
async def test_cancel_task_calls_interrupt() -> None:
    cc = _cc()
    c = cc.ComfyClient()
    with patch.object(c, "post_json", new=AsyncMock(return_value={})) as p:
        ok = await c.cancel_task("any-id")
        assert ok is True
        p.assert_awaited_once()
        called_path = p.await_args.args[0]
        assert called_path == "/interrupt"


# ── download_image_bytes (mocked httpx) ─────────────────────────────────


@pytest.mark.asyncio
async def test_download_image_bytes_returns_response_content() -> None:
    cc = _cc()
    c = cc.ComfyClient()
    img = cc.ComfyOutputImage(filename="a.png", subfolder="", type="output")

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.content = b"\x89PNG\x00\x00data"

    fake_client = MagicMock()
    fake_client.get = AsyncMock(return_value=fake_resp)

    class _Ctx:
        async def __aenter__(self_inner):
            return fake_client
        async def __aexit__(self_inner, *_a):
            return False

    with patch("httpx.AsyncClient", return_value=_Ctx()):
        data = await c.download_image_bytes(img)
    assert data == b"\x89PNG\x00\x00data"


@pytest.mark.asyncio
async def test_download_image_bytes_raises_on_http_error() -> None:
    cc = _cc()
    c = cc.ComfyClient()
    img = cc.ComfyOutputImage(filename="a.png", subfolder="", type="output")

    fake_resp = MagicMock()
    fake_resp.status_code = 500
    fake_resp.text = "boom"

    fake_client = MagicMock()
    fake_client.get = AsyncMock(return_value=fake_resp)

    class _Ctx:
        async def __aenter__(self_inner):
            return fake_client
        async def __aexit__(self_inner, *_a):
            return False

    with patch("httpx.AsyncClient", return_value=_Ctx()):
        with pytest.raises(cc.VendorError):
            await c.download_image_bytes(img)
