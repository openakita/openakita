"""Phase 2.3 — direct_wanxiang_client.py: hot-reload, ref-image bounds, URL probe."""

from __future__ import annotations

from typing import Any

import pytest

from direct_wanxiang_client import (
    DASHSCOPE_BASE_URL_BJ,
    DASHSCOPE_BASE_URL_SG,
    DEFAULT_MODEL,
    DEFAULT_MODEL_PRO,
    MangaWanxiangClient,
    _classify_dashscope_body,
)
from manga_inline.vendor_client import VendorError


@pytest.fixture
def client_factory():
    def _make(initial: dict[str, Any] | None = None) -> tuple[MangaWanxiangClient, dict]:
        store: dict[str, Any] = dict(initial or {})
        c = MangaWanxiangClient(read_settings=lambda: dict(store))
        return c, store

    return _make


# ─── Hot-reload base URL + auth ─────────────────────────────────────────


def test_default_region_uses_beijing(client_factory) -> None:
    c, _ = client_factory({"dashscope_api_key": "sk-x"})
    c._refresh_settings()
    assert c.base_url == DASHSCOPE_BASE_URL_BJ


def test_singapore_region_switches_base_url(client_factory) -> None:
    c, store = client_factory({"dashscope_api_key": "sk-x", "dashscope_region": "singapore"})
    c._refresh_settings()
    assert c.base_url == DASHSCOPE_BASE_URL_SG
    # Hot-swap back.
    store["dashscope_region"] = "beijing"
    c._refresh_settings()
    assert c.base_url == DASHSCOPE_BASE_URL_BJ


def test_auth_headers_uses_settings_callable(client_factory) -> None:
    c, store = client_factory({"dashscope_api_key": "ds-abcd"})
    h = c.auth_headers()
    assert h["Authorization"] == "Bearer ds-abcd"
    assert h["Content-Type"] == "application/json"
    store["dashscope_api_key"] = "ds-newer"
    assert c.auth_headers()["Authorization"] == "Bearer ds-newer"


def test_auth_headers_raises_when_key_missing(client_factory) -> None:
    c, _ = client_factory({})
    with pytest.raises(VendorError) as exc_info:
        c.auth_headers()
    assert exc_info.value.kind == "auth"


def test_has_api_key_reflects_settings(client_factory) -> None:
    c, store = client_factory({})
    assert c.has_api_key() is False
    store["dashscope_api_key"] = "ds-now"
    assert c.has_api_key() is True


# ─── submit_image input validation ──────────────────────────────────────


async def test_submit_image_requires_prompt(client_factory) -> None:
    c, _ = client_factory({"dashscope_api_key": "sk-x"})
    with pytest.raises(ValueError, match="prompt is required"):
        await c.submit_image(prompt="")


async def test_submit_image_rejects_too_many_refs(client_factory) -> None:
    c, _ = client_factory({"dashscope_api_key": "sk-x"})
    with pytest.raises(VendorError) as exc:
        await c.submit_image(prompt="x", ref_images_url=["u"] * 10)
    assert exc.value.kind == "client"


async def test_submit_image_accepts_zero_refs(client_factory, monkeypatch) -> None:
    """0 reference images = pure text-to-image; the body shape MUST omit
    image entries entirely so DashScope doesn't reject for empty image_url."""
    c, _ = client_factory({"dashscope_api_key": "sk-x"})

    captured: list[tuple[str, dict]] = []

    async def fake_post_json(path: str, *, json_body: dict, **_kw: Any) -> dict:
        captured.append((path, json_body))
        return {"output": {"task_id": "task_xyz"}}

    monkeypatch.setattr(c, "post_json", fake_post_json)
    tid = await c.submit_image(prompt="一个少女在樱花树下")
    assert tid == "task_xyz"
    assert len(captured) == 1
    body = captured[0][1]
    content = body["input"]["messages"][0]["content"]
    assert content == [{"text": "一个少女在樱花树下"}]


async def test_submit_image_with_refs_appends_image_blocks(client_factory, monkeypatch) -> None:
    c, _ = client_factory({"dashscope_api_key": "sk-x"})

    captured: dict[str, Any] = {}

    async def fake_post_json(path: str, *, json_body: dict, **_kw: Any) -> dict:
        captured["body"] = json_body
        return {"output": {"task_id": "task_xyz"}}

    monkeypatch.setattr(c, "post_json", fake_post_json)
    await c.submit_image(
        prompt="x",
        ref_images_url=["https://x/a.png", "https://x/b.png"],
    )
    content = captured["body"]["input"]["messages"][0]["content"]
    assert content == [
        {"text": "x"},
        {"image": "https://x/a.png"},
        {"image": "https://x/b.png"},
    ]


async def test_submit_image_rejects_empty_ref_url(client_factory) -> None:
    c, _ = client_factory({"dashscope_api_key": "sk-x"})
    with pytest.raises(ValueError, match="non-empty strings"):
        await c.submit_image(prompt="x", ref_images_url=["", "y"])


async def test_submit_image_invalid_n(client_factory) -> None:
    c, _ = client_factory({"dashscope_api_key": "sk-x"})
    with pytest.raises(ValueError, match="n must be in 1..4"):
        await c.submit_image(prompt="x", n=5)


