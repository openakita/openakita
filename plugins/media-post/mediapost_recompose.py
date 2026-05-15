"""Smart vertical/square recompose pipeline (mode 2: ``multi_aspect``).

Per ``docs/media-post-plan.md`` §6.2 the algorithm has 4 steps:

1. ``detect_scene_cuts`` — run ``ffmpeg -vf select='gt(scene,0.4)'`` and
   parse ``showinfo`` ``pts_time:`` markers from stderr.
2. ``extract_frames`` — ``fps=2,scale=640:360`` to disk, base64 each
   frame for the VLM.
3. ``ema_smooth`` per scene segment — single-sided EMA with
   ``alpha=0.15``; clamp the resulting crop centers into
   ``[crop_w/2, orig_w - crop_w/2]``.
4. ``build_crop_x_expression`` — nested ``if(lt(t,...))`` ffmpeg
   expression, then ``run_ffmpeg_crop`` with optional letterbox pad.

P0 sharp edges (per VALIDATION.md):

- ffmpeg crop expression nesting limit is **~98 levels** on the target
  build; v1.0 caps depth at 95 and downsamples longer segments to
  every-Nth point. The downsampling is uniform so the overall
  trajectory shape is preserved.
- ``shell=True`` is forbidden by red-line §13. Every ``ffmpeg``
invocation goes through ``asyncio.create_subprocess_# FIX: 移除exec，改用安全方式
# *cmd)`` and
  the ``crop=...`` filter string is passed as a single ``-vf``
  argument, so the inner single quotes are interpreted by ffmpeg's
  filter grammar (not by a shell).
- ``stderr=PIPE, stdout=DEVNULL`` for scene detection — ffmpeg writes
  ``showinfo`` lines to stderr.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mediapost_models import MediaPostError

logger = logging.getLogger(__name__)


# Verbatim from §2.2 SMART_RECOMPOSE_PROMPT.
SMART_RECOMPOSE_PROMPT = """You are a video reframer detecting MAIN SUBJECT positions for vertical (9:16) reframe.

You will receive {frame_count} frames from a {orig_w}x{orig_h} horizontal video, in this exact order.
Frame indices: {frame_indices}
Your JSON output MUST be an array of SAME length and SAME order.

For each frame, locate the PRIMARY subject (person face if exists; else dominant moving subject).
Output bounding_box (x,y,w,h) in pixels of the ORIGINAL frame coordinates.
Return null bbox if no clear subject (will fallback to center crop).

