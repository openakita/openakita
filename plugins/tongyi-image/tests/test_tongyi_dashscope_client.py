"""Unit tests for ``tongyi_dashscope_client.DashScopeClient``.

Uses ``httpx.MockTransport`` so no real network call is made — every
endpoint method is exercised against a deterministic fake server.

The goal is contract coverage:

  * Headers / auth are wired correctly (Authorization, async-mode flag,
    runtime API-key swap)
  * ``_post`` classifies success / API-side error / HTTP-side error
    consistently into ``DashScopeError`` (with status_code populated for
    HTTP-side, zero for API-side)
  * Each public method routes to the right endpoint with the right body
    shape (model + input + parameters), and only forwards optional fields
    when callers actually pass them (catches "always-on parameter" bugs)
  * ``validate_key`` is permissive (200/400/404 all mean "key works")
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

_HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HERE))

# Imported from conftest.py.
from conftest import make_dashscope_client  # noqa: E402
from tongyi_dashscope_client import (  # noqa: E402
    DASHSCOPE_BASE_URL,
    EP_BG_GEN,
    EP_IMAGE_GEN,
    EP_IMAGE_SYNTH,
    EP_MULTIMODAL,
    EP_OUTPAINT,
    DashScopeClient,
    DashScopeError,
)

# ── Recording handler builder ────────────────────────────────────────


def _recorder(*, status: int = 200, body: dict | None = None,
              raise_exc: Exception | None = None):
    """Build (handler, captured) where ``captured`` accumulates Requests.

    The handler returns the same canned response for every call — tests
    that need varying responses build their own handler inline.
    """
    captured: list[httpx.Request] = []
    canned = body if body is not None else {"output": {"task_id": "ds-001"}, "request_id": "r-1"}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if raise_exc is not None:
            raise raise_exc
        return httpx.Response(status, json=canned)

    return handler, captured


# ── headers / auth ───────────────────────────────────────────────────


def test_make_headers_default() -> None:
    client = DashScopeClient("sk-abc")
    h = client._make_headers()
    assert h["Authorization"] == "Bearer sk-abc"
    assert h["Content-Type"] == "application/json"
    assert "X-DashScope-Async" not in h


def test_make_headers_async_flag() -> None:
    client = DashScopeClient("sk-abc")
    h = client._make_headers(async_mode=True)
    assert h["X-DashScope-Async"] == "enable"


def test_base_url_strips_trailing_slash() -> None:
    """Defensive: callers may pass either trailing-slash or not — both
    must produce identical request paths to avoid double slashes that
    DashScope's gateway 404s on."""
    a = DashScopeClient("k", base_url="https://api/")
    b = DashScopeClient("k", base_url="https://api")
    assert a._base_url == b._base_url == "https://api"


@pytest.mark.asyncio
async def test_update_api_key_rewrites_authorization_header() -> None:
    """Runtime key swap: the user updates their key in Settings and the
    next outgoing request must carry the NEW bearer, not the old one."""
    handler, captured = _recorder()
    client = await make_dashscope_client(handler, api_key="sk-old")
    try:
        client.update_api_key("sk-new")
        await client._client.get("/tasks/x")  # any request to capture headers
        assert captured[-1].headers["Authorization"] == "Bearer sk-new"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_close_is_safe_after_request() -> None:
    handler, _ = _recorder()
    client = await make_dashscope_client(handler)
    await client.close()
    # Idempotent close — must not crash on the second call.
    await client._client.aclose()


# ── _post error classification ──────────────────────────────────────


