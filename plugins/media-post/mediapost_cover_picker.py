"""Smart cover-frame picker (mode 1: ``cover_pick``).

Per ``docs/media-post-plan.md`` §6.3:

1. ffmpeg ``thumbnail=N`` filter prefilters ~30 candidates from the full
   video (cheap and selects "the most representative" frame per chunk).
2. Each candidate is base64-encoded and sent to Qwen-VL-max in 8-frame
   batches via :func:`MediaPostVlmClient.call_vlm_concurrent`.
3. Six aesthetic axes are scored; ``overall_score`` is a weighted average
   computed by the VLM per ``COVER_PICK_PROMPT`` in §2.2.
4. Candidates below ``min_score_threshold`` are dropped; the rest sort
   by ``overall_score`` desc and the top-N are copied to ``final/``.

All ffmpeg invocations use ``asyncio.create_subprocess_# FIX: 移除exec，改用安全方式
# *cmd)`` —
``shell=True`` is forbidden by red-line §13. Failures inside the
candidate-extraction step raise :class:`MediaPostError("dependency",
…)`` so the pipeline maps to the dependency hint card.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mediapost_models import MediaPostError

logger = logging.getLogger(__name__)


# Prompt used by Qwen-VL-max for aesthetic scoring (verbatim from §2.2).
COVER_PICK_PROMPT = """You are a video cover designer selecting BEST candidate frames for a {platform} video cover.

You will receive {frame_count} frames in this exact order.
Frame indices: {frame_indices}
Your JSON output MUST be an array of SAME length and SAME order.

Score each frame on:
1. lighting (1-5)        - natural/balanced light, no over/underexposure
2. composition (1-5)     - rule of thirds, leading lines, balance
3. subject_clarity (1-5) - main subject sharp, well-framed, eyes visible if person
4. visual_appeal (1-5)   - color harmony, mood, "stop scrolling" force
5. text_safe_zone (1-5)  - large empty area where title/sticker can go (0=no space, 5=lots)

Output (JSON array, same length & order as input):
[
  {{
    "frame_idx": <int from provided list>,
    "lighting": <int>, "composition": <int>, "subject_clarity": <int>,
    "visual_appeal": <int>, "text_safe_zone": <int>,
    "overall_score": <float, weighted: lighting*0.15 + composition*0.20 + subject_clarity*0.25 + visual_appeal*0.30 + text_safe_zone*0.10>,
    "main_subject_bbox": {{"x": <int>, "y": <int>, "width": <int>, "height": <int>}} | null,
    "best_for": "<thumbnail | hero_image | chapter_card | none>",
    "reason": "<one sentence>"
  }}, ...
]
"""


# Per §2.3: ffmpeg ``thumbnail=N`` chooses the most representative frame
# from each window of N frames. Setting N=300 over a typical 30-fps video
# yields one candidate every ~10 seconds, which produces ~30 candidates
# from a 5-minute video.
DEFAULT_THUMBNAIL_WINDOW = 300

# Number of candidates that go into the VLM scoring round. CutClaw's
# experience: keeping this <= 32 (== 4 batches of 8) keeps cost under
# ~¥0.32 per cover_pick run.
DEFAULT_CANDIDATE_COUNT = 30

# Prefilter scale — keeps base64 payload tiny (~80 KB / frame).
DEFAULT_CANDIDATE_SCALE = "512:288"


@dataclass
class CoverPickContext:
    """Per-task config for :func:`pick_covers`."""

    input_video: Path
    out_dir: Path
    quantity: int = 8
    min_score_threshold: float = 3.0
    platform_hint: str = "universal"
    candidate_count: int = DEFAULT_CANDIDATE_COUNT
    thumbnail_window: int = DEFAULT_THUMBNAIL_WINDOW
    candidate_scale: str = DEFAULT_CANDIDATE_SCALE
    vlm_batch_size: int = 8
    vlm_concurrency: int = 4
    extra: dict[str, Any] = field(default_factory=dict)


async def pick_covers(
    ctx: CoverPickContext,
    vlm_client: Any,
    *,
    progress_cb: Callable[[float, str], Any] | None = None,
) -> list[dict[str, Any]]:
    """Run the 4-step cover-pick pipeline. Returns up to ``ctx.quantity`` rows.

    Each row has the shape::

        {
          "rank": int (1-indexed),
          "cover_path": str (final destination under out_dir/final/),
          "thumbnail_path": str (== cover_path in v1.0),
          "overall_score": float,
          "lighting": int, "composition": int, "subject_clarity": int,
          "visual_appeal": int, "text_safe_zone": int,
          "main_subject_bbox": dict | None,
          "best_for": str, "reason": str,
        }

    Args:
        ctx: Per-task config + paths.
        vlm_client: ``MediaPostVlmClient``-shaped object with
            ``call_vlm_concurrent``. Tests pass a mock.
        progress_cb: Optional ``(progress_0_to_1, label) -> None`` for
            UI streaming; called between major steps.

    Raises:
        MediaPostError: ``dependency`` if ffmpeg fails; ``format`` if the
            VLM never returns any usable scores.
    """
    out_dir = ctx.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    if progress_cb:
        await _safe_call(progress_cb, 0.05, "extracting candidates")

    cand_files = await _extract_candidates(
        ctx.input_video,
        out_dir / "candidates",
        thumbnail_window=ctx.thumbnail_window,
        scale=ctx.candidate_scale,
        max_frames=ctx.candidate_count,
    )
    if not cand_files:
        raise MediaPostError("dependency", "ffmpeg produced 0 candidate frames")

    if progress_cb:
        await _safe_call(progress_cb, 0.30, "scoring candidates")

    detections = await _score_candidates(
        cand_files,
        vlm_client,
        platform_hint=ctx.platform_hint,
        batch_size=ctx.vlm_batch_size,
        concurrency=ctx.vlm_concurrency,
    )

    if progress_cb:
        await _safe_call(progress_cb, 0.75, "ranking and copying")

    ranked = _rank_and_filter(
        cand_files,
        detections,
        min_score_threshold=ctx.min_score_threshold,
        quantity=ctx.quantity,
    )

    if not ranked:
        raise MediaPostError(
            "format",
            "All candidates failed VLM scoring or were below the threshold",
        )

    final_dir = out_dir / "final"
    final_dir.mkdir(exist_ok=True)
    final_rows = _copy_finalists(ranked, final_dir)

    if progress_cb:
        await _safe_call(progress_cb, 1.0, "done")

    return final_rows


# ---------------------------------------------------------------------------
# Step 1 — ffmpeg thumbnail filter (cheap startup prefilter)
# ---------------------------------------------------------------------------


async def _extract_candidates(
    video: Path,
    out_dir: Path,
    *,
    thumbnail_window: int,
    scale: str,
    max_frames: int,
) -> list[Path]:
    """Run ``ffmpeg -vf thumbnail=N,scale=... -frames:v M`` and return the PNGs."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-i",
        str(video),
        "-vf",
        f"thumbnail={thumbnail_window},scale={scale}",
        "-frames:v",
        str(max_frames),
        str(out_dir / "cand_%02d.png"),