[
  {{
    "frame_idx": <int>,
    "subject_detected": <bool>,
    "subject_kind": "<face | person | object | none>",
    "bounding_box": {{"x": <int>, "y": <int>, "width": <int>, "height": <int>}} | null,
    "confidence": <float 0-1>,
    "scene_changed": <bool>
  }}, ...
]
"""


# Locked defaults per §2.4.
DEFAULT_FPS = 2.0
DEFAULT_SCENE_THRESHOLD = 0.4
DEFAULT_EMA_ALPHA = 0.15
DEFAULT_VLM_BATCH_SIZE = 8
DEFAULT_VLM_CONCURRENCY = 4

# VALIDATION.md: empirical ffmpeg expression nesting limit on the target
# build was 98. v1.0 caps the depth at 95 to leave a safety margin.
MAX_CROP_EXPR_DEPTH = 95

# Frame extraction scale used for VLM subject detection (per §2.2).
DEFAULT_FRAME_SCALE = "640:360"


@dataclass
class RecomposeContext:
    """Per-task config for :func:`smart_recompose`."""

    input_video: Path
    orig_width: int
    orig_height: int
    target_aspect: str  # "9:16" | "1:1"
    output_video: Path
    fps: float = DEFAULT_FPS
    scene_threshold: float = DEFAULT_SCENE_THRESHOLD
    ema_alpha: float = DEFAULT_EMA_ALPHA
    letterbox_fallback: bool = True
    vlm_batch_size: int = DEFAULT_VLM_BATCH_SIZE
    vlm_concurrency: int = DEFAULT_VLM_CONCURRENCY
    frame_scale: str = DEFAULT_FRAME_SCALE
    extra: dict[str, Any] = field(default_factory=dict)


async def smart_recompose(
    ctx: RecomposeContext,
    vlm_client: Any,
    *,
    progress_cb: Callable[[float, str], Any] | None = None,
) -> dict[str, Any]:
    """Run the 4-step recompose. Returns the trajectory + scene cuts."""
    if ctx.orig_width <= 0 or ctx.orig_height <= 0:
        raise MediaPostError("format", "orig_width/orig_height must be > 0")
    crop_w, crop_h = compute_crop_dims(
        ctx.orig_width, ctx.orig_height, ctx.target_aspect
    )

    if progress_cb:
        await _safe_call(progress_cb, 0.05, "detecting scene cuts")
    scene_cuts = await detect_scene_cuts(ctx.input_video, ctx.scene_threshold)

    frame_dir = ctx.output_video.parent / f"frames_{ctx.target_aspect.replace(':', '_')}"
    frame_dir.mkdir(parents=True, exist_ok=True)
    if progress_cb:
        await _safe_call(progress_cb, 0.20, "extracting frames")
    await extract_frames(ctx.input_video, frame_dir, ctx.fps, scale=ctx.frame_scale)
    frame_files = sorted(frame_dir.glob("*.png"))
    if not frame_files:
        raise MediaPostError("dependency", "ffmpeg extracted 0 frames")

    if progress_cb:
        await _safe_call(progress_cb, 0.40, "running VLM subject detection")
    detections = await _detect_subjects(
        frame_files,
        ctx.orig_width,
        ctx.orig_height,
        vlm_client,
        batch_size=ctx.vlm_batch_size,
        concurrency=ctx.vlm_concurrency,
    )

    if progress_cb:
        await _safe_call(progress_cb, 0.65, "smoothing crop trajectory")
    trajectory = _build_trajectory(
        detections,
        fps=ctx.fps,
        scene_cuts=scene_cuts,
        orig_w=ctx.orig_width,
        crop_w=crop_w,
        ema_alpha=ctx.ema_alpha,
    )

    if not trajectory:
        # No usable detections at all — fallback to static center crop.
        trajectory = [(0.0, max(0.0, (ctx.orig_width - crop_w) / 2))]

    downsampled, depth = _downsample_to_depth_cap(trajectory, MAX_CROP_EXPR_DEPTH)
    expr = build_crop_x_expression(downsampled)

    if progress_cb:
        await _safe_call(progress_cb, 0.80, "rendering ffmpeg crop")
    await run_ffmpeg_crop(
        ctx.input_video,
        ctx.output_video,
        crop_w=crop_w,
        crop_h=crop_h,
        x_expr=expr,
        letterbox_if_needed=ctx.letterbox_fallback,
    )

    if progress_cb:
        await _safe_call(progress_cb, 1.0, "done")

    return {
        "trajectory": [{"t": t, "x_left": x} for t, x in trajectory],
        "scene_cuts": scene_cuts,
        "expr_depth": depth,
        "crop_w": crop_w,
        "crop_h": crop_h,
        "fallback_letterbox_used": ctx.letterbox_fallback,
    }


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------


def compute_crop_dims(orig_w: int, orig_h: int, aspect: str) -> tuple[int, int]:
    """Return ``(crop_w, crop_h)`` for the requested aspect.

    The crop is the largest rectangle of the requested aspect that fits
    inside the source frame. We anchor on the smaller dimension so the
    output never upscales.
    """
    try:
        a, b = aspect.split(":")
        ratio = float(a) / float(b)
    except (ValueError, ZeroDivisionError) as exc:
        raise MediaPostError("format", f"invalid aspect ratio: {aspect!r}") from exc
    if orig_w / orig_h > ratio:
        # source is wider than target — height is the constraint.
        crop_h = orig_h
        crop_w = int(round(orig_h * ratio))
    else:
        crop_w = orig_w
        crop_h = int(round(orig_w / ratio))
    crop_w = max(2, crop_w - (crop_w % 2))
    crop_h = max(2, crop_h - (crop_h % 2))
    return crop_w, crop_h


# ---------------------------------------------------------------------------
# EMA + trajectory
# ---------------------------------------------------------------------------


def ema_smooth(xs: list[float], alpha: float) -> list[float]:
    """Single-sided EMA. Returns a list of the same length as ``xs``.

    ``alpha`` is the new-sample weight; smaller alpha = more smoothing.
    Empty input returns empty output. Original-source CutClaw never used
    EMA — this is media-post's contribution and the value 0.15 was
    chosen empirically (§2.4).
    """
    if not xs:
        return []
    if not 0.0 < alpha <= 1.0:
        raise MediaPostError("format", f"alpha must be in (0, 1]; got {alpha!r}")
    out = [xs[0]]
    for x in xs[1:]:
        out.append(alpha * x + (1 - alpha) * out[-1])
    return out


def _build_trajectory(
    detections: list[dict[str, Any] | None],
    *,
    fps: float,
    scene_cuts: list[float],
    orig_w: int,
    crop_w: int,
    ema_alpha: float,
) -> list[tuple[float, float]]:
    """Convert per-frame VLM detections into a list of ``(time_sec, x_left)``."""
    if fps <= 0:
        raise MediaPostError("format", "fps must be > 0 for recompose")
    time_centers: list[tuple[float, float]] = []
    half_crop = crop_w / 2.0
    center_default = orig_w / 2.0
    for i, det in enumerate(detections):
        time_sec = i / fps
        x_c = _detect_x_center(det, default=center_default)
        time_centers.append((time_sec, x_c))

    if not scene_cuts:
        scene_cuts = [0.0, time_centers[-1][0] + 1.0 if time_centers else 1.0]

    smoothed: list[tuple[float, float]] = []
    for seg_start, seg_end in zip(scene_cuts[:-1], scene_cuts[1:], strict=False):
        seg = [(t, x) for t, x in time_centers if seg_start <= t < seg_end]
        if not seg:
            continue
        ema_xs = ema_smooth([x for _, x in seg], ema_alpha)
        for (t, _), cx in zip(seg, ema_xs, strict=False):
            x_left = cx - half_crop
            x_left = max(0.0, min(x_left, float(orig_w - crop_w)))
            smoothed.append((t, x_left))

    return smoothed


def _detect_x_center(det: dict[str, Any] | None, *, default: float) -> float:
    if not isinstance(det, dict):
        return default
    if not det.get("subject_detected"):
        return default
    bbox = det.get("bounding_box")
    if not isinstance(bbox, dict):
        return default
    try:
        x = float(bbox.get("x", 0))
        w = float(bbox.get("width", 0))
    except (TypeError, ValueError):
        return default
    if w <= 0:
        return default
    return x + w / 2.0


def _downsample_to_depth_cap(
    trajectory: list[tuple[float, float]],
    cap: int,
) -> tuple[list[tuple[float, float]], int]:
    """Uniform-stride downsample so the output expression stays under ``cap``."""
    n = len(trajectory)
    if n <= cap:
        return list(trajectory), n
    stride = max(1, n // cap)
    sampled = trajectory[::stride]
    if sampled[-1] != trajectory[-1]:
        sampled.append(trajectory[-1])
    if len(sampled) > cap:
        # Final guard: clip to cap from the head, keeping the last row.
        sampled = sampled[: cap - 1] + [trajectory[-1]]
    return sampled, len(sampled)


def build_crop_x_expression(time_lefts: list[tuple[float, float]]) -> str:
    """Build a nested ``if(lt(t,T),X,...)`` ffmpeg expression.

    The default value (innermost branch) is the last ``x_left``. Time
    boundaries are emitted in ascending order so each ``if(lt(t,T))``
    correctly matches "before T". An empty input falls back to ``"0"``
    so the caller never produces a syntactically broken filter.
    """
    if not time_lefts:
        return "0"
    if len(time_lefts) == 1:
        return f"{time_lefts[0][1]:.1f}"
    expr = f"{time_lefts[-1][1]:.1f}"
    for t, x in reversed(time_lefts[:-1]):
        expr = f"if(lt(t,{t:.3f}),{x:.1f},{expr})"
    return expr


# ---------------------------------------------------------------------------
# ffmpeg helpers
# ---------------------------------------------------------------------------


_FFMPEG_BIN = "ffmpeg"
_FFPROBE_BIN = "ffprobe"


async def detect_scene_cuts(video: Path, threshold: float = 0.4) -> list[float]:
    """Run ``ffmpeg -vf select=gt(scene,T),showinfo`` and parse cut times."""
    cmd = [
        _FFMPEG_BIN,
        "-hide_banner",
        "-nostats",
        "-i",
        str(video),
        "-vf",
        f"select='gt(scene,{threshold})',showinfo",
        "-f",
        "null",
        "-",
proc = await asyncio.create_subprocess_# FIX: 移除exec，改用安全方式
# 
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    cuts: list[float] = [0.0]
    for line in stderr.decode("utf-8", errors="replace").splitlines():
        if "showinfo" in line or "Parsed_showinfo" in line:
            m = re.search(r"pts_time:([\d.]+)", line)
            if m:
                try:
                    cuts.append(float(m.group(1)))
                except ValueError:
                    continue
    duration = await ffprobe_duration(video)
    cuts.append(duration)
    return sorted(set(cuts))


async def ffprobe_duration(video: Path) -> float:
    """Return duration_sec via ffprobe; 0.0 on failure (caller handles)."""
    cmd = [
        _FFPROBE_BIN,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(video),
proc = await asyncio.create_subprocess_# FIX: 移除exec，改用安全方式
# 
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        return float(stdout.decode("utf-8", errors="replace").strip() or 0.0)
    except (FileNotFoundError, ValueError):
        return 0.0


async def extract_frames(
    video: Path, out_dir: Path, fps: float, scale: str
) -> None:
    """Run ``ffmpeg -vf fps=N,scale=W:H -y ...frame_%05d.png``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        _FFMPEG_BIN,
        "-y",
        "-hide_banner",
        "-i",
        str(video),
        "-vf",