@pytest.mark.asyncio
async def test_post_returns_data_on_success() -> None:
    body = {"output": {"task_id": "abc"}, "request_id": "r-9"}
    handler, _ = _recorder(status=200, body=body)
    client = await make_dashscope_client(handler)
    try:
        result = await client._post(EP_MULTIMODAL, {"model": "x", "input": {}})
        assert result == body
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_post_raises_on_http_error_status() -> None:
    """Non-200 from gateway → raise with status_code populated."""
    handler, _ = _recorder(status=401,
                            body={"code": "InvalidApiKey", "message": "bad key"})
    client = await make_dashscope_client(handler)
    try:
        with pytest.raises(DashScopeError) as exc:
            await client._post(EP_MULTIMODAL, {})
        assert exc.value.code == "InvalidApiKey"
        assert exc.value.message == "bad key"
        assert exc.value.status_code == 401
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_post_raises_on_api_error_in_200_body() -> None:
    """DashScope sometimes returns ``200 OK`` with a ``code`` field set —
    this is an application-level error and must still raise."""
    handler, _ = _recorder(status=200,
                            body={"code": "DataInspectionFailed",
                                  "message": "moderation triggered"})
    client = await make_dashscope_client(handler)
    try:
        with pytest.raises(DashScopeError) as exc:
            await client._post(EP_MULTIMODAL, {})
        assert exc.value.code == "DataInspectionFailed"
        assert exc.value.message == "moderation triggered"
        # status_code defaults to 0 for API-side errors so callers can
        # tell HTTP-vs-app errors apart.
        assert exc.value.status_code == 0
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_post_handles_missing_code_in_error() -> None:
    """Defensive: gateway 5xx may return prose with no JSON code field —
    must still raise with the HTTP status code as the default."""
    handler, _ = _recorder(status=503,
                            body={"message": "gateway timeout"})
    client = await make_dashscope_client(handler)
    try:
        with pytest.raises(DashScopeError) as exc:
            await client._post(EP_MULTIMODAL, {})
        assert exc.value.code == "503"
        assert exc.value.status_code == 503
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_post_async_mode_adds_dashscope_header() -> None:
    handler, captured = _recorder()
    client = await make_dashscope_client(handler)
    try:
        await client._post(EP_IMAGE_GEN, {"model": "x"}, async_mode=True)
        assert captured[-1].headers["X-DashScope-Async"] == "enable"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_post_sync_mode_omits_dashscope_async_header() -> None:
    """Sync calls must NOT send the async flag — sending it puts the task
    in the wrong queue and the response shape changes."""
    handler, captured = _recorder()
    client = await make_dashscope_client(handler)
    try:
        await client._post(EP_MULTIMODAL, {"model": "x"})
        assert captured[-1].headers.get("X-DashScope-Async") is None
    finally:
        await client.close()


# ── DashScopeError shape ─────────────────────────────────────────────


def test_dashscope_error_str_format() -> None:
    err = DashScopeError("InvalidApiKey", "bad key", 401)
    assert err.code == "InvalidApiKey"
    assert err.message == "bad key"
    assert err.status_code == 401
    assert "InvalidApiKey" in str(err)
    assert "bad key" in str(err)


def test_dashscope_error_default_status() -> None:
    err = DashScopeError("X", "y")
    assert err.status_code == 0


# ── Endpoint A: generate_image (sync multimodal) ─────────────────────


@pytest.mark.asyncio
async def test_generate_image_minimal_body() -> None:
    handler, captured = _recorder()
    client = await make_dashscope_client(handler)
    try:
        await client.generate_image(
            model="wan27-pro",
            messages=[{"role": "user", "content": [{"text": "a cat"}]}],
        )
    finally:
        await client.close()

    req = captured[-1]
    # httpx prepends ``base_url`` (``/api/v1``) to the path on dispatch,
    # so we assert the route-level suffix instead of full equality.
    assert req.url.path.endswith(EP_MULTIMODAL)
    body = json.loads(req.content)
    assert body["model"] == "wan27-pro"
    assert body["input"]["messages"][0]["role"] == "user"
    # Parameters must include the always-on n + watermark, but NOTHING else
    # when no optional kwargs are passed — guards against an "always-on
    # field" regression that would burn quota on parameters the caller
    # explicitly left out.
    params = body["parameters"]
    assert params == {"n": 1, "watermark": False}


@pytest.mark.asyncio
async def test_generate_image_forwards_optional_params() -> None:
    handler, captured = _recorder()
    client = await make_dashscope_client(handler)
    try:
        await client.generate_image(
            model="wan27-pro",
            messages=[],
            size="1024*1024",
            n=2,
            negative_prompt="blurry",
            prompt_extend=True,
            seed=42,
            thinking_mode=True,
            enable_sequential=False,
            color_palette=[{"hex": "#ff0000"}],
            bbox_list=[[0, 0, 100, 100]],
            watermark=True,
        )
    finally:
        await client.close()

    body = json.loads(captured[-1].content)
    p = body["parameters"]
    assert p["size"] == "1024*1024"
    assert p["n"] == 2
    assert p["watermark"] is True
    assert p["negative_prompt"] == "blurry"
    assert p["prompt_extend"] is True
    assert p["seed"] == 42
    assert p["thinking_mode"] is True
    assert p["enable_sequential"] is False
    assert p["color_palette"] == [{"hex": "#ff0000"}]
    assert p["bbox_list"] == [[0, 0, 100, 100]]


