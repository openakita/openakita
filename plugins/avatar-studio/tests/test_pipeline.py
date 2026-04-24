"""Pipeline tests for ``avatar_pipeline.run_pipeline``.

We mock the DashScope client (no network) and the task manager (in-memory
SQLite). Each of the four modes gets a happy-path test plus targeted
short-circuit / error / cancel tests:

- ``photo_speak``    → s2v with TTS-audio-duration honoured (Pixelle P1)
- ``video_relip``    → videoretalk skipping face_detect / image_compose
- ``video_reface``   → animate-mix with no TTS at all
- ``avatar_compose`` → i2i → detect → s2v chain
- approval gate raises ``ApprovalRequired`` then resumes when approved
- failure paths populate error_kind via ``hint_for``
- cancellation mid-poll raises ``CancelledError`` → ``status='cancelled'``
- emit() is invoked for every state transition
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from avatar_pipeline import (
    ApprovalRequired,
    AvatarPipelineContext,
    PollSchedule,
    _ctx_payload,
    _emit,
    run_pipeline,
)
from avatar_studio_inline.vendor_client import VendorError
from avatar_task_manager import AvatarTaskManager

# ─── Fakes ──────────────────────────────────────────────────────────────


class FakeClient:
    """Minimal stand-in for ``AvatarDashScopeClient`` — zero real I/O."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.cancelled: set[str] = set()
        self.face_detect_should_pass = True
        self.next_task_id = "ds-1"
        self.poll_responses: list[dict[str, Any]] = []
        self.synth_audio = b"FAKE_MP3_BYTES"
        self.synth_format = "mp3"
        self.image_edit_url = "https://cdn/composed.png"
        self.video_url = "https://cdn/output.mp4"
        # Optional: forced exception per method.
        self.raise_on: dict[str, BaseException] = {}

    def is_cancelled(self, task_id: str) -> bool:
        return task_id in self.cancelled

    async def cancel_task(self, task_id: str) -> bool:
        self.cancelled.add(task_id)
        return True

    async def face_detect(self, image_url: str) -> dict[str, Any]:
        self.calls.append(("face_detect", {"image_url": image_url}))
        if "face_detect" in self.raise_on:
            raise self.raise_on["face_detect"]
        if not self.face_detect_should_pass:
            raise VendorError(
                "humanoid not detected",
                status=200,
                retryable=False,
                kind="dependency",
            )
        return {"check_pass": True, "humanoid": True, "raw": {}}

    async def synth_voice(self, *, text: str, voice_id: str, format: str = "mp3") -> dict[str, Any]:
        self.calls.append(("synth_voice", {"text": text, "voice_id": voice_id}))
        if "synth_voice" in self.raise_on:
            raise self.raise_on["synth_voice"]
        return {"audio_bytes": self.synth_audio, "format": format, "duration_sec": None}

    async def submit_s2v(self, **kw: Any) -> str:
        self.calls.append(("submit_s2v", dict(kw)))
        if "submit_s2v" in self.raise_on:
            raise self.raise_on["submit_s2v"]
        return self.next_task_id

    async def submit_videoretalk(self, **kw: Any) -> str:
        self.calls.append(("submit_videoretalk", dict(kw)))
        return self.next_task_id

    async def submit_animate_mix(self, **kw: Any) -> str:
        self.calls.append(("submit_animate_mix", dict(kw)))
        return self.next_task_id

    async def submit_image_edit(self, **kw: Any) -> str:
        self.calls.append(("submit_image_edit", dict(kw)))
        return "i2i-1"

    async def query_task(self, task_id: str) -> dict[str, Any]:
        self.calls.append(("query_task", {"task_id": task_id}))
        if not self.poll_responses:
            # default: immediate success with the right shape
            url = self.image_edit_url if task_id == "i2i-1" else self.video_url
            kind = "image" if task_id == "i2i-1" else "video"
            return {
                "task_id": task_id,
                "status": "SUCCEEDED",
                "is_done": True,
                "is_ok": True,
                "output_url": url,
                "output_kind": kind,
                "usage": {"video_duration": 5.0} if kind == "video" else {},
            }
        return self.poll_responses.pop(0)


# ─── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
async def tm(tmp_path: Path) -> AvatarTaskManager:
    mgr = AvatarTaskManager(tmp_path / "avatar.db")
    await mgr.init()
    yield mgr
    await mgr.close()


@pytest.fixture
def emit_recorder() -> tuple[Callable[..., Any], list[tuple[str, dict[str, Any]]]]:
    events: list[tuple[str, dict[str, Any]]] = []

    def emit(event: str, payload: dict[str, Any]) -> None:
        events.append((event, payload))

    return emit, events


