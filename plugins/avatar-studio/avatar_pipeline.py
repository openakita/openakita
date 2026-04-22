r"""avatar-studio generation pipeline — 8-step linear orchestration.

Inspired by Pixelle-Video's ``LinearVideoPipeline`` (A1) but written from
scratch to fit the four DashScope flows. We intentionally do NOT use any
SDK pipeline helper because SDK 0.7.0 retracted ``contrib`` entirely.

Steps
-----

1. ``setup_environment``   build the per-task directory tree
2. ``estimate_cost``       items + total → returns ``ApprovalRequired`` if the
                           threshold is exceeded and ``cost_approved=False``
3. ``prepare_assets``      stage uploads + face-detect (when relevant)
4. ``tts_synth``           cosyvoice-v2 → audio.mp3 + duration (Pixelle P1
                           — duration becomes the s2v duration)
5. ``image_compose``       avatar_compose only — wan2.5-i2i-preview
6. ``video_synth``         dispatch by mode → wan2.2-s2v / videoretalk /
                           wan2.2-animate-mix; polls task with 3-tier backoff
7. ``finalize``            download output, write metadata.json, mark task
                           ``succeeded``
8. ``handle_exception``    classify & persist any error, ``emit`` failure,
                           never let an exception escape

Mode short-circuit table
------------------------

============  =====  =====  =====  =====  =====  =====  =====
mode          1 env  2 cost 3 prep 4 tts  5 i2i  6 vid  7 fin
============  =====  =====  =====  =====  =====  =====  =====
photo_speak    ✓      ✓      ✓      ✓*     ✗      ✓      ✓
video_relip    ✓      ✓      ✓      ✓*     ✗      ✓      ✓
video_reface   ✓      ✓      ✓      ✓*     ✗      ✓      ✓
avatar_compose ✓      ✓      ✓      ✓*     ✓      ✓      ✓
============  =====  =====  =====  =====  =====  =====  =====

\* Step 4 is skipped when the user uploaded their own audio (no text in
   ``ctx.params``); ``ctx.tts_audio_duration_sec`` is then sourced from
   ``ctx.params['audio_duration_sec']`` (provided by the upload handler).

Cancellation
------------

``client.is_cancelled(ctx.dashscope_id)`` is checked on every polling
tick; on hit we ``client.cancel_task`` (best-effort), set
``ctx.error_kind = 'cancelled'`` and break out — but the rest of
``finalize`` / ``handle_exception`` still runs to record the cancellation
in the DB and emit ``task_update``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from avatar_dashscope_client import (
    MODEL_ANIMATE_MIX,
    MODEL_S2V,
    MODEL_VIDEORETALK,
    AvatarDashScopeClient,
)
from avatar_models import (
    MODES_BY_ID,
    estimate_cost,
    hint_for,
)
from avatar_studio_inline.upload_preview import build_preview_url
from avatar_studio_inline.vendor_client import VendorError
from avatar_task_manager import AvatarTaskManager

logger = logging.getLogger(__name__)


# ─── Polling strategy (Pixelle-validated 3-tier backoff) ──────────────


@dataclass(frozen=True)
class PollSchedule:
    """3-tier polling backoff with a hard total-timeout ceiling."""

    fast_interval_sec: float = 3.0
    fast_until_sec: float = 30.0
    medium_interval_sec: float = 10.0
    medium_until_sec: float = 120.0
    slow_interval_sec: float = 30.0
    total_timeout_sec: float = 600.0

    def interval_for(self, elapsed_sec: float) -> float:
        if elapsed_sec < self.fast_until_sec:
            return self.fast_interval_sec
        if elapsed_sec < self.medium_until_sec:
            return self.medium_interval_sec
        return self.slow_interval_sec


DEFAULT_POLL = PollSchedule()


# ─── Context ──────────────────────────────────────────────────────────


# Sentinel raised (not returned) by the cost gate when the user has not
# yet approved an over-threshold cost. Caught at the top level of
# ``run_pipeline`` and surfaced as ``error_kind = 'approval_required'``
# (a non-terminal state — the UI re-submits with ``cost_approved=true``).
class ApprovalRequired(Exception):
    """Cost exceeds threshold and the caller did not pre-approve it."""

    def __init__(self, cost_breakdown: dict[str, Any]) -> None:
        super().__init__("cost approval required")
        self.cost_breakdown = cost_breakdown


@dataclass
class AvatarPipelineContext:
    """All mutable state for one job, passed by reference through 8 steps."""

    task_id: str
    mode: str
    params: dict[str, Any]

    # Filled by step 1
    task_dir: Path = field(default_factory=Path)
    asset_paths: dict[str, Path] = field(default_factory=dict)
    asset_urls: dict[str, str] = field(default_factory=dict)

    # Filled by step 2
    cost_breakdown: dict[str, Any] | None = None
    cost_approved: bool = False

    # Filled by step 4
    tts_audio_path: Path | None = None
    tts_audio_duration_sec: float | None = None

    # Filled by step 5 (avatar_compose only)
    composed_image_path: Path | None = None
    composed_image_url: str | None = None

    # Filled by step 6
    dashscope_id: str | None = None
    dashscope_endpoint: str | None = None

    # Filled by step 7
    output_path: Path | None = None
    output_url: str | None = None
    video_duration_sec: float | None = None

    # Filled by step 8 (or anywhere on raise)
    error_kind: str | None = None
    error_message: str | None = None
    error_hints: dict[str, Any] | None = None

    started_at: float = field(default_factory=time.time)


# ─── Public types ─────────────────────────────────────────────────────


# UI-event emitter; signature matches ``api.broadcast_ui_event`` (Pixelle
# C3). Plugins pass ``lambda evt, payload: api.broadcast_ui_event(evt,
# payload)``. We accept both sync and async to keep tests easy.
EmitFn = Callable[[str, dict[str, Any]], Any]

# Optional duration extractor — the plugin layer wires this to a small
# mp3-frame counter (Pixelle A7). When None we fall back to whatever the
# DashScope response reports later in step 6.
GetAudioDurationFn = Callable[[Path], Awaitable[float] | float | None]


# ─── Public entry point ───────────────────────────────────────────────


async def run_pipeline(
    ctx: AvatarPipelineContext,
    *,
    tm: AvatarTaskManager,
    client: AvatarDashScopeClient,
    emit: EmitFn,
    plugin_id: str = "avatar-studio",
    base_data_dir: Path,
    get_audio_duration: GetAudioDurationFn | None = None,
    poll: PollSchedule = DEFAULT_POLL,
) -> AvatarPipelineContext:
    """Run all 8 steps. Never raises — any failure is captured into ``ctx``.

    Args:
        ctx: A fresh ``AvatarPipelineContext`` (only ``task_id`` / ``mode``
            / ``params`` need to be pre-filled).
        tm: Task manager used to persist progress.
        client: DashScope client (already configured with read_settings).
        emit: UI-event emitter; called as ``emit("task_update", payload)``
            on every meaningful state change.
        plugin_id: Used by ``build_preview_url`` to compose UI-facing URLs.
        base_data_dir: Plugin's data dir (typically
            ``api.get_data_dir() / "avatar-studio"``).
        get_audio_duration: Optional helper to compute the precise mp3
            duration after TTS — drives the s2v duration parameter (P1).
        poll: Backoff schedule for DashScope async polling.

    Returns:
        The same ``ctx``, mutated to its terminal state.
    """
    try:
        await _step_setup_environment(ctx, base_data_dir, tm, emit)
        await _step_estimate_cost(ctx, tm, emit)
        await _step_prepare_assets(ctx, plugin_id, client, tm, emit)
        await _step_tts_synth(ctx, plugin_id, client, tm, emit, get_audio_duration)
        await _step_image_compose(ctx, plugin_id, client, tm, emit, poll)
        await _step_video_synth(ctx, client, tm, emit, poll)
        await _step_finalize(ctx, plugin_id, tm, emit)
    except ApprovalRequired as ar:
        # Non-terminal: surface as a soft pause; the UI re-submits with
        # ``cost_approved=true`` after the user clicks confirm.
        ctx.error_kind = "approval_required"
        ctx.error_message = "Cost exceeds threshold; user confirmation required"
        ctx.cost_breakdown = ar.cost_breakdown
        await tm.update_task_safe(
            ctx.task_id,
            status="pending",
            cost_breakdown_json=ar.cost_breakdown,
            error_kind="approval_required",
            error_message=ctx.error_message,
        )
        await _emit(emit, "task_update", _ctx_payload(ctx))
    except BaseException as e:  # noqa: BLE001 - root catcher
        await _step_handle_exception(ctx, e, tm, emit)
    return ctx


# ─── Step 1 · setup_environment ───────────────────────────────────────


async def _step_setup_environment(
    ctx: AvatarPipelineContext,
    base_data_dir: Path,
    tm: AvatarTaskManager,
    emit: EmitFn,
) -> None:
    if ctx.mode not in MODES_BY_ID:
        raise ValueError(f"unknown mode {ctx.mode!r}")
    ctx.task_dir = Path(base_data_dir) / "tasks" / ctx.task_id
    ctx.task_dir.mkdir(parents=True, exist_ok=True)
    await tm.update_task_safe(ctx.task_id, status="running")
    await _emit(emit, "task_update", _ctx_payload(ctx, progress=2))


# ─── Step 2 · estimate_cost (with approval gate) ──────────────────────


async def _step_estimate_cost(
    ctx: AvatarPipelineContext,
    tm: AvatarTaskManager,
    emit: EmitFn,
) -> None:
    audio_dur = ctx.tts_audio_duration_sec or _safe_float(ctx.params.get("audio_duration_sec"))
    text_chars = _safe_int(ctx.params.get("text_chars")) or _len_text(ctx.params.get("text"))
    preview = estimate_cost(
        ctx.mode,
        ctx.params,
        audio_duration_sec=audio_dur,
        text_chars=text_chars,
    )
    ctx.cost_breakdown = dict(preview)
    if preview["exceeds_threshold"] and not ctx.cost_approved:
        # Cost gate — not a true exception, just a flow pause.
        raise ApprovalRequired(ctx.cost_breakdown)
    await tm.update_task_safe(ctx.task_id, cost_breakdown_json=ctx.cost_breakdown)
    await _emit(emit, "task_update", _ctx_payload(ctx, progress=8))


# ─── Step 3 · prepare_assets ──────────────────────────────────────────


async def _step_prepare_assets(
    ctx: AvatarPipelineContext,
    plugin_id: str,
    client: AvatarDashScopeClient,
    tm: AvatarTaskManager,
    emit: EmitFn,
) -> None:
    """Materialise every required asset into ``asset_urls``.

    Inputs are expected to already live under the plugin data dir (the
    upload route persisted them and stored the relative path in
    ``ctx.params['assets'][kind]``). This step:

    1. Resolves each kind → absolute path → preview URL.
    2. For modes that need it, runs ``client.face_detect`` on the portrait
       so we fail fast (saves the s2v unit charge — Pixelle "fail-fast on
       expensive remote calls").
    """
    raw_assets = ctx.params.get("assets") or {}
    if not isinstance(raw_assets, dict):
        raise VendorError(
            "params.assets must be a dict {kind: relative_path}",
            status=422,
            retryable=False,
            kind="client",
        )

    for kind, rel in raw_assets.items():
        if not rel:
            continue
        rel_str = str(rel).replace("\\", "/").lstrip("/")
        ctx.asset_paths[kind] = Path(rel_str)
        ctx.asset_urls[kind] = build_preview_url(plugin_id, rel_str)

    # Modes that ultimately drive s2v need a humanoid pre-check on the
    # portrait. ``avatar_compose`` runs the check AFTER step 5 because the
    # composed image is what feeds s2v, not the raw inputs.
    if ctx.mode == "photo_speak":
        if "image" not in ctx.asset_urls:
            raise VendorError(
                "photo_speak requires an 'image' asset",
                status=422,
                retryable=False,
                kind="client",
            )
        await client.face_detect(ctx.asset_urls["image"])

    await tm.update_task_safe(
        ctx.task_id,
        asset_paths_json={k: str(v) for k, v in ctx.asset_paths.items()},
    )
    await _emit(emit, "task_update", _ctx_payload(ctx, progress=15))


# ─── Step 4 · tts_synth ───────────────────────────────────────────────


async def _step_tts_synth(
    ctx: AvatarPipelineContext,
    plugin_id: str,
    client: AvatarDashScopeClient,
    tm: AvatarTaskManager,
    emit: EmitFn,
    get_audio_duration: GetAudioDurationFn | None,
) -> None:
    text = (ctx.params.get("text") or "").strip()
    voice_id = ctx.params.get("voice_id") or ""

    # Mode 3 (video_reface) doesn't always need TTS; modes 1/2/4 typically
    # do but the user MAY have uploaded an audio asset instead.
    if "audio" in ctx.asset_urls:
        # Real audio uploaded — skip TTS entirely; the upload handler is
        # expected to have populated params['audio_duration_sec'].
        ctx.tts_audio_duration_sec = _safe_float(ctx.params.get("audio_duration_sec"))
        await _emit(emit, "task_update", _ctx_payload(ctx, progress=25))
        return

    if not text:
        # Mode 3 with no text and no audio is fine (pure video reface);
        # other modes will fail loudly at step 6.
        await _emit(emit, "task_update", _ctx_payload(ctx, progress=25))
        return

    if not voice_id:
        raise VendorError(
            "TTS requires a voice_id (params.voice_id)",
            status=422,
            retryable=False,
            kind="client",
        )

    res = await client.synth_voice(text=text, voice_id=str(voice_id))
    audio_path = ctx.task_dir / "audio.mp3"
    audio_path.write_bytes(res["audio_bytes"])
    ctx.tts_audio_path = audio_path
    rel = _rel_to_data_dir(audio_path, plugin_id)
    ctx.asset_urls["audio"] = build_preview_url(plugin_id, rel)

    if get_audio_duration is not None:
        dur = get_audio_duration(audio_path)
        if asyncio.iscoroutine(dur):
            dur = await dur
        if dur:
            ctx.tts_audio_duration_sec = float(dur)

    await tm.update_task_safe(
        ctx.task_id,
        audio_duration_sec=ctx.tts_audio_duration_sec,
    )
    await _emit(emit, "task_update", _ctx_payload(ctx, progress=30))


# ─── Step 5 · image_compose (avatar_compose only) ─────────────────────


async def _step_image_compose(
    ctx: AvatarPipelineContext,
    plugin_id: str,
    client: AvatarDashScopeClient,
    tm: AvatarTaskManager,
    emit: EmitFn,
    poll: PollSchedule,
) -> None:
    if ctx.mode != "avatar_compose":
        return

    refs = [u for k, u in ctx.asset_urls.items() if k.startswith("image")]
    prompt = (ctx.params.get("compose_prompt") or "").strip()
    if not prompt:
        # Fallback prompt — keeps the call legal even if the user did not
        # toggle the qwen-vl assist (the LLM-generated prompt is purely
        # optional per the user requirement).
        prompt = "把人物自然地融合到场景中，保留人物的面部特征"
    if not refs:
        raise VendorError(
            "avatar_compose requires at least one image asset",
            status=422,
            retryable=False,
            kind="client",
        )

    # Submit and poll (i2i is a separate async job from s2v).
    i2i_task_id = await client.submit_image_edit(
        prompt=prompt,
        ref_images_url=refs[:3],
        size=str(ctx.params.get("compose_size") or "") or None,
    )
    res = await _poll_until_done(client, i2i_task_id, poll, ctx, emit, progress_floor=35)
    if not res.get("is_ok"):
        raise VendorError(
            f"image compose failed: {res.get('error_message') or 'unknown'}",
            retryable=False,
            kind=res.get("error_kind") or "server",
        )
    composed_url = res.get("output_url")
    if not composed_url:
        raise VendorError(
            "image compose produced no output_url",
            retryable=False,
            kind="server",
        )
    # Persist the composed URL only — we don't proxy-download the bytes
    # because s2v can fetch the DashScope CDN URL directly.
    ctx.composed_image_url = composed_url
    ctx.asset_urls["composed_image"] = composed_url

    # Now face-detect the composed image (Pixelle "fail-fast on expensive
    # remote calls" — s2v charges per second, detect is per-image).
    await client.face_detect(composed_url)
    await tm.update_task_safe(
        ctx.task_id,
        asset_paths_json={
            **{k: str(v) for k, v in ctx.asset_paths.items()},
            "composed_image": composed_url,
        },
    )
    await _emit(emit, "task_update", _ctx_payload(ctx, progress=55))


# ─── Step 6 · video_synth (mode dispatch) ─────────────────────────────


async def _step_video_synth(
    ctx: AvatarPipelineContext,
    client: AvatarDashScopeClient,
    tm: AvatarTaskManager,
    emit: EmitFn,
    poll: PollSchedule,
) -> None:
    if ctx.mode == "photo_speak":
        ctx.dashscope_endpoint = MODEL_S2V
        ctx.dashscope_id = await client.submit_s2v(
            image_url=ctx.asset_urls["image"],
            audio_url=ctx.asset_urls["audio"],
            resolution=str(ctx.params.get("resolution") or "480P"),
            duration=ctx.tts_audio_duration_sec,
        )
    elif ctx.mode == "video_relip":
        ctx.dashscope_endpoint = MODEL_VIDEORETALK
        ctx.dashscope_id = await client.submit_videoretalk(
            video_url=ctx.asset_urls["video"],
            audio_url=ctx.asset_urls["audio"],
        )
    elif ctx.mode == "video_reface":
        ctx.dashscope_endpoint = MODEL_ANIMATE_MIX
        ctx.dashscope_id = await client.submit_animate_mix(
            image_url=ctx.asset_urls["image"],
            video_url=ctx.asset_urls["video"],
            mode_pro=bool(ctx.params.get("mode_pro")),
            watermark=bool(ctx.params.get("watermark")),
        )
    elif ctx.mode == "avatar_compose":
        ctx.dashscope_endpoint = MODEL_S2V
        composed = ctx.composed_image_url or ctx.asset_urls.get("composed_image")
        if not composed:
            raise VendorError(
                "avatar_compose video_synth missing composed_image",
                retryable=False,
                kind="server",
            )
        ctx.dashscope_id = await client.submit_s2v(
            image_url=composed,
            audio_url=ctx.asset_urls["audio"],
            resolution=str(ctx.params.get("resolution") or "480P"),
            duration=ctx.tts_audio_duration_sec,
        )
    else:
        raise ValueError(f"unknown mode {ctx.mode!r}")

    await tm.update_task_safe(
        ctx.task_id,
        dashscope_id=ctx.dashscope_id,
        dashscope_endpoint=ctx.dashscope_endpoint,
    )
    await _emit(emit, "task_update", _ctx_payload(ctx, progress=60))

    res = await _poll_until_done(client, ctx.dashscope_id, poll, ctx, emit, progress_floor=60)
    if not res.get("is_ok"):
        raise VendorError(
            f"video synth failed: {res.get('error_message') or 'unknown'}",
            retryable=False,
            kind=res.get("error_kind") or "server",
        )
    ctx.output_url = res.get("output_url")
    usage = res.get("usage") or {}
    if isinstance(usage, dict):
        for key in ("video_duration", "duration", "video_length"):
            if usage.get(key):
                ctx.video_duration_sec = float(usage[key])
                break


# ─── Step 7 · finalize ────────────────────────────────────────────────


async def _step_finalize(
    ctx: AvatarPipelineContext,
    plugin_id: str,
    tm: AvatarTaskManager,
    emit: EmitFn,
) -> None:
    # We DON'T proxy-download the bytes by default — the DashScope CDN URL
    # works fine in <video> tags. ``output_path`` is only set if the
    # plugin's settings opted into local archival (left to the plugin
    # layer). Here we just persist the URL.
    metadata = {
        "task_id": ctx.task_id,
        "mode": ctx.mode,
        "params": ctx.params,
        "asset_urls": ctx.asset_urls,
        "tts_audio_duration_sec": ctx.tts_audio_duration_sec,
        "video_duration_sec": ctx.video_duration_sec,
        "cost_breakdown": ctx.cost_breakdown,
        "dashscope_id": ctx.dashscope_id,
        "dashscope_endpoint": ctx.dashscope_endpoint,
        "output_url": ctx.output_url,
        "elapsed_sec": round(time.time() - ctx.started_at, 2),
    }
    (ctx.task_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    await tm.update_task_safe(
        ctx.task_id,
        status="succeeded",
        output_url=ctx.output_url,
        video_duration_sec=ctx.video_duration_sec,
        completed_at=time.time(),
    )
    await _emit(emit, "task_update", _ctx_payload(ctx, progress=100))


# ─── Step 8 · handle_exception ────────────────────────────────────────


async def _step_handle_exception(
    ctx: AvatarPipelineContext,
    exc: BaseException,
    tm: AvatarTaskManager,
    emit: EmitFn,
) -> None:
    if isinstance(exc, asyncio.CancelledError):
        ctx.error_kind = "cancelled"
        ctx.error_message = "task cancelled by user"
        status = "cancelled"
    elif isinstance(exc, VendorError):
        ctx.error_kind = exc.kind or "unknown"
        ctx.error_message = str(exc)
        status = "failed"
    elif isinstance(exc, ValueError):
        ctx.error_kind = "client"
        ctx.error_message = str(exc)
        status = "failed"
    else:
        ctx.error_kind = "unknown"
        ctx.error_message = f"{type(exc).__name__}: {exc}"
        status = "failed"

    ctx.error_hints = dict(hint_for(ctx.error_kind))

    try:
        await tm.update_task_safe(
            ctx.task_id,
            status=status,
            error_kind=ctx.error_kind,
            error_message=ctx.error_message,
            error_hints_json=ctx.error_hints,
            completed_at=time.time(),
        )
    except Exception:  # noqa: BLE001 - never let cleanup raise
        logger.exception("avatar_pipeline: failed to persist error for %s", ctx.task_id)

    await _emit(emit, "task_update", _ctx_payload(ctx, progress=100))


# ─── Polling helper ───────────────────────────────────────────────────


async def _poll_until_done(
    client: AvatarDashScopeClient,
    dashscope_id: str,
    poll: PollSchedule,
    ctx: AvatarPipelineContext,
    emit: EmitFn,
    *,
    progress_floor: int,
) -> dict[str, Any]:
    """Poll ``client.query_task`` with 3-tier backoff until done / timeout / cancel.

    Emits ``task_update`` with a synthetic 0-95% progress (DashScope does
    not expose real progress) so the UI bar moves forward.
    """
    start = time.time()
    last_emit = 0.0
    last_status = ""
    while True:
        if client.is_cancelled(dashscope_id) or client.is_cancelled(ctx.task_id):
            await client.cancel_task(dashscope_id)
            raise asyncio.CancelledError()

        elapsed = time.time() - start
        if elapsed > poll.total_timeout_sec:
            raise VendorError(
                f"DashScope task {dashscope_id} did not finish in {poll.total_timeout_sec:.0f}s",
                retryable=False,
                kind="timeout",
            )

        try:
            res = await client.query_task(dashscope_id)
        except VendorError:
            # transient query failure — log and retry next tick rather
            # than abort the whole pipeline.
            await asyncio.sleep(poll.interval_for(elapsed))
            continue

        status = str(res.get("status") or "")
        # Emit progress at most every 2 seconds, or on status change.
        if status != last_status or (time.time() - last_emit) > 2.0:
            last_status = status
            last_emit = time.time()
            # Synthetic progress: linear up to 95% across total_timeout.
            pct = min(95, progress_floor + int((elapsed / poll.total_timeout_sec) * 35))
            await _emit(
                emit,
                "task_update",
                _ctx_payload(ctx, progress=pct, dashscope_status=status),
            )

        if res.get("is_done"):
            return res
        await asyncio.sleep(poll.interval_for(elapsed))


# ─── Helpers ──────────────────────────────────────────────────────────


def _ctx_payload(
    ctx: AvatarPipelineContext,
    *,
    progress: int | None = None,
    dashscope_status: str | None = None,
) -> dict[str, Any]:
    """Snapshot of ``ctx`` suitable for SSE emission."""
    out: dict[str, Any] = {
        "task_id": ctx.task_id,
        "mode": ctx.mode,
        "asset_urls": dict(ctx.asset_urls),
        "tts_audio_duration_sec": ctx.tts_audio_duration_sec,
        "video_duration_sec": ctx.video_duration_sec,
        "dashscope_id": ctx.dashscope_id,
        "dashscope_endpoint": ctx.dashscope_endpoint,
        "output_url": ctx.output_url,
        "cost_breakdown": ctx.cost_breakdown,
        "error_kind": ctx.error_kind,
        "error_message": ctx.error_message,
        "error_hints": ctx.error_hints,
    }
    if progress is not None:
        out["progress"] = max(0, min(100, int(progress)))
    if dashscope_status is not None:
        out["dashscope_status"] = dashscope_status
    return out


async def _emit(emit: EmitFn, event: str, payload: dict[str, Any]) -> None:
    """Call ``emit`` whether it's sync or async, swallow internal failures."""
    try:
        result = emit(event, payload)
        if asyncio.iscoroutine(result):
            await result
    except Exception:  # noqa: BLE001 - emit is best-effort
        logger.exception("emit(%s) failed for task %s", event, payload.get("task_id"))


def _rel_to_data_dir(path: Path, plugin_id: str) -> str:  # noqa: ARG001
    """Return ``tasks/<task_id>/<file>`` relative to the plugin data dir.

    ``plugin_id`` is accepted for symmetry with ``build_preview_url`` even
    though it isn't needed here — keeps the call sites uniform.
    """
    parts = path.resolve().parts
    if "tasks" in parts:
        idx = parts.index("tasks")
        return "/".join(parts[idx:])
    return path.name


def _safe_float(v: Any) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _safe_int(v: Any) -> int | None:
    try:
        if v is None or v == "":
            return None
        return int(v)
    except (TypeError, ValueError):
        return None


def _len_text(v: Any) -> int | None:
    if not isinstance(v, str) or not v:
        return None
    return len(v)