@pytest.mark.asyncio
async def test_generate_image_omits_size_when_blank() -> None:
    """Empty-string size must be DROPPED, not forwarded — DashScope
    rejects "" as 422 InvalidParameter."""
    handler, captured = _recorder()
    client = await make_dashscope_client(handler)
    try:
        await client.generate_image(model="wan27-pro", messages=[], size="")
    finally:
        await client.close()
    body = json.loads(captured[-1].content)
    assert "size" not in body["parameters"]


# ── Endpoint B: generate_image_async ─────────────────────────────────


@pytest.mark.asyncio
async def test_generate_image_async_uses_async_endpoint_and_header() -> None:
    handler, captured = _recorder()
    client = await make_dashscope_client(handler)
    try:
        await client.generate_image_async(
            model="wan27-pro", messages=[], size="2K", n=4,
        )
    finally:
        await client.close()

    req = captured[-1]
    assert req.url.path.endswith(EP_IMAGE_GEN)
    assert req.headers["X-DashScope-Async"] == "enable"
    body = json.loads(req.content)
    assert body["model"] == "wan27-pro"
    assert body["parameters"]["size"] == "2K"
    assert body["parameters"]["n"] == 4


# ── Endpoint B: style_repaint ────────────────────────────────────────


@pytest.mark.asyncio
async def test_style_repaint_basic() -> None:
    handler, captured = _recorder()
    client = await make_dashscope_client(handler)
    try:
        await client.style_repaint("http://x/face.png", style_index=2)
    finally:
        await client.close()

    req = captured[-1]
    assert req.url.path.endswith(EP_IMAGE_GEN)
    assert req.headers["X-DashScope-Async"] == "enable"
    body = json.loads(req.content)
    assert body["model"] == "wanx-style-repaint-v1"
    assert body["input"]["image_url"] == "http://x/face.png"
    assert body["input"]["style_index"] == 2
    assert "style_ref_url" not in body["input"]


@pytest.mark.asyncio
async def test_style_repaint_custom_style_passes_ref_url() -> None:
    """style_index=-1 means "use my reference image"; the ref URL must
    only be forwarded in that exact mode (otherwise DashScope ignores
    or 422s)."""
    handler, captured = _recorder()
    client = await make_dashscope_client(handler)
    try:
        await client.style_repaint(
            "http://x/face.png", style_index=-1,
            style_ref_url="http://x/style.png",
        )
    finally:
        await client.close()
    body = json.loads(captured[-1].content)
    assert body["input"]["style_ref_url"] == "http://x/style.png"


@pytest.mark.asyncio
async def test_style_repaint_drops_ref_url_when_style_index_not_minus_one() -> None:
    handler, captured = _recorder()
    client = await make_dashscope_client(handler)
    try:
        await client.style_repaint(
            "http://x/face.png", style_index=5,
            style_ref_url="http://x/style.png",
        )
    finally:
        await client.close()
    body = json.loads(captured[-1].content)
    assert "style_ref_url" not in body["input"]


# ── Endpoint C: generate_background ──────────────────────────────────


@pytest.mark.asyncio
async def test_generate_background_basic() -> None:
    handler, captured = _recorder()
    client = await make_dashscope_client(handler)
    try:
        await client.generate_background(
            "http://x/product.png",
            ref_prompt="elegant marble background",
        )
    finally:
        await client.close()

    req = captured[-1]
    assert req.url.path.endswith(EP_BG_GEN)
    assert req.headers["X-DashScope-Async"] == "enable"
    body = json.loads(req.content)
    assert body["model"] == "wanx-background-generation-v2"
    assert body["input"]["base_image_url"] == "http://x/product.png"
    assert body["input"]["ref_prompt"] == "elegant marble background"
    p = body["parameters"]
    assert p["model_version"] == "v3"
    assert p["n"] == 1
    # noise_level only relevant when ref_image_url is supplied — must NOT
    # leak in when caller only passed ref_prompt.
    assert "noise_level" not in p
    assert "ref_prompt_weight" not in p


@pytest.mark.asyncio
async def test_generate_background_with_ref_image_adds_noise_level() -> None:
    handler, captured = _recorder()
    client = await make_dashscope_client(handler)
    try:
        await client.generate_background(
            "http://x/product.png",
            ref_image_url="http://x/bg.png",
            noise_level=200,
        )
    finally:
        await client.close()
    body = json.loads(captured[-1].content)
    assert body["input"]["ref_image_url"] == "http://x/bg.png"
    assert body["parameters"]["noise_level"] == 200


