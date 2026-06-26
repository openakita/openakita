"""Unit tests for ``avatar_dashscope_client.AvatarDashScopeClient``.

Uses ``httpx.MockTransport`` so no real network call is made. Covers:

- auth-headers wired correctly (Bearer + async-mode flag), refreshed by
  ``read_settings`` callable (Pixelle A10 hot reload).
- 4 ``submit_*`` business methods build the right body / hit the right
  endpoint and return the DashScope ``task_id``.
- ``query_task`` extracts ``output_url`` via the three-shape probe
  (``video_url`` / ``image_url`` / ``results[0].url``).
- ``face_detect`` raises ``dependency`` when DashScope says the input is
  not a humanoid (avoids a wasted s2v charge).
- ``submit_image_edit`` rejects 0 or >3 reference images with a 422
  ``client`` error.
- DashScope quota / dependency error bodies are promoted from the generic
  ``client`` / ``server`` kind to the avatar-studio specific kinds by
  ``_classify_dashscope_body``.
- ``cancel_task`` records the cancel flag locally even when the remote
  call fails.
- ``has_api_key`` reflects the ``read_settings`` callable's current value
  (no caching beyond the single call).
"""

from __future__ import annotations

import json

import httpx
import pytest
from avatar_dashscope_client import (
    DASHSCOPE_BASE_URL_BJ,
    MODEL_ANIMATE_MIX,
    MODEL_I2I,
    MODEL_S2V,
    MODEL_S2V_DETECT,
    MODEL_VIDEORETALK,
    PATH_ANIMATE_MIX_SUBMIT,
    PATH_I2I_SUBMIT,
    PATH_S2V_DETECT,
    PATH_S2V_SUBMIT,
    PATH_TASK_CANCEL,
    PATH_TASK_QUERY,
    PATH_VIDEORETALK_SUBMIT,
    AvatarDashScopeClient,
    _classify_dashscope_body,
)
from avatar_studio_inline.vendor_client import (
    ERROR_KIND_CLIENT,
    ERROR_KIND_SERVER,
    VendorError,
)

# ── helpers ─────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _patch_async_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force every ``httpx.AsyncClient(...)`` to use the per-test transport."""
    container: dict[str, httpx.MockTransport] = {}
    real_init = httpx.AsyncClient.__init__

    def patched_init(self: httpx.AsyncClient, *args: object, **kwargs: object) -> None:
        if "transport" not in kwargs and "transport" in container:
            kwargs["transport"] = container["transport"]
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)
    pytest.transport_container = container  # type: ignore[attr-defined]


def _install(transport: httpx.MockTransport) -> None:
    pytest.transport_container["transport"] = transport  # type: ignore[attr-defined]


def _make_client(api_key: str = "sk-test", *, base_url: str | None = None) -> AvatarDashScopeClient:
    settings: dict[str, object] = {
        "api_key": api_key,
        "base_url": base_url or DASHSCOPE_BASE_URL_BJ,
        "timeout": 5.0,
    }
    c = AvatarDashScopeClient(read_settings=lambda: settings, max_retries=0)
    c.retry_backoff = 0.001
    c.retry_max_backoff = 0.005
    return c


# ── auth + hot-reload ───────────────────────────────────────────────────


def test_auth_headers_includes_bearer_but_not_async() -> None:
    c = _make_client("sk-abc")
    h = c.auth_headers()
    assert h["Authorization"] == "Bearer sk-abc"
    assert h["Content-Type"] == "application/json"
    assert "X-DashScope-Async" not in h, (
        "X-DashScope-Async must NOT be in auth_headers; "
        "sync endpoints like face_detect reject it with 403"
    )


def test_async_header_only_in_submit_async() -> None:
    c = _make_client("sk-abc")
    assert c._ASYNC_HEADER == {"X-DashScope-Async": "enable"}


def test_settings_refreshed_each_call_pixelle_a10() -> None:
    state = {"key": "k1"}
    c = AvatarDashScopeClient(read_settings=lambda: {"api_key": state["key"]})
    assert c.auth_headers()["Authorization"] == "Bearer k1"
    state["key"] = "k2"
    assert c.auth_headers()["Authorization"] == "Bearer k2"


