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
    plugin_id: str,  # noqa: ARG001 - kept for symmetry with other steps
    client: AvatarDashScopeClient,
    tm: AvatarTaskManager,
    emit: EmitFn,
) -> None:
    """Materialise every required asset into ``asset_urls``.

    The UI's ``buildPayload`` (ui/dist/index.html) populates
    ``params['assets']`` with **already-public URLs** (Aliyun OSS signed
    HTTPS) keyed by the same names DashScope expects:

      ``image_url``        — single image (photo_speak, video_reface,
                              first ref for avatar_compose)
      ``video_url``        — source video (video_relip, video_reface)
      ``audio_url``        — pre-recorded audio (any mode that skips TTS)
      ``ref_images_url``   — list[str], 1..3 (avatar_compose)

    We do NOT re-wrap these with ``build_preview_url`` — that would
    double-prefix them with ``/api/plugins/...`` and produce an
    unreachable garbage URL.  Instead we validate they look public
    (start with ``https://``) and surface a clear 422 if a local
    ``/api/...`` path leaked through (which means OSS upload at
    ``POST /upload`` time silently fell back to the local URL — the
    user needs to fix Settings → OSS).

    Per-mode validation lives here too so we fail BEFORE running the
    expensive ``face_detect`` / ``submit_*`` calls (Pixelle "fail-fast
    on expensive remote calls").
    """
    raw_assets = ctx.params.get("assets") or {}
    if not isinstance(raw_assets, dict):
        raise VendorError(
            "params.assets must be a dict {url_kind: public_url}",
            status=422,
            retryable=False,
            kind="client",
        )

    # Copy verbatim (lists too — ref_images_url is a list[str]) under the
    # same key names so step 6 can read ctx.asset_urls["image_url"] etc.
    for kind, val in raw_assets.items():
        if val is None or val == "":
            continue
        if isinstance(val, list):
            cleaned = [str(v).strip() for v in val if str(v or "").strip()]
            if cleaned:
                ctx.asset_urls[kind] = cleaned  # type: ignore[assignment]
        else:
            ctx.asset_urls[kind] = str(val).strip()

    # Reject any URL DashScope can't fetch — `/api/...` is local-only,
    # `data:` is currently unused but worth blocking until we actually
    # wire base64 fallback. The hint deliberately points at OSS Settings
    # because that's the *only* reason a value would still be a local
    # URL after POST /upload (it falls back when OSS is not configured).
    for kind, val in list(ctx.asset_urls.items()):
        urls = val if isinstance(val, list) else [val]
        for u in urls:
            u_low = (u or "").lower()
            if u_low.startswith(("/api/", "/")) and not u_low.startswith(("//", "/data:")):
                raise VendorError(
                    f"asset {kind!r} is a local URL ({u!r}); "
                    "DashScope cannot fetch it. Open Settings → OSS and "
                    "fill in endpoint/bucket/key/secret, then re-upload.",
                    status=422,
                    retryable=False,
                    kind="client",
                )

    # Per-mode required-input gate. Names match what UI buildPayload
    # actually emits; if you add a new mode, add its required keys here
    # too — silent drop-throughs is what got us into this audit in the
    # first place.
    required: dict[str, list[str]] = {
        "photo_speak": ["image_url"],
        "video_relip": ["video_url"],
        "video_reface": ["image_url", "video_url"],
        "avatar_compose": ["ref_images_url"],
    }
    for need in required.get(ctx.mode, []):
        if need not in ctx.asset_urls:
            raise VendorError(
                f"{ctx.mode} requires asset '{need}' (not provided by UI)",
                status=422,
                retryable=False,
                kind="client",
            )

    # Modes that ultimately drive s2v need a humanoid pre-check on the
    # portrait. ``avatar_compose`` runs the check AFTER step 5 because
    # the composed image is what feeds s2v, not the raw inputs.
    if ctx.mode == "photo_speak":
        await client.face_detect(str(ctx.asset_urls["image_url"]))

    await tm.update_task_safe(
        ctx.task_id,
        asset_paths_json={
            k: (v if isinstance(v, str) else ",".join(v))
            for k, v in ctx.asset_urls.items()
        },
    )
    await _emit(emit, "task_update", _ctx_payload(ctx, progress=15))


# ─── Step 4 · tts_synth ───────────────────────────────────────────────