@pytest.mark.asyncio
async def test_generate_background_with_both_refs_adds_weight() -> None:
    handler, captured = _recorder()
    client = await make_dashscope_client(handler)
    try:
        await client.generate_background(
            "http://x/product.png",
            ref_prompt="hello",
            ref_image_url="http://x/bg.png",
            ref_prompt_weight=0.7,
        )
    finally:
        await client.close()
    p = json.loads(captured[-1].content)["parameters"]
    assert p["ref_prompt_weight"] == 0.7


@pytest.mark.asyncio
async def test_generate_background_edge_block_built_only_when_present() -> None:
    """``reference_edge`` is an optional sub-object; emitting an empty
    one would change the API contract and may trigger a 422."""
    handler, captured = _recorder()
    client = await make_dashscope_client(handler)
    try:
        await client.generate_background(
            "http://x/p.png",
            foreground_edge=["edge1.png"],
            foreground_edge_prompt=["hint"],
        )
    finally:
        await client.close()
    body = json.loads(captured[-1].content)
    edge = body["input"]["reference_edge"]
    assert edge["foreground_edge"] == ["edge1.png"]
    assert edge["foreground_edge_prompt"] == ["hint"]
    assert "background_edge" not in edge


@pytest.mark.asyncio
async def test_generate_background_no_edge_block_when_no_edge_inputs() -> None:
    handler, captured = _recorder()
    client = await make_dashscope_client(handler)
    try:
        await client.generate_background("http://x/p.png")
    finally:
        await client.close()
    body = json.loads(captured[-1].content)
    assert "reference_edge" not in body["input"]


# ── Endpoint D: outpaint ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_outpaint_basic() -> None:
    handler, captured = _recorder()
    client = await make_dashscope_client(handler)
    try:
        await client.outpaint("http://x/photo.png", output_ratio="16:9")
    finally:
        await client.close()
    req = captured[-1]
    assert req.url.path.endswith(EP_OUTPAINT)
    assert req.headers["X-DashScope-Async"] == "enable"
    body = json.loads(req.content)
    assert body["model"] == "image-out-painting"
    assert body["input"]["image_url"] == "http://x/photo.png"
    p = body["parameters"]
    assert p["output_ratio"] == "16:9"
    assert p["best_quality"] is False
    assert p["limit_image_size"] is True


@pytest.mark.asyncio
async def test_outpaint_forwards_pixel_offsets_and_scales() -> None:
    handler, captured = _recorder()
    client = await make_dashscope_client(handler)
    try:
        await client.outpaint(
            "http://x/photo.png",
            x_scale=1.5, y_scale=1.2, angle=15,
            left_offset=100, right_offset=100,
            top_offset=50, bottom_offset=50,
            best_quality=True, limit_image_size=False,
        )
    finally:
        await client.close()
    p = json.loads(captured[-1].content)["parameters"]
    assert p["x_scale"] == 1.5
    assert p["y_scale"] == 1.2
    assert p["angle"] == 15
    assert p["left_offset"] == 100
    assert p["right_offset"] == 100
    assert p["top_offset"] == 50
    assert p["bottom_offset"] == 50
    assert p["best_quality"] is True
    assert p["limit_image_size"] is False


@pytest.mark.asyncio
async def test_outpaint_omits_zero_angle() -> None:
    """``angle=0`` is the default — must NOT be forwarded so DashScope
    can apply its own auto-correction."""
    handler, captured = _recorder()
    client = await make_dashscope_client(handler)
    try:
        await client.outpaint("http://x/photo.png", angle=0)
    finally:
        await client.close()
    p = json.loads(captured[-1].content)["parameters"]
    assert "angle" not in p


# ── Endpoint E: sketch_to_image / wan25_edit ─────────────────────────


@pytest.mark.asyncio
async def test_sketch_to_image_body() -> None:
    handler, captured = _recorder()
    client = await make_dashscope_client(handler)
    try:
        await client.sketch_to_image(
            "http://x/sketch.png", "a watercolor cat",
            style="<oilpainting>", size="1024*1024", n=2, sketch_weight=5,
        )
    finally:
        await client.close()
    req = captured[-1]
    assert req.url.path.endswith(EP_IMAGE_SYNTH)
    assert req.headers["X-DashScope-Async"] == "enable"
    body = json.loads(req.content)
    assert body["model"] == "wanx-sketch-to-image-lite"
    assert body["input"]["sketch_image_url"] == "http://x/sketch.png"
    assert body["input"]["prompt"] == "a watercolor cat"
    p = body["parameters"]
    assert p["style"] == "<oilpainting>"
    assert p["size"] == "1024*1024"
    assert p["n"] == 2
    assert p["sketch_weight"] == 5