async def test_submit_image_unknown_model(client_factory) -> None:
    c, _ = client_factory({"dashscope_api_key": "sk-x"})
    with pytest.raises(ValueError, match="unknown model"):
        await c.submit_image(prompt="x", model="bogus-model")


async def test_submit_image_raises_when_task_id_missing(client_factory, monkeypatch) -> None:
    c, _ = client_factory({"dashscope_api_key": "sk-x"})

    async def fake_post_json(*_a: Any, **_kw: Any) -> dict:
        return {"output": {}}  # no task_id

    monkeypatch.setattr(c, "post_json", fake_post_json)
    with pytest.raises(VendorError) as exc:
        await c.submit_image(prompt="x")
    assert exc.value.kind == "unknown"
    assert "task_id" in str(exc.value)


# ─── extract_output_image_url shape probe ───────────────────────────────


def test_extract_url_shape_image_url() -> None:
    out = {"image_url": "https://example.com/a.png"}
    assert MangaWanxiangClient.extract_output_image_url(out) == (
        "https://example.com/a.png",
        "image",
    )


def test_extract_url_shape_video_url() -> None:
    out = {"video_url": "https://example.com/a.mp4"}
    assert MangaWanxiangClient.extract_output_image_url(out) == (
        "https://example.com/a.mp4",
        "video",
    )


def test_extract_url_shape_results_dict() -> None:
    out = {"results": {"url": "https://x/b.png"}}
    assert MangaWanxiangClient.extract_output_image_url(out) == (
        "https://x/b.png",
        "image",
    )


def test_extract_url_shape_results_list() -> None:
    out = {"results": [{"url": "https://x/c.png"}, {"url": "https://x/d.png"}]}
    assert MangaWanxiangClient.extract_output_image_url(out) == (
        "https://x/c.png",
        "image",
    )


def test_extract_url_returns_none_for_missing() -> None:
    assert MangaWanxiangClient.extract_output_image_url({}) == (None, None)
    assert MangaWanxiangClient.extract_output_image_url({"results": []}) == (None, None)


def test_extract_url_picks_video_kind_from_extension() -> None:
    out = {"results": [{"url": "https://x/a.mp4"}]}
    assert MangaWanxiangClient.extract_output_image_url(out) == (
        "https://x/a.mp4",
        "video",
    )


# ─── _classify_dashscope_body promotes generic kinds ──────────────────


def test_classify_quota_from_message() -> None:
    body = {"code": "QuotaExceeded", "message": "balance insufficient"}
    assert _classify_dashscope_body(body, "client") == "quota"


def test_classify_content_violation_from_inspection_code() -> None:
    body = {"code": "DataInspectionFailed", "message": "..."}
    assert _classify_dashscope_body(body, "server") == "content_violation"


def test_classify_dependency_from_humanoid_message() -> None:
    body = {"code": "Server.Error", "message": "humanoid detection failed"}
    assert _classify_dashscope_body(body, "server") == "dependency"


def test_classify_unknown_passes_through_fallback() -> None:
    body = {"code": "Some.UnseenCode", "message": "?"}
    assert _classify_dashscope_body(body, "server") == "server"


def test_classify_non_dict_returns_fallback() -> None:
    assert _classify_dashscope_body("not a dict", "client") == "client"


# ─── poll_until_done ─────────────────────────────────────────────────────


async def test_poll_until_done_returns_terminal(monkeypatch) -> None:
    c = MangaWanxiangClient(read_settings=lambda: {"dashscope_api_key": "sk-x"})
    calls: list[str] = []

    async def fake_query(task_id: str) -> dict:
        calls.append(task_id)
        if len(calls) == 1:
            return {
                "task_id": task_id,
                "status": "RUNNING",
                "is_done": False,
                "is_ok": False,
                "usage": {},
                "raw": {},
            }
        return {
            "task_id": task_id,
            "status": "SUCCEEDED",
            "is_done": True,
            "is_ok": True,
            "usage": {},
            "raw": {},
            "output_url": "https://x.png",
            "output_kind": "image",
        }

    c.query_task = fake_query  # type: ignore[assignment]
    final = await c.poll_until_done("t_1", timeout_sec=5.0, poll_interval=0.01)
    assert final["status"] == "SUCCEEDED"
    assert calls == ["t_1", "t_1"]


async def test_poll_until_done_timeout() -> None:
    c = MangaWanxiangClient(read_settings=lambda: {"dashscope_api_key": "sk-x"})

    async def fake_query(_task_id: str) -> dict:
        return {
            "task_id": "t",
            "status": "RUNNING",
            "is_done": False,
            "is_ok": False,
            "usage": {},
            "raw": {},
        }

    c.query_task = fake_query  # type: ignore[assignment]
    with pytest.raises(VendorError) as exc:
        await c.poll_until_done("t", timeout_sec=0.05, poll_interval=0.01)
    assert exc.value.kind == "timeout"


# ─── Sanity: model constants stay in sync with PRICE_TABLE ──────────────


def test_default_model_constants_match_price_table() -> None:
    """DEFAULT_MODEL / DEFAULT_MODEL_PRO must each have an entry in
    manga_models.PRICE_TABLE — otherwise the cost preview will silently
    omit the image line."""
    from manga_models import PRICE_TABLE

    assert DEFAULT_MODEL in PRICE_TABLE
    assert DEFAULT_MODEL_PRO in PRICE_TABLE