def test_settings_callable_failure_falls_back_to_defaults() -> None:
    def boom() -> dict[str, object]:
        raise RuntimeError("settings db locked")

    c = AvatarDashScopeClient(read_settings=boom)
    h = c.auth_headers()
    # Empty API key yields a bare empty Authorization (caller sees auth
    # error from the vendor); the important part is that we don't crash.
    assert h["Authorization"] == ""
    assert c.base_url == DASHSCOPE_BASE_URL_BJ


def test_has_api_key_reflects_callable() -> None:
    state = {"api_key": ""}
    c = AvatarDashScopeClient(read_settings=lambda: state)
    assert c.has_api_key() is False
    state["api_key"] = "sk-x"
    assert c.has_api_key() is True


def test_update_api_key_validates_type() -> None:
    c = _make_client()
    c.update_api_key("sk-new")
    assert c._last_settings["api_key"] == "sk-new"
    with pytest.raises(TypeError):
        c.update_api_key(123)  # type: ignore[arg-type]


# ── submit_s2v ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_submit_s2v_builds_body_and_returns_task_id() -> None:
    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        return httpx.Response(200, json={"output": {"task_id": "t-123"}})

    _install(httpx.MockTransport(handler))
    c = _make_client()
    tid = await c.submit_s2v(
        image_url="https://x/a.png",
        audio_url="https://x/a.mp3",
        resolution="720P",
        duration=8.5,
    )
    assert tid == "t-123"
    assert captured[0].url.path == PATH_S2V_SUBMIT
    body = json.loads(captured[0].content.decode())
    assert body["model"] == MODEL_S2V
    assert body["input"]["image_url"] == "https://x/a.png"
    assert body["input"]["audio_url"] == "https://x/a.mp3"
    assert body["parameters"]["resolution"] == "720P"
    assert body["parameters"]["duration"] == 8.5


@pytest.mark.asyncio
async def test_submit_s2v_omits_duration_when_unset() -> None:
    import json

    captured: list[bytes] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(req.content)
        return httpx.Response(200, json={"output": {"task_id": "t-1"}})

    _install(httpx.MockTransport(handler))
    c = _make_client()
    await c.submit_s2v(image_url="x", audio_url="y")
    body = json.loads(captured[0].decode())
    assert "duration" not in body["parameters"]


# ── submit_videoretalk / animate_mix / image_edit ───────────────────────


@pytest.mark.asyncio
async def test_submit_videoretalk_routes_correctly() -> None:
    import json

    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        return httpx.Response(200, json={"output": {"task_id": "vr-1"}})

    _install(httpx.MockTransport(handler))
    c = _make_client()
    tid = await c.submit_videoretalk(video_url="https://x/v.mp4", audio_url="https://x/a.mp3")
    assert tid == "vr-1"
    # Regression guard: must hit the same image2video/video-synthesis
    # endpoint as s2v / animate-mix — model differentiates, not path.
    assert captured[0].url.path == PATH_VIDEORETALK_SUBMIT
    assert captured[0].url.path == "/api/v1/services/aigc/image2video/video-synthesis"
    body = json.loads(captured[0].content.decode())
    assert body["model"] == MODEL_VIDEORETALK
    assert body["input"] == {"video_url": "https://x/v.mp4", "audio_url": "https://x/a.mp3"}
    # ``video_extension=true`` lets DashScope handle audio>video by
    # looping the source video, which is what the UI expects.
    assert body["parameters"]["video_extension"] is True


@pytest.mark.asyncio
async def test_submit_animate_mix_pro_mode() -> None:
    import json

    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        return httpx.Response(200, json={"output": {"task_id": "am-1"}})

    _install(httpx.MockTransport(handler))
    c = _make_client()
    tid = await c.submit_animate_mix(
        image_url="https://x/i.png",
        video_url="https://x/v.mp4",
        mode_pro=True,
        watermark=True,
    )
    assert tid == "am-1"
    assert captured[0].url.path == PATH_ANIMATE_MIX_SUBMIT
    body = json.loads(captured[0].content.decode())
    assert body["model"] == MODEL_ANIMATE_MIX
    assert body["parameters"] == {"mode": "wan-pro", "watermark": True}


