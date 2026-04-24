"""8-step pipeline orchestrator for the 4 media-post modes.

Per ``docs/media-post-plan.md`` §6.6 + §3.4. Each mode has its own
``STEP_DISPATCH`` row; the orchestrator iterates the steps, broadcasts
``task_update`` events between steps, and translates any
:class:`MediaPostError` into a row update + UI broadcast.

Pipeline guarantees (§3.4):

- Cooperative cancel — ``tm.is_canceled(task_id)`` is polled before
  every step. If true, the task is marked ``cancelled`` and the
  pipeline exits immediately.
- Single failure surface — every uncaught exception becomes
  ``error_kind="unknown"`` so the UI ErrorPanel always has a hint
  card to render.
- Cost approval — if the estimated cost exceeds the warn threshold
  (``COST_THRESHOLD_WARN_CNY``) and the params do NOT include
  ``cost_approved=True`` the task is short-circuited with status
  ``approval_required``. UI re-submits with ``cost_approved=True``.

The orchestrator never calls ffmpeg/Playwright/VLM directly — those
side-effects live in the per-mode modules
(``mediapost_cover_picker`` / ``mediapost_recompose`` /
``mediapost_seo_generator`` / ``mediapost_chapter_renderer``).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mediapost_chapter_renderer import (
    ChapterCardSpec,
    ChapterRenderContext,
    render_chapter_cards,
)
from mediapost_cover_picker import CoverPickContext, pick_covers
from mediapost_models import (
    ALLOWED_PLATFORMS,
    COST_THRESHOLD_WARN_CNY,
    ERROR_HINTS,
    MODES_BY_ID,
    MediaPostError,
    estimate_cost,
)
from mediapost_recompose import RecomposeContext, ffprobe_duration, smart_recompose
from mediapost_seo_generator import generate_seo_pack

logger = logging.getLogger(__name__)


EmitFn = Callable[[str, dict[str, Any]], Any]
StepFn = Callable[["MediaPostContext"], Awaitable[None]]


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------


@dataclass
class MediaPostContext:
    """Per-task pipeline context (~22 fields per Pixelle A2)."""

    task_id: str
    mode: str
    params: dict[str, Any]
    task_dir: Path
    api: Any  # PluginAPI; kept Any to avoid forcing a host import in unit tests.
    tm: Any  # MediaPostTaskManager
    vlm_client: Any  # MediaPostVlmClient

    video_path: Path | None = None
    video_meta: dict[str, Any] = field(default_factory=dict)
    cost_estimated: float = 0.0
    cost_kind: str = "ok"

    cover_rows: list[dict[str, Any]] = field(default_factory=list)
    recompose_rows: list[dict[str, Any]] = field(default_factory=list)
    seo_rows: list[dict[str, Any]] = field(default_factory=list)
    chapter_rows: list[dict[str, Any]] = field(default_factory=list)

    error_kind: str | None = None
    error_message: str | None = None
    error_hints_zh: list[str] = field(default_factory=list)
    error_hints_en: list[str] = field(default_factory=list)

    cancelled: bool = False
    approval_required: bool = False


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


async def setup_environment(ctx: MediaPostContext) -> None:
    ctx.task_dir.mkdir(parents=True, exist_ok=True)


async def estimate_cost_step(ctx: MediaPostContext) -> None:
    duration_sec = float(ctx.params.get("duration_sec", 0.0))
    if not duration_sec and ctx.video_path is not None:
        duration_sec = await ffprobe_duration(ctx.video_path)
        ctx.video_meta.setdefault("duration_sec", duration_sec)

    preview = estimate_cost(
        ctx.mode,
        duration_sec=duration_sec,
        quantity=int(ctx.params.get("quantity", 8)),
        target_aspects=list(ctx.params.get("target_aspects") or []) or None,
        platforms=list(ctx.params.get("platforms") or []) or None,
        recompose_fps=float(ctx.params.get("recompose_fps", 2.0)),
        chapter_count=len(ctx.params.get("chapters") or []),
    )
    ctx.cost_estimated = preview.total_cny
    ctx.cost_kind = preview.cost_kind

    await ctx.tm.update_task_safe(
        ctx.task_id,
        cost_estimated=preview.total_cny,
        cost_kind=preview.cost_kind,
    )

    requires_approval = (
        preview.total_cny >= COST_THRESHOLD_WARN_CNY
        and not bool(ctx.params.get("cost_approved"))
    )
    if requires_approval:
        ctx.approval_required = True
        await ctx.tm.update_task_safe(ctx.task_id, status="approval_required")


async def prepare_assets(ctx: MediaPostContext) -> None:
    if ctx.video_path is not None and ctx.video_path.exists():
        await ctx.tm.update_task_safe(
            ctx.task_id,
            video_path=str(ctx.video_path),
            video_meta_json=json.dumps(ctx.video_meta, ensure_ascii=False),
        )


# Steps 4 + 5 + 6 are mode-specific; we factor them into one combined
# step per mode so each mode owns its full execute-and-write contract.


async def run_cover_pick(ctx: MediaPostContext) -> None:
    if ctx.video_path is None:
        raise MediaPostError("format", "cover_pick requires a video_path")
    cover_ctx = CoverPickContext(
        input_video=ctx.video_path,
        out_dir=ctx.task_dir / "cover_pick",
        quantity=int(ctx.params.get("quantity", 8)),
        min_score_threshold=float(ctx.params.get("min_score_threshold", 3.0)),
        platform_hint=str(ctx.params.get("platform_hint", "universal")),
    )

    async def _on_progress(progress: float, label: str) -> None:
        await _broadcast_progress(ctx, progress, label)

    rows = await pick_covers(cover_ctx, ctx.vlm_client, progress_cb=_on_progress)
    for row in rows:
        await ctx.tm.insert_cover_result(task_id=ctx.task_id, **row)
    ctx.cover_rows = rows


async def run_multi_aspect(ctx: MediaPostContext) -> None:
    if ctx.video_path is None:
        raise MediaPostError("format", "multi_aspect requires a video_path")
    aspects = list(ctx.params.get("target_aspects") or ["9:16"])
    orig_w = int(ctx.video_meta.get("width") or ctx.params.get("orig_width") or 1920)
    orig_h = int(ctx.video_meta.get("height") or ctx.params.get("orig_height") or 1080)

    rows: list[dict[str, Any]] = []
    for idx, aspect in enumerate(aspects):
        out = ctx.task_dir / "multi_aspect" / f"output_{aspect.replace(':', '_')}.mp4"
        rec_ctx = RecomposeContext(
            input_video=ctx.video_path,
            orig_width=orig_w,
            orig_height=orig_h,
            target_aspect=aspect,
            output_video=out,
            fps=float(ctx.params.get("recompose_fps", 2.0)),
            ema_alpha=float(ctx.params.get("ema_alpha", 0.15)),
            scene_threshold=float(ctx.params.get("scene_threshold", 0.4)),
            letterbox_fallback=bool(ctx.params.get("letterbox_fallback", True)),
        )

        async def _on_progress(
            progress: float,
            label: str,
            _i: int = idx,
            _aspect: str = aspect,
        ) -> None:
            base = _i / len(aspects)
            scale = 1.0 / len(aspects)
            await _broadcast_progress(
                ctx, base + scale * progress, f"{_aspect}: {label}"
            )

        result = await smart_recompose(rec_ctx, ctx.vlm_client, progress_cb=_on_progress)
        duration_sec = await ffprobe_duration(out)
        await ctx.tm.insert_recompose_output(
            task_id=ctx.task_id,
            aspect=aspect,
            output_path=str(out),
            output_w=int(result.get("crop_w", 0)),
            output_h=int(result.get("crop_h", 0)),
            duration_sec=duration_sec,
            trajectory=result.get("trajectory"),
            ema_alpha_used=rec_ctx.ema_alpha,
            fps_used=rec_ctx.fps,
            scene_cut_count=len(result.get("scene_cuts", [])),
            fallback_letterbox_used=rec_ctx.letterbox_fallback,
        )
        rows.append(
            {
                "aspect": aspect,
                "output_path": str(out),
                "expr_depth": result.get("expr_depth"),
            }
        )
    ctx.recompose_rows = rows


async def run_seo_pack(ctx: MediaPostContext) -> None:
    platforms = list(ctx.params.get("platforms") or [])
    platforms = [p for p in platforms if p in ALLOWED_PLATFORMS] or sorted(
        ALLOWED_PLATFORMS
    )
    excerpt = str(ctx.params.get("subtitle_excerpt") or "")
    instruction = str(ctx.params.get("instruction") or "")
    title_hint = str(ctx.params.get("video_title_hint") or "")
    include_chapters = bool(ctx.params.get("include_chapters"))
    chapters = ctx.params.get("chapters") or []

    async def _qwen_plus(**kwargs: Any) -> str:
        return await ctx.vlm_client.qwen_plus_call(**kwargs)

    async def _on_progress(progress: float, label: str) -> None:
        await _broadcast_progress(ctx, progress, label)

    pack = await generate_seo_pack(
        video_title_hint=title_hint,
        subtitle_excerpt=excerpt,
        instruction=instruction,
        platforms=platforms,
        qwen_plus_call=_qwen_plus,
        include_chapters=include_chapters,
        chapter_timestamps=chapters,
        progress_cb=_on_progress,
    )

    rows: list[dict[str, Any]] = []
    successes = 0
    for platform in platforms:
        payload = pack.get(platform)
        if payload is None:
            payload = {"_error": "platform call failed"}
        else:
            successes += 1
        await ctx.tm.insert_seo_result(
            task_id=ctx.task_id, platform=platform, payload=payload
        )
        rows.append({"platform": platform, "ok": "_error" not in payload})
    if successes == 0:
        raise MediaPostError("format", "all SEO platforms failed")
    ctx.seo_rows = rows


async def run_chapter_cards(ctx: MediaPostContext) -> None:
    chapters_param = ctx.params.get("chapters") or []
    specs = _build_chapter_specs(chapters_param, ctx.params)
    if not specs:
        raise MediaPostError("format", "chapter_cards requires at least one chapter")

    builtin_templates = ctx.params.get("_builtin_templates") or {}
    templates_dir = ctx.params.get("_templates_dir")
    templates_dir_path = Path(templates_dir) if templates_dir else None

    render_ctx = ChapterRenderContext(
        out_dir=ctx.task_dir / "chapter_cards",
        chapters=specs,
        templates_dir=templates_dir_path,
        builtin_templates=builtin_templates,
        prefer_playwright=bool(ctx.params.get("prefer_playwright", True)),
        drawtext_font=str(ctx.params.get("drawtext_font", "")),
    )

    async def _on_progress(progress: float, label: str) -> None:
        await _broadcast_progress(ctx, progress, label)

    rows = await render_chapter_cards(render_ctx, progress_cb=_on_progress)
    for row in rows:
        await ctx.tm.insert_chapter_card_result(task_id=ctx.task_id, **row)
    ctx.chapter_rows = rows


def _build_chapter_specs(
    chapters: list[Any], params: dict[str, Any]
) -> list[ChapterCardSpec]:
    template_id = str(params.get("template_id", "modern"))
    width = int(params.get("width", 1280))
    height = int(params.get("height", 720))
    extra = dict(params.get("template_params") or {})

    out: list[ChapterCardSpec] = []
    for i, ch in enumerate(chapters, start=1):
        if not isinstance(ch, dict):
            continue
        out.append(
            ChapterCardSpec(
                chapter_index=int(ch.get("chapter_index", i)),
                title=str(ch.get("title", "")),
                subtitle=str(ch.get("subtitle", "")),
                template_id=str(ch.get("template_id", template_id)),
                width=int(ch.get("width", width)),
                height=int(ch.get("height", height)),
                extra_params={**extra, **{k: v for k, v in ch.items() if k.startswith("param_")}},
            )
        )
    return out


# ---------------------------------------------------------------------------
# Step 7+8: write summary + finalize
# ---------------------------------------------------------------------------


async def write_assets(ctx: MediaPostContext) -> None:
    summary = {
        "mode": ctx.mode,
        "cover_count": len(ctx.cover_rows),
        "recompose_count": len(ctx.recompose_rows),
        "seo_platform_count": len(ctx.seo_rows),
        "chapter_count": len(ctx.chapter_rows),
        "cost_estimated_cny": ctx.cost_estimated,
    }
    metadata_path = ctx.task_dir / "metadata.json"
    try:
        metadata_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        logger.debug("write metadata.json failed", exc_info=True)
    await ctx.tm.update_task_safe(
        ctx.task_id, result_summary_json=json.dumps(summary, ensure_ascii=False)
    )


async def finalize(ctx: MediaPostContext) -> None:
    await ctx.tm.update_task_safe(
        ctx.task_id,
        status="completed",
        progress=1.0,
        cost_actual=ctx.cost_estimated,  # v1.0: use estimated as actual.
    )
    await _broadcast_event(
        ctx,
        "task_update",
        {"task_id": ctx.task_id, "status": "completed", "progress": 1.0},
    )


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


_MODE_RUN_STEP: dict[str, StepFn] = {
    "cover_pick": run_cover_pick,
    "multi_aspect": run_multi_aspect,
    "seo_pack": run_seo_pack,
    "chapter_cards": run_chapter_cards,
}


def _build_steps(mode: str) -> list[tuple[str, StepFn]]:
    if mode not in _MODE_RUN_STEP:
        raise MediaPostError("format", f"unknown mode: {mode!r}")
    return [
        ("setup_environment", setup_environment),
        ("estimate_cost", estimate_cost_step),
        ("prepare_assets", prepare_assets),
        ("execute", _MODE_RUN_STEP[mode]),
        ("write_assets", write_assets),
        ("finalize", finalize),
    ]


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


async def run_pipeline(ctx: MediaPostContext) -> None:
    """Execute the mode's step list, handling cancel + error broadcast."""
    if ctx.mode not in MODES_BY_ID:
        await _record_error(ctx, "format", f"unknown mode: {ctx.mode!r}")
        return

    steps = _build_steps(ctx.mode)
    for step_name, step_fn in steps:
        if ctx.tm.is_canceled(ctx.task_id) or ctx.cancelled:
            ctx.cancelled = True
            await ctx.tm.update_task_safe(ctx.task_id, status="cancelled")
            await _broadcast_event(
                ctx,
                "task_update",
                {"task_id": ctx.task_id, "status": "cancelled"},
            )
            return

        await ctx.tm.update_task_safe(
            ctx.task_id, status="running", pipeline_step=step_name
        )
        await _broadcast_event(
            ctx,
            "task_update",
            {"task_id": ctx.task_id, "status": "running", "step": step_name},
        )

        try:
            await step_fn(ctx)
        except MediaPostError as exc:
            await _record_error(ctx, exc.kind, exc.message or str(exc))
            return
        except Exception as exc:
            logger.exception("media-post pipeline unexpected error")
            await _record_error(ctx, "unknown", str(exc))
            return

        if ctx.approval_required:
            await _broadcast_event(
                ctx,
                "task_update",
                {
                    "task_id": ctx.task_id,
                    "status": "approval_required",
                    "cost_estimated": ctx.cost_estimated,
                    "cost_kind": ctx.cost_kind,
                },
            )
            return


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


