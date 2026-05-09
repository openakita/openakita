# ruff: noqa: N999
"""cut_qc — original automation of video-use SKILL.md L84-93 manual checklist.

video-use ships a ``SKILL.md`` describing a *human* QC checklist (look at
the boundary frames, listen for an audio spike, eyeball the subtitle
position). footage-gate turns that checklist into 4 deterministic
checkers + 5 belt-and-suspenders defenses against issues that have bit
the upstream Remotion compositor and the LLM-generated EDLs feeding it:

| Check                       | Defense                                  |
| --------------------------- | ---------------------------------------- |
| boundary_frame_check        | Visual jitter at the cut point           |
| waveform_spike_check        | Audio click / pop at the cut             |
| subtitle_overlay_check      | Subtitle hidden by phone-UI safe zone +  |
|                             | filter-graph ordering                    |
| duration_check              | EDL total vs. rendered MP4 mismatch      |
|                                                                       |
| parse_edl                   | EDL field-name normalisation (Issue #43) |
| preprocess_image_cuts       | Image cuts → mp4 loops (Issue #42)       |
| run_qc_with_remux           | Bounded auto-remux loop with HDR-safe    |
|                             | tonemap (PR #6) and MarginV bump (PR #5) |

The module is **fully testable without ffmpeg**: every checker accepts an
``extract_frames`` / ``compute_envelope`` callable so test code can pass
a numpy stub. Production wiring threads
:func:`footage_gate_ffmpeg.extract_frames` /
:func:`footage_gate_ffmpeg.compute_envelope` through.
"""

from __future__ import annotations

import json
import logging
import math
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from footage_gate_ffmpeg import (
    FFmpegError,
    compute_envelope,
    extract_frames,
    ffprobe_json,
    is_hdr_source,
    run_ffmpeg,
)
from footage_gate_grade import prepare_filter_chain
from footage_gate_models import MIN_SUBTITLE_MARGINV_VERTICAL

logger = logging.getLogger(__name__)


# ── Issue model ──────────────────────────────────────────────────────────


_KNOWN_KINDS = frozenset(
    {
        "bad_cut_visual",
        "bad_cut_audio_spike",
        "subtitle_in_safe_zone",
        "subtitle_overlay_order",
        "duration_mismatch",
        "edl_field_normalized",
        "image_cut_preprocessed",
    }
)


@dataclass
class Issue:
    """Single QC finding. ``severity`` ∈ {info, warning, error}."""

    kind: str
    severity: str
    message: str
    cut_index: int | None = None
    timestamp: float | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "message": self.message,
            "cut_index": self.cut_index,
            "timestamp": self.timestamp,
            **({"payload": self.payload} if self.payload else {}),
        }


@dataclass
class NormalizedEdl:
    """Normalised EDL form consumed by every QC stage.

    All cut times are stored under ``in_seconds`` / ``out_seconds`` even
    when the source EDL used the ``start_seconds`` / ``end_seconds``
    naming. ``total_duration_s`` is computed when missing so
    :func:`duration_check` always has a target.
    """

    cuts: list[dict[str, Any]]
    subtitles: list[dict[str, Any]]
    overlays: list[dict[str, Any]]
    output_resolution: tuple[int, int]
    total_duration_s: float
    field_naming: str  # "standard" | "legacy"
    raw: dict[str, Any]


# ── EDL parsing — belt-and-suspenders (Issue #43) ────────────────────────