@pytest.mark.asyncio
async def test_submit_image_edit_validates_ref_count() -> None:
    c = _make_client()
    with pytest.raises(VendorError) as ei:
        await c.submit_image_edit(prompt="p", ref_images_url=[])
    assert ei.value.status == 422
    assert ei.value.kind == ERROR_KIND_CLIENT

    with pytest.raises(VendorError):
        await c.submit_image_edit(prompt="p", ref_images_url=["a", "b", "c", "d"])


@pytest.mark.asyncio
async def test_submit_image_edit_routes_to_i2i_endpoint() -> None:
    import json

    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        return httpx.Response(200, json={"output": {"task_id": "i2i-1"}})

    _install(httpx.MockTransport(handler))
    c = _make_client()
    tid = await c.submit_image_edit(
        prompt="merge them",
        ref_images_url=["https://x/1.png", "https://x/2.png"],
        size="1024*1024",
    )
    assert tid == "i2i-1"
    assert captured[0].url.path == PATH_I2I_SUBMIT
    body = json.loads(captured[0].content.decode())
    assert body["model"] == MODEL_I2I
    assert body["parameters"]["size"] == "1024*1024"
    assert body["parameters"]["n"] == 1
    assert body["input"]["prompt"] == "merge them"
    # Regression guard: wan2.5-i2i-preview's official body field is
    # ``images`` (not ``ref_images_url`` — that's wan2.7-image-edit's
    # multimodal-generation schema). Sending the wrong key produced
    # the upstream error
    # ``image compose failed: images field is required``.
    assert body["input"]["images"] == ["https://x/1.png", "https://x/2.png"]
    assert "ref_images_url" not in body["input"]


# ── face_detect ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_face_detect_passes_when_humanoid() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"output": {"check_pass": True, "humanoid": True}},
        )

    _install(httpx.MockTransport(handler))
    c = _make_client()
    out = await c.face_detect("https://x/face.png")
    assert out["check_pass"] is True
    assert out["humanoid"] is True


@pytest.mark.asyncio
async def test_face_detect_rejects_non_humanoid_with_dependency_kind() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == PATH_S2V_DETECT
        return httpx.Response(
            200,
            json={"output": {"check_pass": True, "humanoid": False}},
        )

    _install(httpx.MockTransport(handler))
    c = _make_client()
    with pytest.raises(VendorError) as ei:
        await c.face_detect("https://x/cat.png")
    assert ei.value.kind == "dependency"


@pytest.mark.asyncio
async def test_face_detect_uses_correct_model_id() -> None:
    import json

    captured: list[bytes] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(req.content)
        return httpx.Response(
            200,
            json={"output": {"check_pass": True, "humanoid": True}},
        )

    _install(httpx.MockTransport(handler))
    c = _make_client()
    await c.face_detect("https://x/f.png")
    body = json.loads(captured[0].decode())
    assert body["model"] == MODEL_S2V_DETECT


# ── query_task: 3-shape output URL extraction ───────────────────────────


@pytest.mark.asyncio
async def test_query_task_extracts_video_url() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "output": {
                    "task_id": "t-1",
                    "task_status": "SUCCEEDED",
                    "video_url": "https://cdn/x.mp4",
                },
                "usage": {"video_duration": 5},
            },
        )

    _install(httpx.MockTransport(handler))
    c = _make_client()
    res = await c.query_task("t-1")
    assert res["status"] == "SUCCEEDED"
    assert res["is_done"] and res["is_ok"]
    assert res["output_url"] == "https://cdn/x.mp4"
    assert res["output_kind"] == "video"
    assert res["usage"] == {"video_duration": 5}


@pytest.mark.asyncio
async def test_query_task_extracts_image_url() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "output": {
                    "task_status": "SUCCEEDED",
                    "image_url": "https://cdn/x.png",
                },
            },
        )

    _install(httpx.MockTransport(handler))
    c = _make_client()
    res = await c.query_task("t-2")
    assert res["output_url"] == "https://cdn/x.png"
    assert res["output_kind"] == "image"


@pytest.mark.asyncio
async def test_query_task_extracts_results_array() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "output": {
                    "task_status": "SUCCEEDED",
                    "results": [{"url": "https://cdn/x.mp4"}],
                },
            },
        )

    _install(httpx.MockTransport(handler))
    c = _make_client()
    res = await c.query_task("t-3")
    assert res["output_url"] == "https://cdn/x.mp4"
    assert res["output_kind"] == "video"


