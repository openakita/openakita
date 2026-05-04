"""Phase 3.1 — comfy_client.py: hash-cache, lazy import, probe semantics."""

from __future__ import annotations

from typing import Any

import pytest

from comfy_client import (
    ERROR_KIND_CONFIG,
    ERROR_KIND_DEPENDENCY,
    ERROR_KIND_TIMEOUT,
    ERROR_KIND_WORKFLOW,
    MODE_ANIMATE,
    MODE_IMAGE,
    MODE_T2V,
    MangaComfyClient,
    WorkflowError,
    _first_url,
)

# ─── _first_url helper ──────────────────────────────────────────────────


def test_first_url_returns_bare_string() -> None:
    assert _first_url("https://x", ("url",)) == "https://x"


def test_first_url_strips_blank_string() -> None:
    assert _first_url("   ", ("url",)) is None


def test_first_url_unwraps_list_of_strings() -> None:
    assert _first_url(["https://x", "https://y"], ("url",)) == "https://x"


def test_first_url_unwraps_list_of_dicts_url_key() -> None:
    assert _first_url([{"url": "https://x"}], ("url", "video_url")) == "https://x"


def test_first_url_unwraps_list_of_dicts_video_url_key() -> None:
    """Comfykit's outputs sometimes carry a ``video_url`` field; the
    helper must accept either key."""
    assert _first_url([{"video_url": "https://v"}], ("url", "video_url")) == "https://v"


def test_first_url_returns_none_for_empty_list() -> None:
    assert _first_url([], ("url",)) is None


def test_first_url_returns_none_for_dict_with_unknown_keys() -> None:
    assert _first_url({"foo": "bar"}, ("url",)) is None


def test_first_url_returns_first_dict_url_key_match() -> None:
    """When the dict shape uses the bare ``url`` key (without a list)."""
    assert _first_url({"url": "https://z"}, ("url", "video_url")) == "https://z"


# ─── WorkflowError ──────────────────────────────────────────────────────


def test_workflow_error_default_kind_and_to_dict() -> None:
    exc = WorkflowError("oops")
    assert exc.kind == ERROR_KIND_WORKFLOW
    assert exc.retryable is False
    d = exc.to_dict()
    assert d["kind"] == ERROR_KIND_WORKFLOW
    assert d["message"] == "oops"
    assert d["retryable"] is False


def test_workflow_error_custom_kind_and_retryable() -> None:
    exc = WorkflowError("dep missing", kind=ERROR_KIND_DEPENDENCY, retryable=True)
    assert exc.kind == ERROR_KIND_DEPENDENCY
    assert exc.retryable is True


# ─── _resolve_backend ───────────────────────────────────────────────────


def _client_with_settings(settings: dict[str, Any]) -> MangaComfyClient:
    return MangaComfyClient(read_settings=lambda: dict(settings))


def test_resolve_backend_defaults_to_runninghub_when_unset() -> None:
    c = _client_with_settings({})
    assert c._resolve_backend({}) == "runninghub"


def test_resolve_backend_accepts_comfyui_local() -> None:
    c = _client_with_settings({"comfy_backend": "comfyui_local"})
    assert c._resolve_backend({"comfy_backend": "comfyui_local"}) == "comfyui_local"


def test_resolve_backend_rejects_unknown() -> None:
    c = _client_with_settings({"comfy_backend": "wandb"})
    with pytest.raises(WorkflowError) as exc:
        c._resolve_backend({"comfy_backend": "wandb"})
    assert exc.value.kind == ERROR_KIND_CONFIG


def test_resolve_backend_normalises_case() -> None:
    c = _client_with_settings({})
    assert c._resolve_backend({"comfy_backend": "RUNNINGHUB"}) == "runninghub"


# ─── _hash_config ───────────────────────────────────────────────────────


def test_hash_config_changes_when_api_key_changes() -> None:
    c = _client_with_settings({})
    h1 = c._hash_config({"runninghub_api_key": "k1"})
    h2 = c._hash_config({"runninghub_api_key": "k2"})
    assert h1 != h2