def parse_edl(payload: dict[str, Any] | str | bytes) -> NormalizedEdl:
    """Accept the EDL in either field-naming and normalise to ``in/out_seconds``.

    OpenMontage Issue #43 noted LLM-authored EDLs frequently use
    ``start_seconds`` / ``end_seconds`` instead of the Remotion contract
    (``in_seconds`` / ``out_seconds``). Multiplying ``undefined * fps``
    produces NaN and crashes the renderer. We accept either spelling
    silently and surface the rewrite as an info-level :class:`Issue`
    elsewhere (see :func:`run_qc_with_remux`).
    """
    edl = json.loads(payload) if isinstance(payload, (str, bytes)) else dict(payload)

    raw_cuts = edl.get("cuts") or []
    naming = "standard"
    cuts: list[dict[str, Any]] = []
    for raw in raw_cuts:
        cut = dict(raw or {})
        if "in_seconds" not in cut and "start_seconds" in cut:
            cut["in_seconds"] = float(cut["start_seconds"])
            naming = "legacy"
        if "out_seconds" not in cut and "end_seconds" in cut:
            cut["out_seconds"] = float(cut["end_seconds"])
            naming = "legacy"
        cut["in_seconds"] = float(cut.get("in_seconds", 0.0) or 0.0)
        cut["out_seconds"] = float(cut.get("out_seconds", 0.0) or 0.0)
        if "source" not in cut:
            cut["source"] = {}
        cuts.append(cut)

    subtitles = list(edl.get("subtitles") or [])
    overlays = list(edl.get("overlays") or [])
    res = edl.get("output_resolution") or [1920, 1080]
    if isinstance(res, dict):
        res_t = (int(res.get("width", 1920)), int(res.get("height", 1080)))
    else:
        res_t = (int(res[0]), int(res[1]))

    total = float(edl.get("total_duration_s") or edl.get("total_duration") or 0.0)
    if not total:
        total = sum(max(0.0, c["out_seconds"] - c["in_seconds"]) for c in cuts)

    return NormalizedEdl(
        cuts=cuts,
        subtitles=subtitles,
        overlays=overlays,
        output_resolution=res_t,
        total_duration_s=round(total, 3),
        field_naming=naming,
        raw=edl,
    )


# ── Image cut preprocessing — Issue #42 defense ──────────────────────────