# ─── Helpers ────────────────────────────────────────────────────────────


async def _fake_oss_upload(_path: Path, fname: str) -> str:
    """Stand-in for the OssUploader the plugin layer normally injects.

    The pipeline's TTS step *requires* a callable on
    ``ctx.params['_oss_upload_audio']`` (otherwise it errors out with
    "OSS not configured"). Tests don't exercise the OSS network path,
    so we hand back a stable fake URL that satisfies the public-URL
    validator in ``_step_prepare_assets``.
    """
    return f"https://oss.example.com/tts/{fname}"


async def _make_ctx(
    tm: AvatarTaskManager, mode: str, params: dict[str, Any]
) -> AvatarPipelineContext:
    # Persist only the JSON-serialisable subset (the create_task DAO
    # json-dumps the dict). Then inject non-serialisable runtime hooks
    # like ``_oss_upload_audio`` directly onto the in-memory context —
    # this mirrors how plugin.py wires the real OssUploader at task-
    # spawn time without trying to round-trip a callable through SQLite.
    task_id = await tm.create_task(mode=mode, params=params)
    runtime_params = {**params, "_oss_upload_audio": _fake_oss_upload}
    return AvatarPipelineContext(task_id=task_id, mode=mode, params=runtime_params)


_FAST_POLL = PollSchedule(
    fast_interval_sec=0.001,
    fast_until_sec=0.005,
    medium_interval_sec=0.001,
    medium_until_sec=0.01,
    slow_interval_sec=0.001,
    total_timeout_sec=2.0,
)


# ─── photo_speak (mode 1) ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_photo_speak_happy_path_passes_audio_duration_to_s2v(
    tmp_path: Path,
    tm: AvatarTaskManager,
    emit_recorder: tuple[Callable[..., Any], list[tuple[str, dict[str, Any]]]],
) -> None:
    """Pixelle P1 acceptance: TTS-audio-duration MUST be forwarded to s2v."""
    emit, events = emit_recorder
    client = FakeClient()
    ctx = await _make_ctx(
        tm,
        "photo_speak",
        {
            "assets": {"image_url": "https://oss.example.com/x/portrait.png"},
            "text": "hello world",
            "voice_id": "longxiaochun",
            "resolution": "720P",
        },
    )

    async def get_dur(_: Path) -> float:
        return 7.25

    out = await run_pipeline(
        ctx,
        tm=tm,
        client=client,  # type: ignore[arg-type]
        emit=emit,
        plugin_id="avatar-studio",
        base_data_dir=tmp_path,
        get_audio_duration=get_dur,
        poll=_FAST_POLL,
    )
    assert out.error_kind is None
    assert out.output_url == client.video_url
    assert out.tts_audio_duration_sec == 7.25
    assert out.video_duration_sec == 5.0

    # P1: the s2v submit MUST have received duration=7.25.
    s2v_call = next(c for c in client.calls if c[0] == "submit_s2v")
    assert s2v_call[1]["duration"] == 7.25
    assert s2v_call[1]["resolution"] == "720P"

    # face-detect ran exactly once before s2v.
    detect_idx = next(i for i, c in enumerate(client.calls) if c[0] == "face_detect")
    s2v_idx = next(i for i, c in enumerate(client.calls) if c[0] == "submit_s2v")
    assert detect_idx < s2v_idx

    # task row was updated to succeeded
    row = await tm.get_task(ctx.task_id)
    assert row is not None
    assert row["status"] == "succeeded"
    assert row["output_url"] == client.video_url

    # at least one task_update emitted
    assert any(e == "task_update" for e, _ in events)


@pytest.mark.asyncio
async def test_photo_speak_face_detect_failure_classifies_dependency(
    tmp_path: Path,
    tm: AvatarTaskManager,
    emit_recorder: tuple[Callable[..., Any], list[tuple[str, dict[str, Any]]]],
) -> None:
    emit, _ = emit_recorder
    client = FakeClient()
    client.face_detect_should_pass = False
    ctx = await _make_ctx(
        tm,
        "photo_speak",
        {
            "assets": {"image_url": "https://oss.example.com/x/portrait.png"},
            "text": "hi",
            "voice_id": "longxiaochun",
        },
    )
    out = await run_pipeline(
        ctx,
        tm=tm,
        client=client,
        emit=emit,  # type: ignore[arg-type]
        base_data_dir=tmp_path,
        poll=_FAST_POLL,
    )
    assert out.error_kind == "dependency"
    assert out.error_hints is not None and out.error_hints["title_zh"]
    # never got as far as s2v
    assert not any(c[0] == "submit_s2v" for c in client.calls)
    row = await tm.get_task(ctx.task_id)
    assert row is not None
    assert row["status"] == "failed"