@pytest.mark.asyncio
async def test_query_task_routes_to_task_endpoint() -> None:
    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        return httpx.Response(200, json={"output": {"task_status": "PENDING"}})

    _install(httpx.MockTransport(handler))
    c = _make_client()
    res = await c.query_task("xyz-9")
    assert captured[0].method == "GET"
    assert captured[0].url.path == PATH_TASK_QUERY.format(id="xyz-9")
    assert res["status"] == "PENDING"
    assert not res["is_done"]


@pytest.mark.asyncio
async def test_query_task_failure_classifies_quota_kind() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "output": {
                    "task_status": "FAILED",
                    "code": "Quota.Exceeded",
                    "message": "your account balance is insufficient",
                },
            },
        )

    _install(httpx.MockTransport(handler))
    c = _make_client()
    res = await c.query_task("q-1")
    assert res["status"] == "FAILED"
    assert res["error_kind"] == "quota"


# ── _classify_dashscope_body promotion ──────────────────────────────────


def test_classify_promotes_quota() -> None:
    body = {"code": "Quota.Exceeded", "message": "balance is insufficient"}
    assert _classify_dashscope_body(body, ERROR_KIND_CLIENT) == "quota"


def test_classify_promotes_dependency_for_humanoid_failure() -> None:
    body = {"message": "humanoid not detected in input image"}
    assert _classify_dashscope_body(body, ERROR_KIND_SERVER) == "dependency"


def test_classify_promotes_dependency_for_data_inspection() -> None:
    # DashScope's content/format moderation. Documented as a 「dependency」
    # in our taxonomy because the user has to fix the input (re-encode,
    # change file type, swap photo) — retrying with the same URL never
    # works, so the UI must NOT classify it as a transient server fault.
    for code in (
        "InvalidParameter.DataInspection",
        "DataInspectionFailed",
        "Data_Inspection.Reject",
    ):
        body = {"code": code, "message": "media format unsupported"}
        assert _classify_dashscope_body(body, ERROR_KIND_SERVER) == "dependency"


def test_classify_falls_back_when_unrecognised() -> None:
    body = {"message": "some other server hiccup"}
    assert _classify_dashscope_body(body, ERROR_KIND_SERVER) == ERROR_KIND_SERVER


def test_classify_handles_non_dict_body() -> None:
    assert _classify_dashscope_body("plain text error", ERROR_KIND_CLIENT) == ERROR_KIND_CLIENT
    assert _classify_dashscope_body(None, ERROR_KIND_SERVER) == ERROR_KIND_SERVER


# ── cancel ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_task_records_locally_even_if_remote_fails() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "task not found"})

    _install(httpx.MockTransport(handler))
    c = _make_client()
    ok = await c.cancel_task("t-x")
    assert ok is False
    assert c.is_cancelled("t-x") is True
    c.clear_cancelled("t-x")
    assert c.is_cancelled("t-x") is False


@pytest.mark.asyncio
async def test_cancel_task_routes_correctly() -> None:
    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        return httpx.Response(200, json={"output": {"task_status": "CANCELED"}})

    _install(httpx.MockTransport(handler))
    c = _make_client()
    ok = await c.cancel_task("t-cancel")
    assert ok is True
    assert captured[0].method == "POST"
    assert captured[0].url.path == PATH_TASK_CANCEL.format(id="t-cancel")


# ── submit_s2v serialised by Semaphore(1) ───────────────────────────────


@pytest.mark.asyncio
async def test_submits_serialised_by_semaphore() -> None:
    """Two concurrent submits must run one-after-the-other, never overlap."""
    import asyncio

    in_flight = {"n": 0, "max": 0}

    async def slow(req: httpx.Request) -> httpx.Response:
        in_flight["n"] += 1
        in_flight["max"] = max(in_flight["max"], in_flight["n"])
        await asyncio.sleep(0.05)
        in_flight["n"] -= 1
        return httpx.Response(200, json={"output": {"task_id": "t-x"}})

    _install(httpx.MockTransport(slow))
    c = _make_client()
    await asyncio.gather(
        c.submit_s2v(image_url="a", audio_url="b"),
        c.submit_s2v(image_url="c", audio_url="d"),
    )
    assert in_flight["max"] == 1