def test_hash_config_stable_when_unrelated_keys_change() -> None:
    """Settings dict often carries unrelated fields (TTS engine, cost
    threshold, etc.) — these must not invalidate the kit cache."""
    c = _client_with_settings({})
    h1 = c._hash_config({"runninghub_api_key": "k", "tts_engine": "edge"})
    h2 = c._hash_config({"runninghub_api_key": "k", "tts_engine": "cosyvoice"})
    assert h1 == h2


# ─── _resolve_workflow_ref ──────────────────────────────────────────────


def test_resolve_workflow_ref_runninghub_image() -> None:
    settings = {
        "comfy_backend": "runninghub",
        "runninghub_workflow_image": "wf-image-123",
    }
    c = _client_with_settings(settings)
    assert c._resolve_workflow_ref(MODE_IMAGE) == "wf-image-123"


def test_resolve_workflow_ref_runninghub_animate() -> None:
    settings = {
        "comfy_backend": "runninghub",
        "runninghub_workflow_animate": "wf-anim",
    }
    c = _client_with_settings(settings)
    assert c._resolve_workflow_ref(MODE_ANIMATE) == "wf-anim"


def test_resolve_workflow_ref_local_t2v() -> None:
    settings = {
        "comfy_backend": "comfyui_local",
        "comfyui_workflow_t2v": "/tmp/t2v.json",
    }
    c = _client_with_settings(settings)
    assert c._resolve_workflow_ref(MODE_T2V) == "/tmp/t2v.json"


def test_resolve_workflow_ref_unknown_mode_raises() -> None:
    c = _client_with_settings({})
    with pytest.raises(WorkflowError) as exc:
        c._resolve_workflow_ref("foo")
    assert exc.value.kind == ERROR_KIND_CONFIG


def test_resolve_workflow_ref_missing_settings_key_raises() -> None:
    """If the user picked RunningHub but didn't set the workflow id for
    the requested mode, we raise rather than dispatching a no-op."""
    c = _client_with_settings({"comfy_backend": "runninghub"})
    with pytest.raises(WorkflowError) as exc:
        c._resolve_workflow_ref(MODE_IMAGE)
    assert exc.value.kind == ERROR_KIND_CONFIG
    assert "runninghub_workflow_image" in str(exc.value)


# ─── _get_or_create_kit caching ─────────────────────────────────────────


class _FakeKit:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.next_result: Any = {"status": "completed", "images": ["https://img"]}

    def execute(self, workflow_ref: str, params: dict[str, Any]) -> Any:
        self.calls.append((workflow_ref, dict(params)))
        return self.next_result


def _patch_construct(monkeypatch, client: MangaComfyClient, factory) -> None:
    monkeypatch.setattr(client, "_construct_kit", factory)


def test_get_or_create_kit_caches_within_same_config(monkeypatch) -> None:
    settings = {
        "comfy_backend": "runninghub",
        "runninghub_api_key": "k",
    }
    c = _client_with_settings(settings)
    n_calls = {"n": 0}

    def factory(backend: str, cfg: dict[str, Any]) -> Any:
        n_calls["n"] += 1
        return _FakeKit()

    _patch_construct(monkeypatch, c, factory)
    k1 = c._get_or_create_kit()
    k2 = c._get_or_create_kit()
    assert k1 is k2
    assert n_calls["n"] == 1


def test_get_or_create_kit_rebuilds_when_config_changes(monkeypatch) -> None:
    bag = {"comfy_backend": "runninghub", "runninghub_api_key": "k1"}
    c = MangaComfyClient(read_settings=lambda: dict(bag))
    n_calls = {"n": 0}

    def factory(backend: str, cfg: dict[str, Any]) -> Any:
        n_calls["n"] += 1
        return _FakeKit(api_key=cfg.get("runninghub_api_key"))

    _patch_construct(monkeypatch, c, factory)
    c._get_or_create_kit()
    bag["runninghub_api_key"] = "k2"
    c._get_or_create_kit()
    assert n_calls["n"] == 2