async def _record_error(ctx: MediaPostContext, kind: str, message: str) -> None:
    canonical = kind if kind in ERROR_HINTS else "unknown"
    hints = ERROR_HINTS[canonical]
    ctx.error_kind = canonical
    ctx.error_message = message
    ctx.error_hints_zh = list(hints.get("hints_zh", []))
    ctx.error_hints_en = list(hints.get("hints_en", []))
    await ctx.tm.update_task_safe(
        ctx.task_id,
        status="failed",
        error_kind=canonical,
        error_message=message,
        error_hints_json=json.dumps(
            {"zh": ctx.error_hints_zh, "en": ctx.error_hints_en},
            ensure_ascii=False,
        ),
    )
    await _broadcast_event(
        ctx,
        "task_update",
        {
            "task_id": ctx.task_id,
            "status": "failed",
            "error_kind": canonical,
            "error_message": message,
            "hints_zh": ctx.error_hints_zh,
            "hints_en": ctx.error_hints_en,
        },
    )


async def _broadcast_progress(
    ctx: MediaPostContext, progress: float, label: str
) -> None:
    progress = max(0.0, min(1.0, float(progress)))
    await ctx.tm.update_task_safe(ctx.task_id, progress=progress)
    await _broadcast_event(
        ctx,
        "task_update",
        {
            "task_id": ctx.task_id,
            "status": "running",
            "progress": progress,
            "step_label": label,
        },
    )


async def _broadcast_event(
    ctx: MediaPostContext, event_type: str, data: dict[str, Any]
) -> None:
    api = ctx.api
    if api is None:
        return
    try:
        api.broadcast_ui_event(event_type, data)
    except Exception:
        logger.debug("broadcast_ui_event failed", exc_info=True)


__all__ = [
    "MediaPostContext",
    "estimate_cost_step",
    "finalize",
    "prepare_assets",
    "run_chapter_cards",
    "run_cover_pick",
    "run_multi_aspect",
    "run_pipeline",
    "run_seo_pack",
    "setup_environment",
    "write_assets",
]