# ─── video_relip (mode 2) ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_video_relip_skips_face_detect_and_image_compose(
    tmp_path: Path,
    tm: AvatarTaskManager,
    emit_recorder: tuple[Callable[..., Any], list[tuple[str, dict[str, Any]]]],
) -> None:
    emit, _ = emit_recorder
    client = FakeClient()
    ctx = await _make_ctx(
        tm,
        "video_relip",
        {
            "assets": {"video_url": "https://oss.example.com/x/in.mp4"},
            "text": "你好",
            "voice_id": "longxiaobai",
        },
    )

    async def get_dur(_: Path) -> float:
        return 4.0

    out = await run_pipeline(
        ctx,
        tm=tm,
        client=client,
        emit=emit,  # type: ignore[arg-type]
        base_data_dir=tmp_path,
        get_audio_duration=get_dur,
        poll=_FAST_POLL,
    )
    assert out.error_kind is None
    assert out.output_url == client.video_url
    methods = [c[0] for c in client.calls]
    assert "face_detect" not in methods
    assert "submit_image_edit" not in methods
    assert "submit_videoretalk" in methods


# ─── video_reface (mode 3) ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_video_reface_no_tts_no_detect(
    tmp_path: Path,
    tm: AvatarTaskManager,
    emit_recorder: tuple[Callable[..., Any], list[tuple[str, dict[str, Any]]]],
) -> None:
    emit, _ = emit_recorder
    client = FakeClient()
    ctx = await _make_ctx(
        tm,
        "video_reface",
        {
            "assets": {
                "image_url": "https://oss.example.com/x/actor.png",
                "video_url": "https://oss.example.com/x/scene.mp4",
            },
            # 5s × 0.60 = 3元 (under threshold) so no approval gate fires.
            "video_duration_sec": 5,
            "mode_pro": False,
            "watermark": False,
        },
    )
    ctx.cost_approved = True  # belt-and-braces in case threshold tightens
    out = await run_pipeline(
        ctx,
        tm=tm,
        client=client,
        emit=emit,  # type: ignore[arg-type]
        base_data_dir=tmp_path,
        poll=_FAST_POLL,
    )
    assert out.error_kind is None
    methods = [c[0] for c in client.calls]
    assert "synth_voice" not in methods
    assert "face_detect" not in methods
    am = next(c for c in client.calls if c[0] == "submit_animate_mix")
    assert am[1]["mode_pro"] is False
    assert am[1]["watermark"] is False


# ─── avatar_compose (mode 4) ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_avatar_compose_chains_i2i_detect_s2v(
    tmp_path: Path,
    tm: AvatarTaskManager,
    emit_recorder: tuple[Callable[..., Any], list[tuple[str, dict[str, Any]]]],
) -> None:
    emit, _ = emit_recorder
    client = FakeClient()
    ctx = await _make_ctx(
        tm,
        "avatar_compose",
        {
            "assets": {
                "ref_images_url": [
                    "https://oss.example.com/x/portrait.png",
                    "https://oss.example.com/x/scene.png",
                ],
            },
            "text": "hi there",
            "voice_id": "longxiaochun",
            "resolution": "480P",
            "compose_prompt": "merge them",
        },
    )

    async def get_dur(_: Path) -> float:
        return 3.0

    out = await run_pipeline(
        ctx,
        tm=tm,
        client=client,
        emit=emit,  # type: ignore[arg-type]
        base_data_dir=tmp_path,
        get_audio_duration=get_dur,
        poll=_FAST_POLL,
    )
    assert out.error_kind is None
    assert out.composed_image_url == client.image_edit_url
    methods = [c[0] for c in client.calls]
    # The chain order: image_edit → query_task(i2i) → face_detect(composed)
    # → submit_s2v → query_task(ds-1)
    assert methods.index("submit_image_edit") < methods.index("face_detect")
    assert methods.index("face_detect") < methods.index("submit_s2v")
    # face_detect called against the COMPOSED image
    detect_call = next(c for c in client.calls if c[0] == "face_detect")
    assert detect_call[1]["image_url"] == client.image_edit_url