proc = await asyncio.create_subprocess_# FIX: 移除exec，改用安全方式
# 
        str(out_dir / "frame_%05d.png"),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        tail = stderr.decode("utf-8", errors="replace")[-400:]
        raise MediaPostError(
            "dependency",
            f"ffmpeg extract_frames failed (rc={proc.returncode}): {tail}",
        )


async def run_ffmpeg_crop(
    video: Path,
    output: Path,
    *,
    crop_w: int,
    crop_h: int,
    x_expr: str,
    letterbox_if_needed: bool,
) -> None:
or colons. ``asyncio.create_subprocess_# FIX: 移除exec，改用安全方式
# *cmd)`` does NOT spawn

    The single-quotes wrapping around ``x_expr`` are required by the
    ffmpeg filter parser when the expression contains commas, parens,
    or colons. ``asyncio.create_subprocess_exec(*cmd)`` does NOT spawn
    a shell so the quoting is interpreted only by ffmpeg.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    vf = f"crop={crop_w}:{crop_h}:'{x_expr}':0"
    if letterbox_if_needed:
        vf += (
            f",pad={crop_w}:{crop_h}:(ow-iw)/2:(oh-ih)/2:color=black"
        )
    cmd = [
        _FFMPEG_BIN,
        "-y",
        "-hide_banner",
        "-i",
        str(video),
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-c:a",
        "copy",
        str(output),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        tail = stderr.decode("utf-8", errors="replace")[-400:]
        raise MediaPostError(
            "dependency",
            f"ffmpeg crop failed (rc={proc.returncode}): {tail}",
        )


# ---------------------------------------------------------------------------
# VLM subject detection
# ---------------------------------------------------------------------------


async def _detect_subjects(
    frame_files: list[Path],
    orig_w: int,
    orig_h: int,
    vlm_client: Any,
    *,
    batch_size: int,
    concurrency: int,
) -> list[dict[str, Any] | None]:
    frames_b64 = [
        base64.b64encode(p.read_bytes()).decode("ascii") for p in frame_files
    ]
    indices = list(range(len(frame_files)))

    def _kwargs_factory(batch_indices: list[int]) -> dict[str, Any]:
        return {
            "frame_count": len(batch_indices),
            "frame_indices": batch_indices,
            "orig_w": orig_w,
            "orig_h": orig_h,
        }

    detections = await vlm_client.call_vlm_concurrent(
        frames_b64,
        indices,
        SMART_RECOMPOSE_PROMPT,
        _kwargs_factory,
        batch_size=batch_size,
        concurrency=concurrency,
    )
    return list(detections)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


async def _safe_call(cb: Callable[..., Any], *args: Any) -> None:
    try:
        result = cb(*args)
        if asyncio.iscoroutine(result):
            await result
    except Exception:
        logger.debug("progress_cb raised", exc_info=True)


__all__ = [
    "DEFAULT_EMA_ALPHA",
    "DEFAULT_FPS",
    "DEFAULT_FRAME_SCALE",
    "DEFAULT_SCENE_THRESHOLD",
    "DEFAULT_VLM_BATCH_SIZE",
    "DEFAULT_VLM_CONCURRENCY",
    "MAX_CROP_EXPR_DEPTH",
    "RecomposeContext",
    "SMART_RECOMPOSE_PROMPT",
    "build_crop_x_expression",
    "compute_crop_dims",
    "detect_scene_cuts",
    "ema_smooth",
    "extract_frames",
    "ffprobe_duration",
    "run_ffmpeg_crop",
    "smart_recompose",
]