async def _step_tts_synth(
    ctx: AvatarPipelineContext,
    plugin_id: str,  # noqa: ARG001 - kept for symmetry; OSS provides public URL
    client: AvatarDashScopeClient,
    tm: AvatarTaskManager,
    emit: EmitFn,
    get_audio_duration: GetAudioDurationFn | None,
) -> None:
    text = (ctx.params.get("text") or "").strip()
    voice_id = ctx.params.get("voice_id") or ""

    # Mode 3 (video_reface) doesn't always need TTS; modes 1/2/4 typically
    # do but the user MAY have uploaded an audio asset instead.
    if "audio_url" in ctx.asset_urls:
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

    # ── Synth ───────────────────────────────────────────────────────
    res = await client.synth_voice(text=text, voice_id=str(voice_id))
    actual_format = str(res.get("format") or "mp3").lower()
    audio_bytes = res["audio_bytes"]

    # ── Persist locally with the *actual* container's extension ────
    # Two prior bugs cohabitated the old ``audio.mp3`` line:
    #   (a) the file lived under ``tasks/{tid}/`` but the upload-preview
    #       route was scoped to ``uploads/`` only — so the URL we built
    #       returned 404, and DashScope (which would have fetched it)
    #       would have failed too.
    #   (b) the extension was always ``.mp3`` even when synth_voice
    #       returned WAV-wrapped PCM (its raw-PCM fallback) — so the
    #       browser's <audio> tag silently refused to decode.
    # Fix: write under ``uploads/audios/`` with the real extension; the
    # local URL is only used for the preview row in metadata.json — the
    # *actual* URL handed to DashScope is the OSS one we sign below.
    audios_dir = (ctx.task_dir.parent.parent / "uploads" / "audios")
    audios_dir.mkdir(parents=True, exist_ok=True)
    fname = f"tts_{ctx.task_id}.{actual_format}"
    audio_path = audios_dir / fname
    audio_path.write_bytes(audio_bytes)
    ctx.tts_audio_path = audio_path

    # ── Hand DashScope a public URL ─────────────────────────────────
    # Pipeline doesn't import OssUploader directly — that would cross
    # the layering. Instead the plugin layer pre-stuffed an uploader
    # into ``ctx.params['_oss_upload_audio']`` (a coroutine factory)
    # whenever OSS is configured. If it's missing we fail early with a
    # clear message rather than handing DashScope an unreachable URL.
    upload_fn = ctx.params.get("_oss_upload_audio")
    if not callable(upload_fn):
        raise VendorError(
            "TTS audio cannot be sent to DashScope without OSS configured. "
            "Open Settings → OSS and fill in the four fields.",
            status=400,
            retryable=False,
            kind="client",
        )
    audio_public_url = await upload_fn(audio_path, fname)
    ctx.asset_urls["audio_url"] = audio_public_url

    # ── Compute real duration so step 6 / cost gate are accurate ────
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

    # ``ref_images_url`` is the canonical name (matches the UI's
    # buildPayload + the wan2.5-i2i-preview body field). Fall back to a
    # single ``image_url`` so a manual API caller can pass one image
    # without having to wrap it in a list.
    ref_val = ctx.asset_urls.get("ref_images_url")
    if isinstance(ref_val, list):
        refs = [str(u) for u in ref_val if u]
    elif ref_val:
        refs = [str(ref_val)]
    else:
        single = str(ctx.asset_urls.get("image_url") or "").strip()
        refs = [single] if single else []

    prompt = (ctx.params.get("compose_prompt") or "").strip()
    if not prompt:
        # Fallback prompt — keeps the call legal even if the user did not
        # toggle the qwen-vl assist (the LLM-generated prompt is purely
        # optional per the user requirement).
        prompt = "把人物自然地融合到场景中，保留人物的面部特征"
    if not refs:
        raise VendorError(
            "avatar_compose requires at least one image asset (ref_images_url)",
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
    # because s2v can fetch the DashScope CDN URL directly. Stored under
    # the ``composed_image_url`` key (matches the dashscope field naming
    # used by step 6) so a single rename never desyncs.
    ctx.composed_image_url = composed_url
    ctx.asset_urls["composed_image_url"] = composed_url

    # Now face-detect the composed image (Pixelle "fail-fast on expensive
    # remote calls" — s2v charges per second, detect is per-image).
    await client.face_detect(composed_url)
    await tm.update_task_safe(
        ctx.task_id,
        asset_paths_json={
            **{
                k: (v if isinstance(v, str) else ",".join(v))
                for k, v in ctx.asset_urls.items()
            },
            "composed_image_url": composed_url,
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
    # All keys are the post-fix names from _step_prepare_assets — see
    # the docstring there for the UI contract. The cast to ``str`` is
    # defensive: ``ref_images_url`` is the only list-valued asset, and
    # the video pipeline never reads it directly (it's consumed in
    # step 5 by image_compose), so anything we read here MUST be a str.
    image_url = str(ctx.asset_urls.get("image_url") or "")
    video_url = str(ctx.asset_urls.get("video_url") or "")
    audio_url = str(ctx.asset_urls.get("audio_url") or "")

    if ctx.mode == "photo_speak":
        ctx.dashscope_endpoint = MODEL_S2V
        ctx.dashscope_id = await client.submit_s2v(
            image_url=image_url,
            audio_url=audio_url,
            resolution=str(ctx.params.get("resolution") or "480P"),
            duration=ctx.tts_audio_duration_sec,
        )
    elif ctx.mode == "video_relip":
        ctx.dashscope_endpoint = MODEL_VIDEORETALK
        ctx.dashscope_id = await client.submit_videoretalk(
            video_url=video_url,
            audio_url=audio_url,
        )
    elif ctx.mode == "video_reface":
        ctx.dashscope_endpoint = MODEL_ANIMATE_MIX
        ctx.dashscope_id = await client.submit_animate_mix(
            image_url=image_url,
            video_url=video_url,
            mode_pro=bool(ctx.params.get("mode_pro")),
            watermark=bool(ctx.params.get("watermark")),
        )
    elif ctx.mode == "avatar_compose":
        ctx.dashscope_endpoint = MODEL_S2V
        # Step 5 stored the composed portrait under both
        # ``ctx.composed_image_url`` AND ``asset_urls["composed_image_url"]``
        # for downstream symmetry with the other modes.
        composed = ctx.composed_image_url or str(
            ctx.asset_urls.get("composed_image_url") or ""
        )
        if not composed:
            raise VendorError(
                "avatar_compose video_synth missing composed_image_url",
                retryable=False,
                kind="server",
            )
        ctx.dashscope_id = await client.submit_s2v(
            image_url=composed,
            audio_url=audio_url,
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
    plugin_id: str,  # noqa: ARG001 - kept for symmetry with other steps
    tm: AvatarTaskManager,
    emit: EmitFn,
) -> None:
    # DashScope CDN URLs expire after ~24 hours. The earlier version of
    # this step only persisted ``output_url``, which made every task
    # 「broken video」 the next morning. Now we eagerly download the
    # bytes into ``ctx.task_dir`` and store BOTH the local path and the
    # CDN URL — the UI prefers the CDN URL while it's still warm and
    # falls back to the local copy after expiry.
    #
    # Filename comes from the URL when possible (so an mp4/png/jpg
    # extension survives), otherwise we infer from ``output_kind`` set
    # by ``_extract_output_url`` upstream.
    output_local: Path | None = None
    if ctx.output_url:
        try:
            output_local = await _download_output(ctx)
        except Exception as e:  # noqa: BLE001
            # Don't fail the task just because archival broke — surface
            # a warning in metadata so the UI can show a "未本地归档"
            # badge but keep the succeeded status.
            logger.warning(
                "avatar-studio: archive download failed for task %s: %s",
                ctx.task_id, e,
            )
    if output_local is not None:
        ctx.output_path = output_local

    # ``ctx.params`` may contain non-serialisable runtime hooks (e.g.
    # the ``_oss_upload_audio`` callable injected by the plugin layer).
    # Strip private/underscored keys before persisting metadata so a
    # function reference doesn't crash the json.dumps below.
    persistable_params = {
        k: v for k, v in ctx.params.items()
        if not (isinstance(k, str) and k.startswith("_"))
    }
    metadata = {
        "task_id": ctx.task_id,
        "mode": ctx.mode,
        "params": persistable_params,
        "asset_urls": ctx.asset_urls,
        "tts_audio_duration_sec": ctx.tts_audio_duration_sec,
        "video_duration_sec": ctx.video_duration_sec,
        "cost_breakdown": ctx.cost_breakdown,
        "dashscope_id": ctx.dashscope_id,
        "dashscope_endpoint": ctx.dashscope_endpoint,
        "output_url": ctx.output_url,
        "output_path": str(ctx.output_path) if ctx.output_path else None,
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
        output_path=str(ctx.output_path) if ctx.output_path else None,
        video_duration_sec=ctx.video_duration_sec,
        completed_at=time.time(),
    )
    await _emit(emit, "task_update", _ctx_payload(ctx, progress=100))


async def _download_output(ctx: AvatarPipelineContext) -> Path:
    """Pull the DashScope CDN URL into ``ctx.task_dir`` for offline replay.

    Uses ``httpx`` (already a project dep via vendor_client) with a 90-
    second timeout — generated videos are typically < 10 MB, so this is
    plenty.  Raises on non-200 so the caller logs and moves on without
    blocking task completion.
    """
    import httpx  # local import keeps the pipeline module light

    url = str(ctx.output_url or "")
    if not url:
        raise ValueError("no output_url to download")

    # Pick the extension from the URL (works for the standard
    # `.../output.mp4?Expires=...` shape DashScope uses) before falling
    # back to a generic .bin so the file at least exists somewhere.
    name = url.split("?", 1)[0].rsplit("/", 1)[-1] or "output.bin"
    if "." not in name:
        name = name + ".mp4"
    target = ctx.task_dir / name

    # Short connect timeout (5 s) keeps test runs and "DashScope CDN
    # is throttling us today" scenarios from blocking the entire
    # pipeline for the full 90 s read window — once the TCP handshake
    # succeeds we still allow the full 90 s for the download itself.
    timeout = httpx.Timeout(connect=5.0, read=90.0, write=10.0, pool=5.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as cli:
        resp = await cli.get(url)
        if resp.status_code != 200:
            raise VendorError(
                f"output download failed: {resp.status_code}",
                status=resp.status_code,
                retryable=False,
                kind="server",
            )
        target.write_bytes(resp.content)
    return target


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
