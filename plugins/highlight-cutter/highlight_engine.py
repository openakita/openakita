"""highlight-cutter — core engine.

Three-stage pipeline (audit3 三分自检 — three-thirds self check applied):

1. **transcribe** — turn audio into ``[{start, end, text}]`` chunks
2. **score & pick** — score each chunk for "highlight-worthiness", pick K
   candidates evenly distributed across the timeline (avoid clustering at
   the start)
3. **render** — feed the picked segments into ``RenderPipeline`` and run
   ffmpeg via the concat demuxer

This file deliberately keeps each stage replaceable via dependency
injection — ``transcribe_fn``, ``score_fn``, ``render_fn`` — so the same
engine drives the pytest suite, the live plugin, and the subtitle-maker /
video-translator plugins downstream (P2).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from openakita_plugin_sdk.contrib import build_render_pipeline

logger = logging.getLogger(__name__)


# ── data types ──────────────────────────────────────────────────────────


@dataclass
class TranscriptChunk:
    """One ASR chunk (sentence-ish granularity)."""

    start: float
    end: float
    text: str
    confidence: float = 1.0

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class HighlightSegment:
    """A picked candidate for the final cut."""

    start: float
    end: float
    score: float
    reason: str
    text: str = ""
    label: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── transcription (vendor-agnostic) ─────────────────────────────────────


TranscribeFn = Callable[[Path], Awaitable[list[TranscriptChunk]]]
ScoreFn      = Callable[[list[TranscriptChunk]], list[tuple[TranscriptChunk, float, str]]]


async def whisper_cpp_transcribe(
    source: Path,
    *,
    model: str = "base",
    language: str = "auto",
    binary: str = "whisper-cli",
    timeout_sec: float = 600.0,
) -> list[TranscriptChunk]:
    """Default transcription: shells out to ``whisper-cli`` (whisper.cpp).

    Returns an empty list on any failure (caller falls back to silence-based
    chunking if no transcript is available).
    """
    bin_path = shutil.which(binary)
    if not bin_path:
        logger.warning("whisper.cpp binary '%s' not found in PATH; transcription skipped", binary)
        return []

    out_json = source.with_suffix(".whisper.json")
    cmd = [
        bin_path,
        "-m", model,
        "-l", language,
        "--output-json",
        "--output-file", str(out_json.with_suffix("")),
        str(source),
    ]

    def _run() -> list[TranscriptChunk]:
        try:
            subprocess.run(cmd, timeout=timeout_sec, check=True, capture_output=True)
        except subprocess.SubprocessError as e:
            logger.warning("whisper.cpp failed: %s", e)
            return []
        try:
            data = json.loads(out_json.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            logger.warning("Cannot read whisper json output: %s", e)
            return []
        chunks = []
        for seg in data.get("transcription", []):
            try:
                chunks.append(TranscriptChunk(
                    start=float(seg.get("offsets", {}).get("from", 0)) / 1000.0,
                    end=float(seg.get("offsets", {}).get("to", 0)) / 1000.0,
                    text=str(seg.get("text", "")).strip(),
                    confidence=1.0,
                ))
            except (TypeError, ValueError):
                continue
        return chunks

    return await asyncio.to_thread(_run)


# ── scoring & picking ──────────────────────────────────────────────────


_HIGHLIGHT_KEYWORDS = (
    # Reactions / climax
    "wow", "哇", "天啊", "厉害", "amazing", "incredible", "震惊",
    # Conclusions / takeaways
    "所以", "总之", "结论", "记住", "重点", "key", "summary", "note that",
    # Quotables
    "我觉得", "其实", "actually", "the truth is",
    # Decisions
    "决定", "we will", "let's",
)
_PUNCTUATION_END = ("。", "！", "？", "!", "?", ".")


def keyword_score(chunks: list[TranscriptChunk]) -> list[tuple[TranscriptChunk, float, str]]:
    """Naive but explainable scorer — keyword density + sentence completeness.

    Returns ``[(chunk, score, reason)]``.  Replace with a learned model later;
    the contract stays the same so it's swappable.
    """
    out: list[tuple[TranscriptChunk, float, str]] = []
    for c in chunks:
        text = c.text.strip()
        if not text:
            out.append((c, 0.0, "empty text"))
            continue
        score = 0.0
        reasons: list[str] = []

        # Keyword presence
        lower = text.lower()
        kw_hits = sum(1 for k in _HIGHLIGHT_KEYWORDS if k in lower)
        if kw_hits:
            score += min(kw_hits * 0.25, 1.0)
            reasons.append(f"包含 {kw_hits} 个亮点关键词")

        # Sentence completeness
        if any(text.endswith(p) for p in _PUNCTUATION_END):
            score += 0.2
            reasons.append("完整句")

        # Length sweet spot (5-45 chars in Chinese / ~10-90 chars in English)
        L = len(text)
        if 5 <= L <= 90:
            score += 0.3
            reasons.append("长度适中")
        elif L > 90:
            score -= 0.2
            reasons.append("偏长")

        # Repeats (suggestive of a memorable hook)
        words = re.findall(r"\w+", text)
        if words and len(set(words)) / max(1, len(words)) < 0.6:
            score += 0.15
            reasons.append("有复读")

        score = max(0.0, min(score, 1.5))
        out.append((c, score, "; ".join(reasons) or "no notable signal"))
    return out


def pick_segments(
    scored: list[tuple[TranscriptChunk, float, str]],
    *,
    target_count: int = 5,
    min_segment_sec: float = 3.0,
    max_segment_sec: float = 20.0,
    total_duration: float | None = None,
) -> list[HighlightSegment]:
    """Pick K segments balanced across the timeline (audit3 三分自检 enforcement).

    Strategy: split the timeline into ``target_count`` equal "buckets",
    pick the highest-scoring chunk in each bucket whose duration falls in
    ``[min, max]``.  This avoids the LLM tendency to cluster everything at
    the beginning of the video.
    """
    if not scored:
        return []
    target_count = max(1, target_count)
    chunks_only = [c for c, _, _ in scored if c.duration > 0]
    if not chunks_only:
        return []
    if total_duration is None:
        total_duration = max(c.end for c in chunks_only)
    if total_duration <= 0:
        return []

    bucket_width = total_duration / target_count
    picked: list[HighlightSegment] = []
    used_ids: set[int] = set()

    for b in range(target_count):
        lo = b * bucket_width
        hi = (b + 1) * bucket_width
        in_bucket = [
            (i, c, s, r) for i, (c, s, r) in enumerate(scored)
            if id(c) not in used_ids
            and c.start >= lo and c.start < hi
            and min_segment_sec <= c.duration <= max_segment_sec
        ]
        if not in_bucket:
            # Relax the duration constraint
            in_bucket = [
                (i, c, s, r) for i, (c, s, r) in enumerate(scored)
                if id(c) not in used_ids and c.start >= lo and c.start < hi
            ]
        if not in_bucket:
            continue
        in_bucket.sort(key=lambda t: t[2], reverse=True)
        _, c, s, r = in_bucket[0]
        used_ids.add(id(c))
        # Clamp duration if too long
        end = min(c.end, c.start + max_segment_sec)
        picked.append(HighlightSegment(
            start=c.start, end=end, score=s, reason=r, text=c.text,
            label=f"段{b+1}",
        ))

    return picked


# ── rendering ───────────────────────────────────────────────────────────


def render_highlights(
    *,
    source: Path,
    segments: list[HighlightSegment],
    output: Path,
    fps: int = 24,
    width: int | None = None,
    height: int | None = None,
    timeout_sec: float = 600.0,
    ffmpeg: str = "ffmpeg",
) -> Path:
    """Render the picked segments using contrib.RenderPipeline + ffmpeg.

    Two-step process: trim each segment to its own intermediate file, then
    concat them via the demuxer (this is what's reliable across container
    formats — see CutClaw/OpenMontage for war stories).
    """
    if not segments:
        raise ValueError("No segments to render")

    output.parent.mkdir(parents=True, exist_ok=True)
    work_dir = output.parent / f".{output.stem}_parts"
    work_dir.mkdir(parents=True, exist_ok=True)

    parts: list[Path] = []
    for i, seg in enumerate(segments):
        part_path = work_dir / f"part_{i:03d}.mp4"
        pipe = build_render_pipeline(
            segments=[{"source": source, "start": seg.start, "end": seg.end}],
            output=part_path, fps=fps, width=width, height=height,
            timeout_sec=timeout_sec,
        )
        cmd = pipe.to_simple_command(ffmpeg=ffmpeg)
        subprocess.run(cmd, check=True, timeout=timeout_sec, capture_output=True)
        parts.append(part_path)

    # Concat
    concat_pipe = build_render_pipeline(
        segments=[{"source": p, "start": 0, "end": None} for p in parts],
        output=output, fps=fps, width=width, height=height,
        timeout_sec=timeout_sec,
    )
    list_file = work_dir / "_list.txt"
    concat_pipe.write_concat_list(list_file)
    cmd = concat_pipe.to_concat_command(list_file=list_file, ffmpeg=ffmpeg)
    subprocess.run(cmd, check=True, timeout=timeout_sec, capture_output=True)

    # Clean up parts (best-effort)
    for p in parts:
        try: p.unlink()
        except OSError: pass
    try: list_file.unlink()
    except OSError: pass
    try: work_dir.rmdir()
    except OSError: pass

    return output