# ─── Cost approval gate ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cost_gate_pauses_until_approved(
    tmp_path: Path,
    tm: AvatarTaskManager,
    emit_recorder: tuple[Callable[..., Any], list[tuple[str, dict[str, Any]]]],
) -> None:
    """Over-threshold cost without approval → pipeline pauses, status=pending."""
    emit, _ = emit_recorder
    client = FakeClient()
    ctx = await _make_ctx(
        tm,
        "video_reface",
        {
            "assets": {
                "image_url": "https://oss.example.com/x.png",
                "video_url": "https://oss.example.com/x.mp4",
            },
            # 60s × 1.20元/s = 72元 — well over default 5元 threshold
            "video_duration_sec": 60,
            "mode_pro": True,
        },
    )
    out = await run_pipeline(
        ctx,
        tm=tm,
        client=client,
        emit=emit,  # type: ignore[arg-type]
        base_data_dir=tmp_path,
        poll=_FAST_POLL,
    )
    assert out.error_kind == "approval_required"
    assert out.cost_breakdown is not None
    assert out.cost_breakdown["exceeds_threshold"] is True
    row = await tm.get_task(ctx.task_id)
    assert row is not None
    # Status stays 'pending' so the user can re-submit with approval.
    assert row["status"] == "pending"
    # Submit was never called.
    assert not any(c[0].startswith("submit_") for c in client.calls)


@pytest.mark.asyncio
async def test_cost_gate_passes_when_approved(
    tmp_path: Path,
    tm: AvatarTaskManager,
    emit_recorder: tuple[Callable[..., Any], list[tuple[str, dict[str, Any]]]],
) -> None:
    emit, _ = emit_recorder
    client = FakeClient()
    ctx = await _make_ctx(
        tm,
        "video_reface",
        {
            "assets": {
                "image_url": "https://oss.example.com/x.png",
                "video_url": "https://oss.example.com/x.mp4",
            },
            "video_duration_sec": 60,
            "mode_pro": True,
        },
    )
    ctx.cost_approved = True
    out = await run_pipeline(
        ctx,
        tm=tm,
        client=client,
        emit=emit,  # type: ignore[arg-type]
        base_data_dir=tmp_path,
        poll=_FAST_POLL,
    )
    assert out.error_kind is None
    assert any(c[0] == "submit_animate_mix" for c in client.calls)


# ─── Cancellation ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancellation_mid_poll_marks_status_cancelled(
    tmp_path: Path,
    tm: AvatarTaskManager,
    emit_recorder: tuple[Callable[..., Any], list[tuple[str, dict[str, Any]]]],
) -> None:
    emit, _ = emit_recorder
    client = FakeClient()
    # Make the first poll come back PENDING so we get a chance to cancel.
    client.poll_responses = [
        {"task_id": "ds-1", "status": "PENDING", "is_done": False, "is_ok": False},
        {"task_id": "ds-1", "status": "PENDING", "is_done": False, "is_ok": False},
    ]
    ctx = await _make_ctx(
        tm,
        "video_reface",
        {
            "assets": {
                "image_url": "https://oss.example.com/x.png",
                "video_url": "https://oss.example.com/x.mp4",
            },
            "video_duration_sec": 3,
        },
    )

    async def cancel_after_first_poll() -> None:
        await asyncio.sleep(0.0)  # let pipeline start
        for _ in range(50):
            if any(c[0] == "query_task" for c in client.calls):
                break
            await asyncio.sleep(0.005)
        client.cancelled.add("ds-1")

    cancel_task = asyncio.create_task(cancel_after_first_poll())
    out = await run_pipeline(
        ctx,
        tm=tm,
        client=client,
        emit=emit,  # type: ignore[arg-type]
        base_data_dir=tmp_path,
        poll=_FAST_POLL,
    )
    await cancel_task
    assert out.error_kind == "cancelled"
    row = await tm.get_task(ctx.task_id)
    assert row is not None
    assert row["status"] == "cancelled"


# ─── Polling timeout ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_polling_total_timeout_classifies_timeout(
    tmp_path: Path,
    tm: AvatarTaskManager,
    emit_recorder: tuple[Callable[..., Any], list[tuple[str, dict[str, Any]]]],
) -> None:
    emit, _ = emit_recorder
    client = FakeClient()
    # Always pending → forces the total_timeout branch.
    client.poll_responses = [
        {"task_id": "ds-1", "status": "PENDING", "is_done": False, "is_ok": False}
        for _ in range(2000)
    ]
    ctx = await _make_ctx(
        tm,
        "video_reface",
        {
            "assets": {
                "image_url": "https://oss.example.com/x.png",
                "video_url": "https://oss.example.com/x.mp4",
            },
            "video_duration_sec": 3,
        },
    )
    out = await run_pipeline(
        ctx,
        tm=tm,
        client=client,
        emit=emit,  # type: ignore[arg-type]
        base_data_dir=tmp_path,
        poll=PollSchedule(
            fast_interval_sec=0.001,
            fast_until_sec=0.005,
            medium_interval_sec=0.001,
            medium_until_sec=0.01,
            slow_interval_sec=0.001,
            total_timeout_sec=0.05,
        ),
    )
    assert out.error_kind == "timeout"