proc = await asyncio.create_subprocess_# FIX: 移除exec，改用安全方式
# 
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
            f"ffmpeg thumbnail filter failed (rc={proc.returncode}): {tail}",
        )
    files = sorted(out_dir.glob("cand_*.png"))
    return files


# ---------------------------------------------------------------------------
# Step 2 — VLM scoring (delegated to mediapost_vlm_client.call_vlm_concurrent)
# ---------------------------------------------------------------------------


async def _score_candidates(
    cand_files: list[Path],
    vlm_client: Any,
    *,
    platform_hint: str,
    batch_size: int,
    concurrency: int,
) -> list[dict[str, Any] | None]:
    """Base64-encode and dispatch to VLM. Returns a list aligned with cand_files."""
    frames_b64 = [base64.b64encode(p.read_bytes()).decode("ascii") for p in cand_files]
    indices = list(range(len(cand_files)))

    def _kwargs_factory(batch_indices: list[int]) -> dict[str, Any]:
        return {
            "frame_count": len(batch_indices),
            "frame_indices": batch_indices,
            "platform": platform_hint,
        }

    detections = await vlm_client.call_vlm_concurrent(
        frames_b64,
        indices,
        COVER_PICK_PROMPT,
        _kwargs_factory,
        batch_size=batch_size,
        concurrency=concurrency,
    )
    return list(detections)


# ---------------------------------------------------------------------------
# Step 3 — rank + threshold filter
# ---------------------------------------------------------------------------


def _rank_and_filter(
    cand_files: list[Path],
    detections: list[dict[str, Any] | None],
    *,
    min_score_threshold: float,
    quantity: int,
) -> list[tuple[Path, dict[str, Any]]]:
    """Pair files with VLM rows, drop empties / below-threshold, sort by score."""
    paired: list[tuple[Path, dict[str, Any]]] = []
    for path, det in zip(cand_files, detections, strict=False):
        if det is None or not isinstance(det, dict):
            continue
        score = _safe_float(det.get("overall_score"), default=0.0)
        if score < min_score_threshold:
            continue
        paired.append((path, det))
    paired.sort(key=lambda pd: -_safe_float(pd[1].get("overall_score"), 0.0))
    return paired[: max(0, int(quantity))]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Step 4 — copy finalists into final/ + return clean rows for DB insert
# ---------------------------------------------------------------------------


def _copy_finalists(
    ranked: list[tuple[Path, dict[str, Any]]],
    final_dir: Path,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for rank, (src, det) in enumerate(ranked, start=1):
        dst = final_dir / f"cover_{rank:02d}.png"
        shutil.copy(src, dst)
        rows.append(_normalize_row(rank, dst, det))
    return rows


def _normalize_row(rank: int, dst: Path, det: dict[str, Any]) -> dict[str, Any]:
    """Coerce VLM output into a stable shape for `insert_cover_result`."""
    bbox = det.get("main_subject_bbox")
    if bbox is not None and not isinstance(bbox, dict):
        bbox = None
    return {
        "rank": rank,
        "cover_path": str(dst),
        "thumbnail_path": str(dst),
        "overall_score": _safe_float(det.get("overall_score")),
        "lighting": _safe_int(det.get("lighting")),
        "composition": _safe_int(det.get("composition")),
        "subject_clarity": _safe_int(det.get("subject_clarity")),
        "visual_appeal": _safe_int(det.get("visual_appeal")),
        "text_safe_zone": _safe_int(det.get("text_safe_zone")),
        "main_subject_bbox": bbox,
        "best_for": str(det.get("best_for") or "thumbnail"),
        "reason": str(det.get("reason") or ""),
    }


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


async def _safe_call(cb: Callable[..., Any], *args: Any) -> None:
    """Call sync or async callback, suppressing all exceptions."""
    try:
        result = cb(*args)
        if asyncio.iscoroutine(result):
            await result
    except Exception:
        logger.debug("progress_cb raised", exc_info=True)


__all__ = [
    "COVER_PICK_PROMPT",
    "DEFAULT_CANDIDATE_COUNT",
    "DEFAULT_CANDIDATE_SCALE",
    "DEFAULT_THUMBNAIL_WINDOW",
    "CoverPickContext",
    "pick_covers",
]