def test_construct_kit_raises_dependency_when_comfykit_absent(monkeypatch) -> None:
    """The lazy import must surface as a WorkflowError(kind=dependency)."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kw: Any) -> Any:
        if name == "comfykit":
            raise ImportError("No module named 'comfykit'")
        return real_import(name, *args, **kw)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    settings = {"comfy_backend": "runninghub", "runninghub_api_key": "k"}
    c = _client_with_settings(settings)
    with pytest.raises(WorkflowError) as exc:
        c._construct_kit("runninghub", settings)
    assert exc.value.kind == ERROR_KIND_DEPENDENCY
    assert "comfykit" in str(exc.value)


def test_construct_kit_raises_config_when_runninghub_key_empty(monkeypatch) -> None:
    """Even with comfykit installed, an empty api_key short-circuits."""
    monkeypatch.setattr(
        "comfy_client.MangaComfyClient._construct_kit",
        MangaComfyClient._construct_kit,
    )
    c = _client_with_settings({"comfy_backend": "runninghub", "runninghub_api_key": "   "})
    with pytest.raises(WorkflowError) as exc:
        c._construct_kit("runninghub", {"runninghub_api_key": "   "})
    # Either dependency (no comfykit installed) or config (empty key).
    assert exc.value.kind in {ERROR_KIND_CONFIG, ERROR_KIND_DEPENDENCY}


def test_construct_kit_local_requires_url(monkeypatch) -> None:
    c = _client_with_settings({"comfy_backend": "comfyui_local", "comfyui_local_url": ""})
    with pytest.raises(WorkflowError) as exc:
        c._construct_kit("comfyui_local", {"comfyui_local_url": ""})
    assert exc.value.kind in {ERROR_KIND_CONFIG, ERROR_KIND_DEPENDENCY}


# ─── _validate_status ───────────────────────────────────────────────────


def test_validate_status_passes_through_completed_dict() -> None:
    res = {"status": "completed", "images": ["https://x"]}
    assert MangaComfyClient._validate_status(res, "wf") is res


def test_validate_status_passes_through_succeeded_object() -> None:
    class R:
        status = "succeeded"
        images = ["https://x"]

    r = R()
    assert MangaComfyClient._validate_status(r, "wf") is r


def test_validate_status_passes_through_when_no_status_field() -> None:
    """Some workflows return a bare dict — extractor decides if it's
    actually usable."""
    res = {"images": ["https://x"]}
    assert MangaComfyClient._validate_status(res, "wf") is res


def test_validate_status_rejects_failed() -> None:
    res = {"status": "failed", "msg": "node error"}
    with pytest.raises(WorkflowError) as exc:
        MangaComfyClient._validate_status(res, "wf")
    assert exc.value.kind == ERROR_KIND_WORKFLOW
    assert "node error" in str(exc.value)


def test_validate_status_uses_message_when_msg_absent() -> None:
    res = {"status": "ERROR", "message": "human text"}
    with pytest.raises(WorkflowError) as exc:
        MangaComfyClient._validate_status(res, "wf")
    assert "human text" in str(exc.value)


# ─── _extract_image_url / _extract_video_url ────────────────────────────


def test_extract_image_url_from_images_list() -> None:
    assert MangaComfyClient._extract_image_url({"images": ["https://x"]}) == "https://x"


def test_extract_image_url_from_outputs_list_of_dicts() -> None:
    res = {"outputs": [{"url": "https://o"}]}
    assert MangaComfyClient._extract_image_url(res) == "https://o"


def test_extract_image_url_from_object_attribute() -> None:
    class R:
        images = ["https://attr"]

    assert MangaComfyClient._extract_image_url(R()) == "https://attr"


def test_extract_video_url_prefers_videos_list() -> None:
    res = {"videos": ["https://v"], "images": ["https://i"]}
    assert MangaComfyClient._extract_video_url(res) == "https://v"


def test_extract_video_url_returns_none_when_missing() -> None:
    assert MangaComfyClient._extract_video_url({"foo": "bar"}) is None


# ─── generate_image (happy path + edge cases) ───────────────────────────


@pytest.mark.asyncio
async def test_generate_image_uses_workflow_ref_and_params(monkeypatch) -> None:
    settings = {
        "comfy_backend": "runninghub",
        "runninghub_api_key": "k",
        "runninghub_workflow_image": "wf-img",
    }
    c = _client_with_settings(settings)
    fake = _FakeKit()
    fake.next_result = {"status": "completed", "images": ["https://panel.png"]}
    _patch_construct(monkeypatch, c, lambda b, cfg: fake)

    out = await c.generate_image(
        prompt="李雷上学",
        ref_image_urls=["https://ref1", "https://ref2"],
        negative_prompt="模糊",
        size="1024*1024",
        seed=42,
    )
    assert out["image_url"] == "https://panel.png"
    assert fake.calls == [
        (
            "wf-img",
            {
                "prompt": "李雷上学",
                "negative_prompt": "模糊",
                "ref_images": ["https://ref1", "https://ref2"],
                "size": "1024*1024",
                "seed": 42,
            },
        )
    ]


@pytest.mark.asyncio
async def test_generate_image_rejects_empty_prompt() -> None:
    c = _client_with_settings({})
    with pytest.raises(WorkflowError) as exc:
        await c.generate_image(prompt="")
    assert exc.value.kind == ERROR_KIND_CONFIG


@pytest.mark.asyncio
async def test_generate_image_raises_when_workflow_returns_no_url(monkeypatch) -> None:
    settings = {
        "comfy_backend": "runninghub",
        "runninghub_api_key": "k",
        "runninghub_workflow_image": "wf-img",
    }
    c = _client_with_settings(settings)
    fake = _FakeKit()
    fake.next_result = {"status": "completed", "images": []}
    _patch_construct(monkeypatch, c, lambda b, cfg: fake)
    with pytest.raises(WorkflowError) as exc:
        await c.generate_image(prompt="x")
    assert exc.value.kind == ERROR_KIND_WORKFLOW


@pytest.mark.asyncio
async def test_generate_image_propagates_kit_failure(monkeypatch) -> None:
    settings = {
        "comfy_backend": "runninghub",
        "runninghub_api_key": "k",
        "runninghub_workflow_image": "wf-img",
    }
    c = _client_with_settings(settings)

    class CrashingKit:
        def execute(self, *a: Any, **k: Any) -> Any:
            raise RuntimeError("boom")

    _patch_construct(monkeypatch, c, lambda b, cfg: CrashingKit())
    with pytest.raises(WorkflowError) as exc:
        await c.generate_image(prompt="x")
    assert exc.value.kind == ERROR_KIND_WORKFLOW
    assert exc.value.retryable is True
    assert "boom" in str(exc.value)


@pytest.mark.asyncio
async def test_generate_image_times_out(monkeypatch) -> None:
    """Timeout maps to ERROR_KIND_TIMEOUT and retryable=True."""
    settings = {
        "comfy_backend": "runninghub",
        "runninghub_api_key": "k",
        "runninghub_workflow_image": "wf",
    }
    c = _client_with_settings(settings)

    class SlowKit:
        def execute(self, *a: Any, **k: Any) -> Any:
            import time

            time.sleep(0.5)
            return {"status": "completed", "images": ["https://x"]}

    _patch_construct(monkeypatch, c, lambda b, cfg: SlowKit())
    with pytest.raises(WorkflowError) as exc:
        await c.generate_image(prompt="x", timeout_sec=0.05)
    assert exc.value.kind == ERROR_KIND_TIMEOUT
    assert exc.value.retryable is True


# ─── generate_i2v ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_i2v_passes_params(monkeypatch) -> None:
    settings = {
        "comfy_backend": "runninghub",
        "runninghub_api_key": "k",
        "runninghub_workflow_animate": "wf-i2v",
    }
    c = _client_with_settings(settings)
    fake = _FakeKit()
    fake.next_result = {"status": "completed", "videos": ["https://v.mp4"]}
    _patch_construct(monkeypatch, c, lambda b, cfg: fake)
    out = await c.generate_i2v(
        image_url="https://panel.png",
        prompt="慢镜头摇晃",
        duration_sec=7,
        ratio="9:16",
    )
    assert out["video_url"] == "https://v.mp4"
    assert fake.calls[0][0] == "wf-i2v"
    assert fake.calls[0][1]["image_url"] == "https://panel.png"
    assert fake.calls[0][1]["duration"] == 7


@pytest.mark.asyncio
async def test_generate_i2v_requires_image_url() -> None:
    c = _client_with_settings({})
    with pytest.raises(WorkflowError):
        await c.generate_i2v(image_url="", prompt="x")


@pytest.mark.asyncio
async def test_generate_i2v_clamps_zero_duration(monkeypatch) -> None:
    settings = {
        "comfy_backend": "runninghub",
        "runninghub_api_key": "k",
        "runninghub_workflow_animate": "wf-i2v",
    }
    c = _client_with_settings(settings)
    fake = _FakeKit()
    fake.next_result = {"status": "completed", "videos": ["https://v"]}
    _patch_construct(monkeypatch, c, lambda b, cfg: fake)
    await c.generate_i2v(image_url="https://x", prompt="y", duration_sec=0)
    assert fake.calls[0][1]["duration"] == 1


# ─── generate_t2v ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_t2v_uses_t2v_workflow_key(monkeypatch) -> None:
    settings = {
        "comfy_backend": "runninghub",
        "runninghub_api_key": "k",
        "runninghub_workflow_t2v": "wf-t2v",
        "runninghub_workflow_animate": "wf-i2v",
    }
    c = _client_with_settings(settings)
    fake = _FakeKit()
    fake.next_result = {"status": "completed", "videos": ["https://t2v.mp4"]}
    _patch_construct(monkeypatch, c, lambda b, cfg: fake)
    out = await c.generate_t2v(prompt="壮观的剑舞", duration_sec=5)
    assert out["video_url"] == "https://t2v.mp4"
    assert fake.calls[0][0] == "wf-t2v"


# ─── probe_backend ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_probe_runninghub_missing_key() -> None:
    c = _client_with_settings({"comfy_backend": "runninghub", "runninghub_api_key": ""})
    out = await c.probe_backend()
    assert out["ok"] is False
    assert out["backend"] == "runninghub"
    assert "runninghub_api_key" in out["message"]


@pytest.mark.asyncio
async def test_probe_runninghub_constructs_kit_only(monkeypatch) -> None:
    """Probing must NOT issue a billable workflow run — just construct the kit."""
    settings = {"comfy_backend": "runninghub", "runninghub_api_key": "k"}
    c = _client_with_settings(settings)
    fake = _FakeKit()
    _patch_construct(monkeypatch, c, lambda b, cfg: fake)
    out = await c.probe_backend()
    assert out["ok"] is True
    assert out["backend"] == "runninghub"
    assert fake.calls == []  # No execute call.


@pytest.mark.asyncio
async def test_probe_runninghub_surfaces_dependency_error(monkeypatch) -> None:
    settings = {"comfy_backend": "runninghub", "runninghub_api_key": "k"}
    c = _client_with_settings(settings)

    def boom(backend: str, cfg: dict[str, Any]) -> Any:
        raise WorkflowError("comfykit missing", kind=ERROR_KIND_DEPENDENCY)

    _patch_construct(monkeypatch, c, boom)
    out = await c.probe_backend()
    assert out["ok"] is False
    assert out["backend"] == "runninghub"
    assert "comfykit" in out["message"]


@pytest.mark.asyncio
async def test_probe_unknown_backend_does_not_crash() -> None:
    c = _client_with_settings({"comfy_backend": "wandb"})
    out = await c.probe_backend()
    assert out["ok"] is False
    assert out["backend"] == "unknown"


@pytest.mark.asyncio
async def test_probe_comfyui_local_missing_url() -> None:
    c = _client_with_settings({"comfy_backend": "comfyui_local", "comfyui_local_url": ""})
    out = await c.probe_backend()
    assert out["ok"] is False
    assert out["backend"] == "comfyui_local"
    assert "comfyui_local_url" in out["message"]