# ─── Cost breakdown is recorded as JSON in the task row ─────────────────


@pytest.mark.asyncio
async def test_cost_breakdown_persisted_as_json(
    tmp_path: Path,
    tm: AvatarTaskManager,
    emit_recorder: tuple[Callable[..., Any], list[tuple[str, dict[str, Any]]]],
) -> None:
    emit, _ = emit_recorder
    client = FakeClient()
    ctx = await _make_ctx(
        tm,
        "photo_speak",
        {
            "assets": {"image_url": "https://oss.example.com/x.png"},
            "text": "hi",
            "voice_id": "longxiaochun",
        },
    )
    await run_pipeline(
        ctx,
        tm=tm,
        client=client,
        emit=emit,  # type: ignore[arg-type]
        base_data_dir=tmp_path,
        poll=_FAST_POLL,
    )
    row = await tm.get_task(ctx.task_id)
    assert row is not None
    assert row["status"] == "succeeded"
    cb = row.get("cost_breakdown") or json.loads(row["cost_breakdown_json"])
    assert "items" in cb
    assert cb["formatted_total"].startswith("¥")


# ─── metadata.json is written ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_metadata_json_written_to_task_dir(
    tmp_path: Path,
    tm: AvatarTaskManager,
    emit_recorder: tuple[Callable[..., Any], list[tuple[str, dict[str, Any]]]],
) -> None:
    emit, _ = emit_recorder
    client = FakeClient()
    ctx = await _make_ctx(
        tm,
        "video_relip",
        {
            "assets": {"video_url": "https://oss.example.com/x.mp4"},
            "text": "hi",
            "voice_id": "longxiaochun",
        },
    )
    await run_pipeline(
        ctx,
        tm=tm,
        client=client,
        emit=emit,  # type: ignore[arg-type]
        base_data_dir=tmp_path,
        poll=_FAST_POLL,
    )
    meta_path = ctx.task_dir / "metadata.json"
    assert meta_path.is_file()
    md = json.loads(meta_path.read_text(encoding="utf-8"))
    assert md["task_id"] == ctx.task_id
    assert md["mode"] == "video_relip"
    assert md["output_url"] == client.video_url


# ─── Helpers (utility coverage) ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_emit_swallows_internal_failures() -> None:
    def boom(_e: str, _p: dict[str, Any]) -> None:
        raise RuntimeError("subscriber crashed")

    # Must NOT raise.
    await _emit(boom, "task_update", {"task_id": "x"})


def test_ctx_payload_clamps_progress_to_0_100() -> None:
    ctx = AvatarPipelineContext(task_id="t", mode="photo_speak", params={})
    p = _ctx_payload(ctx, progress=200)
    assert p["progress"] == 100
    p2 = _ctx_payload(ctx, progress=-7)
    assert p2["progress"] == 0


def test_approval_required_carries_breakdown() -> None:
    ar = ApprovalRequired({"total": 99.0, "items": []})
    assert ar.cost_breakdown["total"] == 99.0


# ─── Failure mode: synth_voice raises VendorError ───────────────────────


@pytest.mark.asyncio
async def test_synth_voice_failure_propagates_kind(
    tmp_path: Path,
    tm: AvatarTaskManager,
    emit_recorder: tuple[Callable[..., Any], list[tuple[str, dict[str, Any]]]],
) -> None:
    emit, _ = emit_recorder
    client = FakeClient()
    client.raise_on["synth_voice"] = VendorError(
        "auth missing",
        status=401,
        retryable=False,
        kind="auth",
    )
    ctx = await _make_ctx(
        tm,
        "photo_speak",
        {
            "assets": {"image_url": "https://oss.example.com/x.png"},
            "text": "hi",
            "voice_id": "longxiaochun",
        },
    )
    out = await run_pipeline(
        ctx,
        tm=tm,
        client=client,
        emit=emit,  # type: ignore[arg-type]
        base_data_dir=tmp_path,
        poll=_FAST_POLL,
    )
    assert out.error_kind == "auth"
    row = await tm.get_task(ctx.task_id)
    assert row is not None
    assert row["status"] == "failed"
    assert row["error_kind"] == "auth"