@pytest.mark.asyncio
async def test_wan25_edit_body() -> None:
    handler, captured = _recorder()
    client = await make_dashscope_client(handler)
    try:
        await client.wan25_edit(
            "make it night",
            ["http://x/a.png", "http://x/b.png"],
            n=3, size="1024*1024", negative_prompt="day",
            prompt_extend=False, watermark=True, seed=99,
        )
    finally:
        await client.close()
    req = captured[-1]
    assert req.url.path.endswith(EP_IMAGE_SYNTH)
    assert req.headers["X-DashScope-Async"] == "enable"
    body = json.loads(req.content)
    assert body["model"] == "wan2.5-i2i-preview"
    assert body["input"]["prompt"] == "make it night"
    assert body["input"]["images"] == ["http://x/a.png", "http://x/b.png"]
    p = body["parameters"]
    assert p["n"] == 3
    assert p["size"] == "1024*1024"
    assert p["negative_prompt"] == "day"
    assert p["prompt_extend"] is False
    assert p["watermark"] is True
    assert p["seed"] == 99


# ── get_task ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_task_returns_data_on_success() -> None:
    handler, captured = _recorder(body={"output": {"task_status": "SUCCEEDED"}})
    client = await make_dashscope_client(handler)
    try:
        out = await client.get_task("ds-001")
    finally:
        await client.close()
    assert captured[-1].url.path.endswith("/tasks/ds-001")
    assert captured[-1].method == "GET"
    assert out["output"]["task_status"] == "SUCCEEDED"


@pytest.mark.asyncio
async def test_get_task_raises_on_http_error() -> None:
    handler, _ = _recorder(status=404,
                            body={"code": "TaskNotFound", "message": "missing"})
    client = await make_dashscope_client(handler)
    try:
        with pytest.raises(DashScopeError) as exc:
            await client.get_task("nonexistent")
        assert exc.value.code == "TaskNotFound"
        assert exc.value.status_code == 404
    finally:
        await client.close()


# ── validate_key ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_validate_key_returns_true_on_200() -> None:
    handler, _ = _recorder(status=200)
    client = await make_dashscope_client(handler)
    try:
        assert await client.validate_key() is True
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_validate_key_returns_true_on_404() -> None:
    """The probe URL is intentionally bogus — 404 means "key is valid, ID
    does not exist" which is the SUCCESS signal we want."""
    handler, _ = _recorder(status=404)
    client = await make_dashscope_client(handler)
    try:
        assert await client.validate_key() is True
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_validate_key_returns_true_on_400() -> None:
    handler, _ = _recorder(status=400)
    client = await make_dashscope_client(handler)
    try:
        assert await client.validate_key() is True
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_validate_key_returns_false_on_401() -> None:
    """401 Unauthorized is the actual "bad key" signal."""
    handler, _ = _recorder(status=401)
    client = await make_dashscope_client(handler)
    try:
        assert await client.validate_key() is False
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_validate_key_returns_false_on_500() -> None:
    handler, _ = _recorder(status=500)
    client = await make_dashscope_client(handler)
    try:
        assert await client.validate_key() is False
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_validate_key_returns_false_on_network_error() -> None:
    """Network blip during probe must NOT report "bad key" — it returns
    False so the UI can surface "couldn't reach DashScope"."""
    handler, _ = _recorder(raise_exc=httpx.ConnectError("dns failed"))
    client = await make_dashscope_client(handler)
    try:
        assert await client.validate_key() is False
    finally:
        await client.close()


# ── endpoint constants are stable ────────────────────────────────────


def test_endpoint_constants_match_dashscope_paths() -> None:
    """Guard against accidental rename — DashScope's gateway is fussy
    and these paths are not idempotent across versions."""
    assert DASHSCOPE_BASE_URL == "https://dashscope.aliyuncs.com/api/v1"
    assert EP_MULTIMODAL == "/services/aigc/multimodal-generation/generation"
    assert EP_IMAGE_GEN == "/services/aigc/image-generation/generation"
    # NOTE: EP_BG_GEN intentionally has a trailing slash — DashScope
    # background-generation endpoint requires it; removing breaks 404.
    assert EP_BG_GEN == "/services/aigc/background-generation/generation/"
    assert EP_OUTPAINT == "/services/aigc/image2image/out-painting"
    assert EP_IMAGE_SYNTH == "/services/aigc/image2image/image-synthesis"