def preprocess_image_cuts(
    edl: NormalizedEdl,
    *,
    work_dir: Path,
    fps: int = 30,
    ffmpeg_path: str | None = None,
    timeout_sec: float = 60.0,
) -> tuple[NormalizedEdl, list[Issue]]:
    """Convert any image-source cut to a tiny mp4 loop and rewrite the EDL.

    OpenMontage Issue #42 reported that mixing ``mp4`` and ``png`` /
    ``jpg`` cuts crashes the Rust frame_cache (``Option::unwrap on
    None``). We sidestep by re-encoding image sources up front. The
    rewritten EDL keeps the original metadata under
    ``cut.source.original_image_path`` so consumers can audit.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    info: list[Issue] = []
    width, height = edl.output_resolution

    for idx, cut in enumerate(edl.cuts):
        source = cut.get("source") or {}
        media_type = (source.get("media_type") or "").lower()
        path_str = source.get("path") or source.get("uri") or ""
        if media_type != "image" and not _looks_like_image(path_str):
            continue
        if not path_str:
            continue

        duration = max(0.1, cut["out_seconds"] - cut["in_seconds"])
        out = work_dir / f"image_cut_{idx:04d}.mp4"
        args: list[str] = [
            "-y",
            "-loop",
            "1",
            "-i",
            str(path_str),
            "-t",
            f"{duration:.3f}",
            "-r",
            str(int(fps)),
            "-s",
            f"{int(width)}x{int(height)}",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-pix_fmt",
            "yuv420p",
            "-tune",
            "stillimage",
            str(out),
        ]
        try:
            run_ffmpeg(args, timeout_sec=timeout_sec, ffmpeg_path=ffmpeg_path)
        except FFmpegError as exc:
            logger.warning("preprocess_image_cuts failed for %s: %s", path_str, exc)
            continue

        cut["source"] = {
            "media_type": "video",
            "path": str(out),
            "original_image_path": path_str,
        }
        info.append(
            Issue(
                kind="image_cut_preprocessed",
                severity="info",
                message=(
                    f"image cut at index {idx} converted to {out.name} "
                    f"(loop, {fps} fps, {width}x{height})"
                ),
                cut_index=idx,
                payload={
                    "original_path": path_str,
                    "rewritten_path": str(out),
                },
            )
        )
    return edl, info


def _looks_like_image(path: str) -> bool:
    return path.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"))


# ── Type aliases for injectable IO (keeps the checkers ffmpeg-free) ──────


ExtractFramesFn = Callable[..., list[Path]]
ComputeEnvelopeFn = Callable[..., "np.ndarray"]


# ── Checker #1 — boundary frame jitter ───────────────────────────────────


def boundary_frame_check(
    video: Path,
    cuts: Sequence[dict[str, Any]],
    *,
    threshold: float = 30.0,
    window_sec: float = 1.5,
    samples: int = 10,
    work_dir: Path | None = None,
    ffmpeg_path: str | None = None,
    extract_frames_fn: ExtractFramesFn = extract_frames,
) -> list[Issue]:
    """Sample N frames around each cut boundary; flag if mean abs-diff > threshold.

    The mean absolute pixel diff is computed in a downscaled grayscale
    space (160 px wide) so a 4 K source still lands in milliseconds. The
    threshold of 30 (out of 255) corresponds to a *visible* jump rather
    than codec dithering — calibrated against the video-use sample set.
    """
    issues: list[Issue] = []
    if not cuts:
        return issues

    try:
        from PIL import Image  # type: ignore[import-not-found]
    except ImportError:
        logger.warning("PIL not installed — skipping boundary_frame_check")
        return issues

    base_work = work_dir or video.parent / "_qc_frames"
    base_work.mkdir(parents=True, exist_ok=True)

    for idx, cut in enumerate(cuts[1:], start=1):
        T = float(cut.get("in_seconds", 0.0) or 0.0)
        if T <= 0:
            continue
        ts = np.linspace(
            max(0.0, T - window_sec),
            T + window_sec,
            num=int(samples),
        ).tolist()
        cut_dir = base_work / f"cut_{idx:04d}"
        try:
            frames = extract_frames_fn(
                video,
                timestamps=ts,
                dest_dir=cut_dir,
                width=160,
                ffmpeg_path=ffmpeg_path,
            )
        except FFmpegError as exc:
            logger.warning("boundary_frame_check ffmpeg failure: %s", exc)
            continue

        if len(frames) < 2:
            continue

        arrays: list[np.ndarray] = []
        for f in frames:
            try:
                with Image.open(f) as img:
                    arrays.append(np.asarray(img.convert("L"), dtype=np.float32))
            except Exception as exc:  # noqa: BLE001
                logger.debug("PIL open failed: %s", exc)

        if len(arrays) < 2:
            continue
        diffs = [float(np.abs(arrays[i] - arrays[i - 1]).mean()) for i in range(1, len(arrays))]
        peak = max(diffs)
        if peak > threshold:
            issues.append(
                Issue(
                    kind="bad_cut_visual",
                    severity="warning",
                    message=(f"cut {idx}: peak frame diff {peak:.1f} > threshold {threshold:.1f}"),
                    cut_index=idx,
                    timestamp=T,
                    payload={"peak_diff": round(peak, 2)},
                )
            )
    return issues


# ── Checker #2 — audio waveform spike ────────────────────────────────────


def waveform_spike_check(
    video: Path,
    cuts: Sequence[dict[str, Any]],
    *,
    threshold: float = 0.85,
    window_sec: float = 0.5,
    envelope_samples: int = 4000,
    ffmpeg_path: str | None = None,
    compute_envelope_fn: ComputeEnvelopeFn = compute_envelope,
) -> list[Issue]:
    """Flag cut boundaries where the normalised RMS envelope exceeds threshold.

    A single envelope is computed for the whole input (one ffmpeg call)
    and indexed per cut to keep the cost predictable on long inputs.
    """
    issues: list[Issue] = []
    if not cuts:
        return issues
    try:
        envelope = compute_envelope_fn(
            video,
            samples=envelope_samples,
            ffmpeg_path=ffmpeg_path,
        )
    except FFmpegError as exc:
        logger.warning("waveform_spike_check envelope failure: %s", exc)
        return issues
    if envelope.size == 0:
        return issues

    try:
        probe = ffprobe_json(video)
        duration = float(probe.get("format", {}).get("duration", 0) or 0)
    except (FFmpegError, ValueError):
        duration = 0.0
    if duration <= 0:
        return issues

    sec_per_bucket = duration / float(envelope.size)
    half = max(1, int(round(window_sec / sec_per_bucket)))

    for idx, cut in enumerate(cuts[1:], start=1):
        T = float(cut.get("in_seconds", 0.0) or 0.0)
        if T <= 0:
            continue
        center = int(round(T / sec_per_bucket))
        lo = max(0, center - half)
        hi = min(int(envelope.size), center + half + 1)
        if lo >= hi:
            continue
        window_peak = float(envelope[lo:hi].max())
        if window_peak > threshold:
            issues.append(
                Issue(
                    kind="bad_cut_audio_spike",
                    severity="warning",
                    message=(
                        f"cut {idx}: audio peak {window_peak:.2f} > "
                        f"threshold {threshold:.2f} within ±{window_sec:.1f}s"
                    ),
                    cut_index=idx,
                    timestamp=T,
                    payload={"peak_rms": round(window_peak, 3)},
                )
            )
    return issues


# ── Checker #3 — subtitle overlay safety + ordering ──────────────────────


def subtitle_overlay_check(
    edl: NormalizedEdl,
    *,
    min_marginv_vertical: int = MIN_SUBTITLE_MARGINV_VERTICAL,
) -> list[Issue]:
    """Ensure subtitles are above the platform safe zone on vertical output.

    PR #5 in video-use noted ``MarginV=35`` lands inside the TikTok / IG
    Reels / YouTube Shorts bottom UI strip on 9:16 outputs. We require
    ``MarginV >= 90`` for any output whose aspect ratio is portrait or
    squarer than 9:16. Landscape outputs are not affected.

    The "filter-graph ordering" check (subtitles must come AFTER overlays
    in the filter chain) is also enforced when the EDL ships an explicit
    ``filter_chain`` array of strings — the test matrix uses this to
    prove we did not regress the upstream contract.
    """
    width, height = edl.output_resolution
    issues: list[Issue] = []

    # Vertical safe zone — Issue #2 defense.
    if height >= width:  # portrait or square
        for s in edl.subtitles:
            margin = int(s.get("MarginV", s.get("margin_v", 0)) or 0)
            if margin < min_marginv_vertical:
                issues.append(
                    Issue(
                        kind="subtitle_in_safe_zone",
                        severity="warning",
                        message=(
                            "subtitle MarginV "
                            f"{margin} < {min_marginv_vertical} on vertical "
                            f"output {width}x{height}"
                        ),
                        payload={
                            "subtitle_id": s.get("id"),
                            "margin_v": margin,
                            "required_min": min_marginv_vertical,
                        },
                    )
                )

    # Filter-chain ordering — only check when explicitly provided.
    chain = edl.raw.get("filter_chain")
    if isinstance(chain, list) and chain:
        order = [str(step).lower() for step in chain]
        first_subs = next((i for i, s in enumerate(order) if "subtitles" in s), -1)
        last_overlay = -1
        for i, step in enumerate(order):
            if "overlay" in step:
                last_overlay = i
        if first_subs != -1 and last_overlay > first_subs:
            issues.append(
                Issue(
                    kind="subtitle_overlay_order",
                    severity="error",
                    message=(
                        "filter_chain orders 'overlay' after 'subtitles'; subtitles will be hidden"
                    ),
                    payload={
                        "first_subtitles_index": first_subs,
                        "last_overlay_index": last_overlay,
                        "chain": chain,
                    },
                )
            )
    return issues


# ── Checker #4 — duration mismatch ───────────────────────────────────────


def duration_check(
    video: Path,
    edl: NormalizedEdl,
    *,
    tolerance_sec: float = 0.5,
    ffprobe_path: str | None = None,
) -> list[Issue]:
    """Compare the rendered video duration to the EDL total."""
    issues: list[Issue] = []
    try:
        probe = ffprobe_json(video, ffprobe_path=ffprobe_path)
    except FFmpegError as exc:
        logger.warning("duration_check ffprobe failed: %s", exc)
        return issues
    actual = float(probe.get("format", {}).get("duration", 0) or 0)
    expected = edl.total_duration_s
    if expected <= 0:
        return issues
    diff = abs(actual - expected)
    if diff > tolerance_sec:
        issues.append(
            Issue(
                kind="duration_mismatch",
                severity="warning",
                message=(
                    f"video duration {actual:.2f}s differs from EDL "
                    f"total {expected:.2f}s by {diff:.2f}s"
                ),
                payload={
                    "actual": round(actual, 3),
                    "expected": round(expected, 3),
                    "tolerance": tolerance_sec,
                },
            )
        )
    return issues


# ── Bundled checker run ──────────────────────────────────────────────────


@dataclass
class QcResult:
    """Output of :func:`run_qc_with_remux`."""

    issues: list[Issue]
    attempts: int
    final_video: Path
    grid_path: Path | None
    edl_used: NormalizedEdl
    naming_normalized: bool


def run_all_checks(
    video: Path,
    edl: NormalizedEdl,
    *,
    ffmpeg_path: str | None = None,
    ffprobe_path: str | None = None,
    work_dir: Path | None = None,
) -> list[Issue]:
    """Run all four checkers in order. Used by both single-pass QC and
    the remux loop."""
    issues: list[Issue] = []
    issues.extend(
        boundary_frame_check(
            video,
            edl.cuts,
            ffmpeg_path=ffmpeg_path,
            work_dir=work_dir,
        )
    )
    issues.extend(waveform_spike_check(video, edl.cuts, ffmpeg_path=ffmpeg_path))
    issues.extend(subtitle_overlay_check(edl))
    issues.extend(duration_check(video, edl, ffprobe_path=ffprobe_path))
    return issues


# ── Auto-remux loop ──────────────────────────────────────────────────────


def _has_blocking_issues(issues: Sequence[Issue]) -> bool:
    return any(i.severity in ("warning", "error") for i in issues)


def _apply_fix_strategies(
    edl: NormalizedEdl,
    issues: Sequence[Issue],
    *,
    min_marginv_vertical: int = MIN_SUBTITLE_MARGINV_VERTICAL,
    boundary_nudge_sec: float = 0.1,
) -> NormalizedEdl:
    """Apply bounded fixes for each known issue kind.

    The fixes are intentionally tiny so a misclassified false-positive
    cannot make the output worse: ±0.1 s on cut boundaries, MarginV
    floor at the platform minimum, no subtitle text rewrites.
    """
    for issue in issues:
        if (
            issue.kind == "bad_cut_visual"
            and issue.cut_index is not None
            or issue.kind == "bad_cut_audio_spike"
            and issue.cut_index is not None
        ):
            cut = edl.cuts[issue.cut_index]
            cut["in_seconds"] = max(0.0, cut["in_seconds"] + boundary_nudge_sec)
        elif issue.kind == "subtitle_in_safe_zone":
            for s in edl.subtitles:
                margin = int(s.get("MarginV", s.get("margin_v", 0)) or 0)
                if margin < min_marginv_vertical:
                    s["MarginV"] = min_marginv_vertical
    return edl


def remux_from_edl(
    edl: NormalizedEdl,
    output: Path,
    *,
    ffmpeg_path: str | None = None,
    ffprobe_path: str | None = None,
    timeout_sec: float = 600.0,
) -> Path:
    """Concat each cut's segment and write the result to ``output``.

    Pure ``ffmpeg -f concat`` based — no Remotion / NLE bridge. Keeps
    the v1.0 surface tiny and the remux loop deterministic. HDR-aware:
    when the FIRST source clip is HDR we re-encode through
    ``TONEMAP_CHAIN`` (PR #6 fix) so the rendered output lands in BT.709
    SDR for downstream tools.
    """
    output.parent.mkdir(parents=True, exist_ok=True)

    if not edl.cuts:
        raise ValueError("EDL has no cuts to remux")

    # HDR detection on the first source — best-effort, fail-open.
    first_path = (edl.cuts[0].get("source") or {}).get("path")
    hdr = bool(first_path and is_hdr_source(Path(first_path), ffprobe_path=ffprobe_path))

    work_dir = output.parent / f"_remux_{output.stem}"
    work_dir.mkdir(parents=True, exist_ok=True)
    seg_paths: list[Path] = []
    for idx, cut in enumerate(edl.cuts):
        src = (cut.get("source") or {}).get("path")
        if not src:
            continue
        seg = work_dir / f"seg_{idx:04d}.mp4"
        in_t = float(cut["in_seconds"])
        out_t = float(cut["out_seconds"])
        dur = max(0.05, out_t - in_t)
        chain = prepare_filter_chain("", hdr_source=hdr)
        args: list[str] = [
            "-y",
            "-ss",
            f"{max(0.0, in_t):.3f}",
            "-i",
            str(src),
            "-t",
            f"{dur:.3f}",
        ]
        if chain:
            args.extend(["-vf", chain])
        args.extend(
            [
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                str(seg),
            ]
        )
        run_ffmpeg(args, timeout_sec=timeout_sec, ffmpeg_path=ffmpeg_path)
        seg_paths.append(seg)

    if not seg_paths:
        raise ValueError("EDL produced no remuxable segments")

    list_file = work_dir / "concat.txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for seg in seg_paths:
            f.write(f"file '{seg.as_posix()}'\n")

    run_ffmpeg(
        [
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(output),
        ],
        timeout_sec=timeout_sec,
        ffmpeg_path=ffmpeg_path,
    )
    return output


def run_qc_with_remux(
    video: Path,
    edl_payload: dict[str, Any] | str | bytes,
    *,
    work_dir: Path,
    auto_remux: bool = False,
    max_attempts: int = 3,
    ffmpeg_path: str | None = None,
    ffprobe_path: str | None = None,
    remux_fn: Callable[..., Path] = remux_from_edl,
) -> QcResult:
    """Top-level cut_qc entry point.

    1. Parse + normalise the EDL (Issue #43 defense).
    2. Preprocess image cuts to mp4 loops (Issue #42 defense).
    3. Run all four checkers on ``video``.
    4. If ``auto_remux`` is enabled and there are warnings/errors, apply
       bounded fixes and re-render up to ``max_attempts`` times.
    5. Render ``qc_grid.png`` snapshot of the final cut boundaries.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    edl = parse_edl(edl_payload)

    bookkeeping: list[Issue] = []
    if edl.field_naming == "legacy":
        bookkeeping.append(
            Issue(
                kind="edl_field_normalized",
                severity="info",
                message=(
                    "EDL used start_seconds/end_seconds — normalised to "
                    "in_seconds/out_seconds. Please update upstream LLM "
                    "prompts to emit the standard names."
                ),
            )
        )

    edl, image_info = preprocess_image_cuts(
        edl,
        work_dir=work_dir / "image_cuts",
        ffmpeg_path=ffmpeg_path,
    )
    bookkeeping.extend(image_info)

    current_video = video
    attempts = 0
    issues = run_all_checks(
        current_video,
        edl,
        ffmpeg_path=ffmpeg_path,
        ffprobe_path=ffprobe_path,
        work_dir=work_dir / "frames",
    )

    while auto_remux and attempts < max(1, int(max_attempts)) and _has_blocking_issues(issues):
        attempts += 1
        edl = _apply_fix_strategies(edl, issues)
        next_path = work_dir / f"output_remuxed_attempt_{attempts}.mp4"
        try:
            current_video = remux_fn(
                edl,
                next_path,
                ffmpeg_path=ffmpeg_path,
                ffprobe_path=ffprobe_path,
            )
        except (FFmpegError, ValueError) as exc:
            logger.warning("auto-remux attempt %d aborted: %s", attempts, exc)
            break
        issues = run_all_checks(
            current_video,
            edl,
            ffmpeg_path=ffmpeg_path,
            ffprobe_path=ffprobe_path,
            work_dir=work_dir / f"frames_attempt_{attempts}",
        )

    grid_path = render_qc_grid(
        current_video,
        edl,
        work_dir / "qc_grid.png",
        ffmpeg_path=ffmpeg_path,
    )

    return QcResult(
        issues=bookkeeping + issues,
        attempts=attempts,
        final_video=current_video,
        grid_path=grid_path,
        edl_used=edl,
        naming_normalized=(edl.field_naming == "legacy"),
    )


# ── qc_grid.png renderer ─────────────────────────────────────────────────


def render_qc_grid(
    video: Path,
    edl: NormalizedEdl,
    dest_path: Path,
    *,
    ffmpeg_path: str | None = None,
    extract_frames_fn: ExtractFramesFn = extract_frames,
    max_cells: int = 9,
) -> Path | None:
    """Build a 3×3 (default) PNG mosaic of the cut-boundary frames.

    Returns the destination path on success, ``None`` when the renderer
    cannot run (PIL missing or ffmpeg fails for every cut). The mosaic
    is intentionally tiny (each thumbnail 320×180) so the QC report
    embed stays under a few hundred KB.
    """
    try:
        from PIL import Image  # type: ignore[import-not-found]
    except ImportError:
        logger.warning("PIL not installed — skipping qc_grid render")
        return None

    cuts = edl.cuts[: max(1, max_cells)]
    timestamps = [float(c.get("in_seconds", 0.0) or 0.0) for c in cuts if c]
    if not timestamps:
        return None

    tmp = dest_path.parent / "_qc_grid_frames"
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        frames = extract_frames_fn(
            video,
            timestamps=timestamps,
            dest_dir=tmp,
            width=320,
            ffmpeg_path=ffmpeg_path,
        )
    except FFmpegError as exc:
        logger.warning("qc_grid frame extraction failed: %s", exc)
        return None

    if not frames:
        return None

    cols = int(math.ceil(math.sqrt(len(frames))))
    rows = int(math.ceil(len(frames) / cols))
    cell_w, cell_h = 320, 180
    grid = Image.new("RGB", (cols * cell_w, rows * cell_h), color=(0, 0, 0))
    for idx, fpath in enumerate(frames):
        try:
            with Image.open(fpath) as img:
                thumb = img.convert("RGB").resize((cell_w, cell_h), Image.Resampling.LANCZOS)
                grid.paste(thumb, ((idx % cols) * cell_w, (idx // cols) * cell_h))
        except Exception as exc:  # noqa: BLE001
            logger.debug("qc_grid paste failed for %s: %s", fpath, exc)

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    grid.save(dest_path, format="PNG", optimize=True)
    shutil.rmtree(tmp, ignore_errors=True)
    return dest_path


__all__ = [
    "Issue",
    "NormalizedEdl",
    "QcResult",
    "boundary_frame_check",
    "duration_check",
    "parse_edl",
    "preprocess_image_cuts",
    "remux_from_edl",
    "render_qc_grid",
    "run_all_checks",
    "run_qc_with_remux",
    "subtitle_overlay_check",
    "waveform_spike_check",
]
