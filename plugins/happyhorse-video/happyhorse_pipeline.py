r"""happyhorse-video generation pipeline — 8-step linear orchestration.

Inspired by ``plugins/avatar-studio/avatar_pipeline.py`` (which itself
follows Pixelle-Video's ``LinearVideoPipeline``). Extended to cover the
full happyhorse-video catalog of 12 modes.

Steps
-----

1. ``setup_environment``   build the per-task directory tree
2. ``estimate_cost``       items + total → ``ApprovalRequired`` if the
                           threshold is exceeded and ``cost_approved=False``
3. ``prepare_assets``      stage uploads + face-detect + ``from_asset_ids``
                           expansion (workbench upstream consumption)
4. ``tts_synth``           cosyvoice-v2 / edge-tts → audio.mp3 + duration.
                           Skipped entirely when the resolved
                           :class:`ModelEntry` has ``native_audio_sync=True``
                           (HappyHorse 1.0 family) OR the user supplied
                           ``audio_url`` directly.
5. ``image_compose``       avatar_compose only — wan2.7-image (or
                           wan2.5-i2i-preview as fallback)
6. ``video_synth``         dispatch by registry to:
                             * happyhorse_dashscope_client.submit_video_synth
                               for {t2v, i2v, i2v_end, video_extend, r2v,
                               video_edit, long_video}
                             * submit_s2v / submit_videoretalk /
                               submit_animate_mix / submit_animate_move for
                               {photo_speak, video_relip, video_reface,
                               pose_drive, avatar_compose}
7. ``finalize``            download output, write metadata.json, mark task
                           ``succeeded`` AND populate the workbench protocol
                           fields (video_url / video_path / last_frame_url
                           / last_frame_path / asset_ids_json).
8. ``handle_exception``    classify & persist any error, ``emit`` failure,
                           never let an exception escape

Cancellation
------------

``client.is_cancelled(ctx.dashscope_id)`` is checked on every polling
tick; on hit we ``client.cancel_task`` (best-effort), set
``ctx.error_kind = 'cancelled'`` and break out — but the rest of
``finalize`` / ``handle_exception`` still runs to record the
cancellation in the DB and emit ``task_update``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from happyhorse_dashscope_client import (
    HappyhorseDashScopeClient,
)
from happyhorse_inline.vendor_client import VendorError
from happyhorse_model_registry import ModelEntry
from happyhorse_models import (
    MODES_BY_ID,
    _normalize_tts_engine,
    check_audio_duration,
    estimate_cost,
    hint_for,
)
from happyhorse_task_manager import HappyhorseTaskManager

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


# Mode groups (mirrors the registry's endpoint families).
_VIDEO_SYNTH_MODES: frozenset[str] = frozenset(
    {"t2v", "i2v", "i2v_end", "video_extend", "r2v", "video_edit", "long_video"}
)
_DIGITAL_HUMAN_MODES: frozenset[str] = frozenset(
    {"photo_speak", "video_relip", "video_reface", "pose_drive", "avatar_compose"}
)
_TTS_CAPABLE_MODES: frozenset[str] = frozenset(
    {"photo_speak", "video_relip", "video_reface", "avatar_compose", "long_video"}
)


class ApprovalRequired(Exception):
    """Cost exceeds threshold and the caller did not pre-approve it."""

    def __init__(self, cost_breakdown: dict[str, Any]) -> None:
        super().__init__("cost approval required")
        self.cost_breakdown = cost_breakdown


# ─── Context ──────────────────────────────────────────────────────────


@dataclass
class HappyhorsePipelineContext:
    """All mutable state for one job, passed by reference through 8 steps."""

    task_id: str
    mode: str
    params: dict[str, Any]
    model_id: str = ""

    # Filled by step 1.
    task_dir: Path = field(default_factory=Path)
    asset_paths: dict[str, Path] = field(default_factory=dict)
    asset_urls: dict[str, Any] = field(default_factory=dict)
    model_entry: ModelEntry | None = None

    # Filled by step 2.
    cost_breakdown: dict[str, Any] | None = None
    cost_approved: bool = False

    # Filled by step 4.
    tts_audio_path: Path | None = None
    tts_audio_duration_sec: float | None = None
    tts_engine_used: str | None = None  # "cosyvoice" | "edge" | None

    # Filled by step 5 (avatar_compose only).
    composed_image_path: Path | None = None
    composed_image_url: str | None = None

    # Filled by step 6.
    dashscope_id: str | None = None
    dashscope_endpoint: str | None = None

    # Filled by step 7 — workbench protocol fields.
    video_url: str | None = None
    video_path: Path | None = None
    last_frame_url: str | None = None
    last_frame_path: Path | None = None
    video_duration_sec: float | None = None
    asset_ids: list[str] = field(default_factory=list)

    # Filled by step 8 (or anywhere on raise).
    error_kind: str | None = None
    error_message: str | None = None
    error_hints: dict[str, Any] | None = None

    started_at: float = field(default_factory=time.time)


# ─── Public types ─────────────────────────────────────────────────────


EmitFn = Callable[[str, dict[str, Any]], Any]
GetAudioDurationFn = Callable[[Path], Awaitable[float] | float | None]


# ─── Public entry point ───────────────────────────────────────────────


async def run_pipeline(
    ctx: HappyhorsePipelineContext,
    *,
    tm: HappyhorseTaskManager,
    client: HappyhorseDashScopeClient,
    emit: EmitFn,
    plugin_id: str = "happyhorse-video",
    base_data_dir: Path,
    get_audio_duration: GetAudioDurationFn | None = None,
    poll: PollSchedule = DEFAULT_POLL,
    output_subdir_mode: str = "task",
    output_naming_rule: str = "{filename}",
) -> HappyhorsePipelineContext:
    """Run all 8 steps. Never raises — any failure is captured into ``ctx``."""
    try:
        await _step_setup_environment(ctx, base_data_dir, client, tm, emit)
        await _step_estimate_cost(ctx, tm, emit)
        await _step_prepare_assets(ctx, plugin_id, client, tm, emit)
        await _step_tts_synth(ctx, plugin_id, client, tm, emit, get_audio_duration)
        # Re-check the audio duration once we know the final length.
        post_tts_err = check_audio_duration(ctx.mode, ctx.tts_audio_duration_sec)
        if post_tts_err:
            raise VendorError(
                post_tts_err,
                status=422,
                retryable=False,
                kind="client",
            )
        await _step_image_compose(ctx, plugin_id, client, tm, emit, poll)
        await _step_video_synth(ctx, client, tm, emit, poll)
        await _step_finalize(
            ctx,
            plugin_id,
            tm,
            emit,
            base_data_dir=base_data_dir,
            output_subdir_mode=output_subdir_mode,
            output_naming_rule=output_naming_rule,
        )
    except ApprovalRequired as ar:
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
    except BaseException as e:  # noqa: BLE001 — root catcher
        await _step_handle_exception(ctx, e, tm, emit)
    return ctx


# ─── Step 1 · setup_environment ───────────────────────────────────────


async def _step_setup_environment(
    ctx: HappyhorsePipelineContext,
    base_data_dir: Path,
    client: HappyhorseDashScopeClient,
    tm: HappyhorseTaskManager,
    emit: EmitFn,
) -> None:
    if ctx.mode not in MODES_BY_ID:
        raise ValueError(f"unknown mode {ctx.mode!r}")
    ctx.task_dir = Path(base_data_dir) / "tasks" / ctx.task_id
    ctx.task_dir.mkdir(parents=True, exist_ok=True)
    # Resolve the registry entry once and stash it on ctx so all
    # downstream steps share the same ModelEntry instance.
    ctx.model_entry = client.resolve_model(ctx.mode, ctx.model_id or None)
    if not ctx.model_id:
        ctx.model_id = ctx.model_entry.model_id
    await tm.update_task_safe(
        ctx.task_id, status="running", model_id=ctx.model_id
    )
    await _emit(emit, "task_update", _ctx_payload(ctx, progress=2))


# ─── Step 2 · estimate_cost (with approval gate) ──────────────────────


async def _step_estimate_cost(
    ctx: HappyhorsePipelineContext,
    tm: HappyhorseTaskManager,
    emit: EmitFn,
) -> None:
    audio_dur = ctx.tts_audio_duration_sec or _safe_float(
        ctx.params.get("audio_duration_sec")
    )
    text_chars = _safe_int(ctx.params.get("text_chars")) or _len_text(
        ctx.params.get("text")
    )
    # Make sure ``model`` is set in params so estimate_cost reads it.
    p = dict(ctx.params)
    p.setdefault("model", ctx.model_id)
    preview = estimate_cost(
        ctx.mode,
        p,
        audio_duration_sec=audio_dur,
        text_chars=text_chars,
    )
    ctx.cost_breakdown = dict(preview)
    if preview["exceeds_threshold"] and not ctx.cost_approved:
        raise ApprovalRequired(ctx.cost_breakdown)
    await tm.update_task_safe(
        ctx.task_id, cost_breakdown_json=ctx.cost_breakdown
    )
    await _emit(emit, "task_update", _ctx_payload(ctx, progress=8))


# ─── Step 3 · prepare_assets ──────────────────────────────────────────


async def _step_prepare_assets(
    ctx: HappyhorsePipelineContext,
    plugin_id: str,  # noqa: ARG001 — kept for symmetry
    client: HappyhorseDashScopeClient,
    tm: HappyhorseTaskManager,
    emit: EmitFn,
) -> None:
    """Materialise every required asset into ``asset_urls`` and run mode
    pre-flight checks (face_detect for s2v family).
    """
    raw_assets = ctx.params.get("assets") or {}
    if not isinstance(raw_assets, dict):
        raise VendorError(
            "params.assets must be a dict {url_kind: public_url}",
            status=422,
            retryable=False,
            kind="client",
        )

    for kind, val in raw_assets.items():
        if val is None or val == "":
            continue
        if isinstance(val, list):
            cleaned = [str(v).strip() for v in val if str(v or "").strip()]
            if cleaned:
                ctx.asset_urls[kind] = cleaned
        else:
            ctx.asset_urls[kind] = str(val).strip()

    # UI/tool schemas send the common media fields at the top level, while
    # older helper code used params.assets. Normalize both shapes here so the
    # rest of the pipeline can use ctx.asset_urls consistently.
    for kind in (
        "first_frame_url",
        "last_frame_url",
        "source_video_url",
        "video_url",
        "image_url",
        "audio_url",
        "reference_urls",
        "image_urls",
        "ref_images_url",
    ):
        val = ctx.params.get(kind)
        if val is None or val == "":
            continue
        if isinstance(val, list):
            cleaned = [str(v).strip() for v in val if str(v or "").strip()]
            if cleaned and kind not in ctx.asset_urls:
                ctx.asset_urls[kind] = cleaned
        elif str(val).strip() and kind not in ctx.asset_urls:
            ctx.asset_urls[kind] = str(val).strip()

    # Compatibility aliases used by the DashScope digital-human methods.
    if "source_video_url" in ctx.asset_urls and "video_url" not in ctx.asset_urls:
        ctx.asset_urls["video_url"] = ctx.asset_urls["source_video_url"]
    if "image_url" in ctx.asset_urls and "ref_images_url" not in ctx.asset_urls:
        refs = [str(ctx.asset_urls["image_url"])]
        extra = ctx.asset_urls.get("image_urls")
        if isinstance(extra, list):
            refs.extend(str(u) for u in extra if u)
        elif extra:
            refs.append(str(extra))
        ctx.asset_urls["ref_images_url"] = refs

    # Surface any obvious local-URL leaks early — DashScope can't reach them.
    for kind, val in list(ctx.asset_urls.items()):
        urls = val if isinstance(val, list) else [val]
        for u in urls:
            u_low = (u or "").lower()
            if u_low.startswith(("/api/", "/")) and not u_low.startswith(
                ("//", "/data:")
            ):
                raise VendorError(
                    f"asset {kind!r} is a local URL ({u!r}); "
                    "DashScope cannot fetch it. Open Settings → OSS and "
                    "fill in endpoint/bucket/key/secret, then re-upload.",
                    status=422,
                    retryable=False,
                    kind="client",
                )

    # Per-mode required-input gate. Names match what UI buildPayload emits.
    required: dict[str, list[str]] = {
        "i2v": ["first_frame_url"],
        "i2v_end": ["first_frame_url", "last_frame_url"],
        "video_extend": ["source_video_url"],
        "video_edit": ["source_video_url"],
        "r2v": ["reference_urls"],
        "photo_speak": ["image_url"],
        "video_relip": ["source_video_url"],
        "video_reface": ["image_url", "source_video_url"],
        "pose_drive": ["image_url", "source_video_url"],
        "avatar_compose": ["ref_images_url"],
    }
    for need in required.get(ctx.mode, []):
        if need not in ctx.asset_urls:
            raise VendorError(
                f"{ctx.mode} requires asset '{need}' (not provided)",
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


# ─── Step 4 · tts_synth (skipped for HappyHorse 1.0 native sync) ──────


async def _step_tts_synth(
    ctx: HappyhorsePipelineContext,
    plugin_id: str,  # noqa: ARG001
    client: HappyhorseDashScopeClient,
    tm: HappyhorseTaskManager,
    emit: EmitFn,
    get_audio_duration: GetAudioDurationFn | None,
) -> None:
    # Skip TTS entirely for HappyHorse 1.0 (native audio-video sync) and
    # for any video-synth mode that isn't TTS-capable.
    if ctx.mode not in _TTS_CAPABLE_MODES:
        await _emit(emit, "task_update", _ctx_payload(ctx, progress=25))
        return
    if ctx.model_entry is not None and ctx.model_entry.native_audio_sync:
        # HappyHorse 1.0 generates audio internally — TTS step is a no-op.
        await _emit(emit, "task_update", _ctx_payload(ctx, progress=25))
        return

    text = (ctx.params.get("text") or "").strip()
    voice_id = ctx.params.get("voice_id") or ""

    # User uploaded their own audio — skip synth.
    if "audio_url" in ctx.asset_urls:
        ctx.tts_audio_duration_sec = _safe_float(
            ctx.params.get("audio_duration_sec")
        )
        await _emit(emit, "task_update", _ctx_payload(ctx, progress=25))
        return

    if not text:
        # Modes that require audio (photo_speak / video_relip /
        # avatar_compose) will fail loudly at step 6 with a clear error.
        await _emit(emit, "task_update", _ctx_payload(ctx, progress=25))
        return
    if not voice_id:
        raise VendorError(
            "TTS requires a voice_id (params.voice_id)",
            status=422,
            retryable=False,
            kind="client",
        )

    resolver = ctx.params.get("_resolve_voice_id")
    if callable(resolver):
        resolved_voice_id = resolver(str(voice_id))
        if asyncio.iscoroutine(resolved_voice_id):
            resolved_voice_id = await resolved_voice_id
        if resolved_voice_id:
            voice_id = str(resolved_voice_id)

    # Decide engine: edge if voice id starts with 'zh-' (Microsoft Edge
    # neural voices), cosyvoice otherwise. The user can also pin
    # ``params['tts_engine']`` to force a choice. The pin is normalised
    # through the same helper as estimate_cost so cost preview and
    # actual synthesis never disagree on which engine billed which job.
    raw_pin = str(ctx.params.get("tts_engine") or "").strip().lower()
    if raw_pin:
        engine = (
            "cosyvoice"
            if _normalize_tts_engine(raw_pin) == "cosyvoice-v2"
            else "edge"
        )
    elif str(voice_id).startswith(("zh-CN", "zh-HK", "zh-TW")):
        engine = "edge"
    else:
        engine = "cosyvoice"
    ctx.tts_engine_used = engine

    audios_dir = ctx.task_dir.parent.parent / "uploads" / "audios"
    audios_dir.mkdir(parents=True, exist_ok=True)

    if engine == "edge":
        from happyhorse_tts_edge import EdgeTtsDependencyError, synth_voice

        fname = f"tts_{ctx.task_id}.mp3"
        audio_path = audios_dir / fname
        try:
            res = await synth_voice(text=text, voice=str(voice_id), output_path=audio_path)
        except EdgeTtsDependencyError as e:
            raise VendorError(
                str(e),
                status=500,
                retryable=False,
                kind="dependency",
            ) from e
        ctx.tts_audio_path = audio_path
        ctx.tts_audio_duration_sec = _safe_float(res.get("duration_sec"))
    else:
        res = await client.synth_voice(text=text, voice_id=str(voice_id))
        actual_format = str(res.get("format") or "mp3").lower()
        audio_bytes = res["audio_bytes"]
        fname = f"tts_{ctx.task_id}.{actual_format}"
        audio_path = audios_dir / fname
        audio_path.write_bytes(audio_bytes)
        ctx.tts_audio_path = audio_path

    upload_fn = ctx.params.get("_oss_upload_audio")
    if not callable(upload_fn):
        raise VendorError(
            "TTS audio cannot be sent to DashScope without OSS configured. "
            "Open Settings → OSS and fill in the four fields.",
            status=400,
            retryable=False,
            kind="client",
        )
    audio_public_url = await upload_fn(audio_path, audio_path.name)
    ctx.asset_urls["audio_url"] = audio_public_url

    if get_audio_duration is not None and audio_path is not None:
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
    ctx: HappyhorsePipelineContext,
    plugin_id: str,  # noqa: ARG001
    client: HappyhorseDashScopeClient,
    tm: HappyhorseTaskManager,
    emit: EmitFn,
    poll: PollSchedule,
) -> None:
    if ctx.mode != "avatar_compose":
        return

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
        prompt = "把人物自然地融合到场景中，保留人物的面部特征"
    if not refs:
        raise VendorError(
            "avatar_compose requires at least one image asset (ref_images_url)",
            status=422,
            retryable=False,
            kind="client",
        )

    ensure_safe = ctx.params.get("_ensure_images_safe")
    if callable(ensure_safe):
        try:
            refs = await ensure_safe(refs[:9])
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "happyhorse-video: ensure_images_safe failed (%s); "
                "forwarding original URLs",
                e,
            )

    # Pick which image-edit model to call based on ctx.model_id (registry).
    image_model = ctx.model_id or "wan2.7-image"
    if image_model.startswith("wan2.5"):
        i2i_task_id = await client.submit_image_edit(
            prompt=prompt,
            ref_images_url=refs[:3],
            size=str(ctx.params.get("compose_size") or "") or None,
        )
    else:
        i2i_task_id = await client.submit_image_edit_wan27(
            prompt=prompt,
            ref_images_url=refs[:9],
            size=str(ctx.params.get("compose_size") or "") or None,
            model=image_model,
        )

    res = await _poll_until_done(
        client, i2i_task_id, poll, ctx, emit, progress_floor=35
    )
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
    ctx.composed_image_url = composed_url
    ctx.asset_urls["composed_image_url"] = composed_url

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
    ctx: HappyhorsePipelineContext,
    client: HappyhorseDashScopeClient,
    tm: HappyhorseTaskManager,
    emit: EmitFn,
    poll: PollSchedule,
) -> None:
    image_url = str(ctx.asset_urls.get("image_url") or "")
    video_url = str(ctx.asset_urls.get("video_url") or "")
    audio_url = str(ctx.asset_urls.get("audio_url") or "")
    first_frame_url = str(ctx.asset_urls.get("first_frame_url") or "")
    last_frame_url = str(ctx.asset_urls.get("last_frame_url") or "")
    source_video_url = str(ctx.asset_urls.get("source_video_url") or "")
    reference_urls: list[str] = []
    raw_refs = ctx.asset_urls.get("reference_urls")
    if isinstance(raw_refs, list):
        reference_urls = [str(u) for u in raw_refs if u]

    prompt = str(ctx.params.get("prompt") or "")
    resolution = str(ctx.params.get("resolution") or "")
    aspect = str(ctx.params.get("aspect_ratio") or ctx.params.get("aspect") or "")
    duration_raw = ctx.params.get("duration")
    duration = _safe_float(duration_raw) if duration_raw is not None else None
    task_type = ctx.params.get("task_type")

    # ── Video synthesis modes (HappyHorse + Wan 2.6/2.7) ────────────
    if ctx.mode in _VIDEO_SYNTH_MODES:
        # ``long_video`` is normally driven by happyhorse_long_video.py
        # (Phase 7) which generates each segment via i2v + concats with
        # ffmpeg. When run through this pipeline directly it behaves as
        # a single i2v segment — the long-video runner sets
        # ``mode='long_video'`` on the parent task and per-segment tasks
        # use their own mode (i2v / t2v).
        ctx.dashscope_endpoint = ctx.model_id
        ctx.dashscope_id = await client.submit_video_synth(
            mode=ctx.mode,
            model_id=ctx.model_id,
            prompt=prompt,
            first_frame_url=first_frame_url or None,
            last_frame_url=last_frame_url or None,
            reference_urls=reference_urls or None,
            source_video_url=source_video_url or None,
            driving_audio_url=str(ctx.params.get("driving_audio_url") or "") or None,
            resolution=resolution or None,
            aspect=aspect or None,
            duration=duration,
            task_type=str(task_type) if task_type else None,
        )

    elif ctx.mode == "photo_speak":
        ctx.dashscope_endpoint = "wan2.2-s2v"
        ctx.dashscope_id = await client.submit_s2v(
            image_url=image_url,
            audio_url=audio_url,
            resolution=resolution or "480P",
            duration=ctx.tts_audio_duration_sec,
        )
    elif ctx.mode == "video_relip":
        ctx.dashscope_endpoint = "videoretalk"
        ctx.dashscope_id = await client.submit_videoretalk(
            video_url=video_url,
            audio_url=audio_url,
        )
    elif ctx.mode == "video_reface":
        ctx.dashscope_endpoint = "wan2.2-animate-mix"
        ctx.dashscope_id = await client.submit_animate_mix(
            image_url=image_url,
            video_url=video_url,
            mode_pro=bool(ctx.params.get("mode_pro")),
            watermark=bool(ctx.params.get("watermark")),
        )
    elif ctx.mode == "pose_drive":
        ctx.dashscope_endpoint = "wan2.2-animate-move"
        ctx.dashscope_id = await client.submit_animate_move(
            image_url=image_url,
            video_url=video_url,
            mode_pro=bool(ctx.params.get("mode_pro")),
            watermark=bool(ctx.params.get("watermark")),
        )
    elif ctx.mode == "avatar_compose":
        ctx.dashscope_endpoint = "wan2.2-s2v"
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
            resolution=resolution or "480P",
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

    res = await _poll_until_done(
        client, ctx.dashscope_id, poll, ctx, emit, progress_floor=60
    )
    if not res.get("is_ok"):
        raise VendorError(
            f"video synth failed: {res.get('error_message') or 'unknown'}",
            retryable=False,
            kind=res.get("error_kind") or "server",
        )
    ctx.video_url = res.get("output_url")
    ctx.last_frame_url = res.get("last_frame_url")
    usage = res.get("usage") or {}
    if isinstance(usage, dict):
        for key in ("video_duration", "duration", "video_length"):
            if usage.get(key):
                ctx.video_duration_sec = float(usage[key])
                break


# ─── Step 7 · finalize ────────────────────────────────────────────────


async def _step_finalize(
    ctx: HappyhorsePipelineContext,
    plugin_id: str,  # noqa: ARG001
    tm: HappyhorseTaskManager,
    emit: EmitFn,
    *,
    base_data_dir: Path | None = None,
    output_subdir_mode: str = "task",
    output_naming_rule: str = "{filename}",
) -> None:
    output_local: Path | None = None
    last_frame_local: Path | None = None
    if ctx.video_url:
        try:
            output_local = await _download_url(
                ctx.video_url, ctx.task_dir, fallback_ext="mp4"
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "happyhorse-video: video download failed for task %s: %s",
                ctx.task_id,
                e,
            )
    if ctx.last_frame_url:
        try:
            last_frame_local = await _download_url(
                ctx.last_frame_url, ctx.task_dir, fallback_ext="png"
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "happyhorse-video: last_frame download failed for task %s: %s",
                ctx.task_id,
                e,
            )

    if output_local is not None:
        ctx.video_path = output_local
        if base_data_dir is not None:
            try:
                relocated = _relocate_output(
                    output_local,
                    ctx=ctx,
                    base_data_dir=Path(base_data_dir),
                    subdir_mode=output_subdir_mode,
                    naming_rule=output_naming_rule,
                )
                if relocated is not None and relocated != output_local:
                    ctx.video_path = relocated
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "happyhorse-video: output relocation failed for task %s: %s",
                    ctx.task_id,
                    e,
                )

    if last_frame_local is not None:
        ctx.last_frame_path = last_frame_local

    # ── Asset Bus integration ────────────────────────────────────────
    # The plugin layer optionally injects ``_publish_asset`` to register
    # the produced video / last_frame as Asset Bus rows. Returns asset_ids
    # which downstream workbenches consume via ``from_asset_ids``. The
    # metadata payload keeps task lineage (task_id / mode / model_id /
    # cost / dashscope_id) on every asset row so downstream tools can
    # filter without round-tripping through the SQLite ``tasks`` table.
    publish_fn = ctx.params.get("_publish_asset")
    base_metadata: dict[str, Any] = {
        "plugin": "happyhorse-video",
        "task_id": ctx.task_id,
        "mode": ctx.mode,
        "model_id": ctx.model_id,
        "dashscope_id": ctx.dashscope_id,
        "dashscope_endpoint": ctx.dashscope_endpoint,
        "duration_sec": ctx.video_duration_sec,
        "cost_breakdown": ctx.cost_breakdown,
    }
    if callable(publish_fn):
        try:
            if ctx.video_path is not None:
                aid = await publish_fn(
                    str(ctx.video_path),
                    "video",
                    ctx.video_url or "",
                    {**base_metadata, "preview_url": ctx.video_url or ""},
                )
                if aid:
                    ctx.asset_ids.append(str(aid))
            if ctx.last_frame_path is not None:
                aid = await publish_fn(
                    str(ctx.last_frame_path),
                    "image",
                    ctx.last_frame_url or "",
                    {
                        **base_metadata,
                        "role": "last_frame",
                        "preview_url": ctx.last_frame_url or "",
                    },
                )
                if aid:
                    ctx.asset_ids.append(str(aid))
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "happyhorse-video: asset publish failed for task %s: %s",
                ctx.task_id,
                e,
            )

    # ── Persist metadata + DB row ────────────────────────────────────
    persistable_params = {
        k: v
        for k, v in ctx.params.items()
        if not (isinstance(k, str) and k.startswith("_"))
    }
    metadata = {
        "task_id": ctx.task_id,
        "mode": ctx.mode,
        "model_id": ctx.model_id,
        "params": persistable_params,
        "asset_urls": ctx.asset_urls,
        "tts_audio_duration_sec": ctx.tts_audio_duration_sec,
        "tts_engine_used": ctx.tts_engine_used,
        "video_duration_sec": ctx.video_duration_sec,
        "cost_breakdown": ctx.cost_breakdown,
        "dashscope_id": ctx.dashscope_id,
        "dashscope_endpoint": ctx.dashscope_endpoint,
        "video_url": ctx.video_url,
        "video_path": str(ctx.video_path) if ctx.video_path else None,
        "last_frame_url": ctx.last_frame_url,
        "last_frame_path": str(ctx.last_frame_path) if ctx.last_frame_path else None,
        "asset_ids": list(ctx.asset_ids),
        "elapsed_sec": round(time.time() - ctx.started_at, 2),
    }
    (ctx.task_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    await tm.update_task_safe(
        ctx.task_id,
        status="succeeded",
        video_url=ctx.video_url or "",
        video_path=str(ctx.video_path) if ctx.video_path else "",
        last_frame_url=ctx.last_frame_url or "",
        last_frame_path=str(ctx.last_frame_path) if ctx.last_frame_path else "",
        asset_ids_json=list(ctx.asset_ids),
        video_duration_sec=ctx.video_duration_sec,
        completed_at=time.time(),
    )
    await _emit(emit, "task_update", _ctx_payload(ctx, progress=100))


async def _download_url(url: str, task_dir: Path, *, fallback_ext: str) -> Path:
    """Download a public URL into ``task_dir``. Used for the main video
    output and the optional last_frame image."""
    import httpx

    if not url:
        raise ValueError("no url to download")
    name = url.split("?", 1)[0].rsplit("/", 1)[-1] or f"output.{fallback_ext}"
    if "." not in name:
        name = f"{name}.{fallback_ext}"
    target = task_dir / name
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


# ─── Output organisation (subdir + naming rule) ───────────────────────


_OUTPUT_SUBDIR_MODES = {"task", "date", "mode", "date_mode", "flat"}


def _safe_path_segment(s: str) -> str:
    bad = '<>:"/\\|?*'
    cleaned = "".join("_" if ch in bad or ord(ch) < 32 else ch for ch in s)
    cleaned = cleaned.strip(" .") or "output"
    return cleaned[:120]


def _relocate_output(
    src: Path,
    *,
    ctx: HappyhorsePipelineContext,
    base_data_dir: Path,
    subdir_mode: str,
    naming_rule: str,
) -> Path | None:
    if subdir_mode not in _OUTPUT_SUBDIR_MODES:
        subdir_mode = "task"
    template = (naming_rule or "{filename}").strip() or "{filename}"

    now = datetime.fromtimestamp(ctx.started_at, tz=UTC).astimezone()
    date = now.strftime("%Y-%m-%d")
    timestr = now.strftime("%H%M%S")
    datetime_str = f"{date}_{timestr}"
    short_id = ctx.task_id[:8]
    mode = _safe_path_segment(ctx.mode)
    src_stem = src.stem
    src_ext = src.suffix.lstrip(".") or "mp4"

    placeholders = {
        "task_id": ctx.task_id,
        "short_id": short_id,
        "date": date,
        "time": timestr,
        "datetime": datetime_str,
        "mode": mode,
        "model": _safe_path_segment(ctx.model_id or ""),
        "filename": src_stem,
        "ext": src_ext,
    }

    if subdir_mode == "task":
        sub_dir: Path | None = None
    elif subdir_mode == "date":
        sub_dir = base_data_dir / "outputs" / date
    elif subdir_mode == "mode":
        sub_dir = base_data_dir / "outputs" / mode
    elif subdir_mode == "date_mode":
        sub_dir = base_data_dir / "outputs" / date / mode
    else:
        sub_dir = base_data_dir / "outputs"

    class _Defaults(dict):
        def __missing__(self, key: str) -> str:  # pragma: no cover
            return "{" + key + "}"

    name_no_ext = template.format_map(_Defaults(placeholders))
    name_no_ext = _safe_path_segment(name_no_ext)
    if name_no_ext.lower().endswith("." + src_ext.lower()):
        name_no_ext = name_no_ext[: -(len(src_ext) + 1)]
    final_name = f"{name_no_ext}.{src_ext}"

    if sub_dir is None:
        target = src.parent / final_name
    else:
        sub_dir.mkdir(parents=True, exist_ok=True)
        target = sub_dir / final_name

    if target.resolve() == src.resolve():
        return None

    if target.exists():
        stem, dot, ext = final_name.rpartition(".")
        n = 2
        while True:
            cand = (
                target.parent / f"{stem}-{n}.{ext}"
                if dot
                else target.parent / f"{final_name}-{n}"
            )
            if not cand.exists():
                target = cand
                break
            n += 1

    src.replace(target)
    return target


# ─── Step 8 · handle_exception ────────────────────────────────────────


async def _step_handle_exception(
    ctx: HappyhorsePipelineContext,
    exc: BaseException,
    tm: HappyhorseTaskManager,
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
    except Exception:  # noqa: BLE001 — never let cleanup raise
        logger.exception(
            "happyhorse_pipeline: failed to persist error for %s", ctx.task_id
        )

    await _emit(emit, "task_update", _ctx_payload(ctx, progress=100))


# ─── Polling helper ───────────────────────────────────────────────────


async def _poll_until_done(
    client: HappyhorseDashScopeClient,
    dashscope_id: str,
    poll: PollSchedule,
    ctx: HappyhorsePipelineContext,
    emit: EmitFn,
    *,
    progress_floor: int,
) -> dict[str, Any]:
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
                f"DashScope task {dashscope_id} did not finish in "
                f"{poll.total_timeout_sec:.0f}s",
                retryable=False,
                kind="timeout",
            )

        try:
            res = await client.query_task(dashscope_id)
        except VendorError:
            await asyncio.sleep(poll.interval_for(elapsed))
            continue

        status = str(res.get("status") or "")
        if status != last_status or (time.time() - last_emit) > 2.0:
            last_status = status
            last_emit = time.time()
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
    ctx: HappyhorsePipelineContext,
    *,
    progress: int | None = None,
    dashscope_status: str | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "task_id": ctx.task_id,
        "mode": ctx.mode,
        "model_id": ctx.model_id,
        "asset_urls": _stringify_assets(ctx.asset_urls),
        "tts_audio_duration_sec": ctx.tts_audio_duration_sec,
        "tts_engine_used": ctx.tts_engine_used,
        "video_duration_sec": ctx.video_duration_sec,
        "dashscope_id": ctx.dashscope_id,
        "dashscope_endpoint": ctx.dashscope_endpoint,
        "video_url": ctx.video_url,
        "video_path": str(ctx.video_path) if ctx.video_path else None,
        "last_frame_url": ctx.last_frame_url,
        "last_frame_path": str(ctx.last_frame_path) if ctx.last_frame_path else None,
        "asset_ids": list(ctx.asset_ids),
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


def _stringify_assets(d: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, list):
            out[k] = [str(x) for x in v]
        else:
            out[k] = str(v) if v is not None else ""
    return out


async def _emit(emit: EmitFn, event: str, payload: dict[str, Any]) -> None:
    try:
        result = emit(event, payload)
        if asyncio.iscoroutine(result):
            await result
    except Exception:  # noqa: BLE001
        logger.exception("emit(%s) failed for task %s", event, payload.get("task_id"))


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
