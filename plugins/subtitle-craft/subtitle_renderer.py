"""SRT/VTT generation, timeline repair, and burn helpers for subtitle-craft.

Three logical surfaces:

1. **Conversion**: ``words_to_srt_cues``, ``cues_to_srt``, ``cues_to_vtt``,
   ``parse_srt``.
2. **Repair**: ``repair_srt_cues`` — applies the 5 fixes (zero-length, out-of-
   order, overlap, short-cue extension, line wrap) called out in §3 of
   ``docs/subtitle-craft-plan.md`` (P0-12 + P1-7~P1-9).
3. **Burn**: ``burn_subtitles_ass`` (A path; ffmpeg subtitles filter) and
   ``burn_subtitles_html`` (B path; Playwright HTML transparent PNG overlay,
   P0-13 lazy-import + P0-14 singleton).

Red-line guardrails baked in (Phase 2b):

- **No top-level ``import playwright``**. Even ``from playwright.async_api
  import async_playwright`` lives **inside** ``burn_subtitles_html`` and
  ``_PlaywrightSingleton`` methods. Phase 0 grep guard
  (``test_no_handoff_route_literal`` companion) verifies via
  ``rg "^from playwright" subtitle_renderer.py`` — must be 0 hits.
- ``burn_subtitles_html`` always wraps the Playwright work in try/except
  and falls back to ``burn_subtitles_ass`` on **any** exception (P1-13).
- ``_ffmpeg_subtitles_arg`` builds the Windows-safe ``filename=`` keyword
  form per VALIDATION.md §4 (P0-16). Single source of truth — pipeline
  must call this helper, never hand-roll the escape.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from subtitle_asr_client import AsrWord
from subtitle_models import SubtitleStyle

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tunables (all values match docs/subtitle-craft-plan.md §3 & §7.4)
# ---------------------------------------------------------------------------

#: Hard line-length cap (characters; CJK = 1, ASCII = 1).
DEFAULT_MAX_LINE_CHARS: int = 42

#: Wrap a cue into 2 lines once a single line crosses this fraction of cap.
WRAP_AFTER_CHAR_RATIO: float = 0.7

#: Maximum cue duration before splitting (seconds).
DEFAULT_MAX_CUE_DURATION_SEC: float = 6.0

#: Minimum cue duration; shorter cues get extended to this length.
DEFAULT_MIN_CUE_DURATION_SEC: float = 0.5

#: Gap inserted between adjacent cues that overlap (seconds).
DEFAULT_OVERLAP_GAP_SEC: float = 0.04

#: Pause between words that triggers a hard cue boundary (seconds).
DEFAULT_WORD_GAP_SEC: float = 0.6


# ---------------------------------------------------------------------------
# Cue dataclass + serialization
# ---------------------------------------------------------------------------


@dataclass
class SRTCue:
    """One subtitle cue.

    Indices are 1-based to match the SRT file format directly.
    Times in **seconds** (float). Convert with :func:`_format_srt_time` /
    :func:`_format_vtt_time` for serialization.
    """

    index: int
    start: float
    end: float
    text: str
    speaker_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def _format_srt_time(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(int(t), 3600)
    m, s = divmod(rem, 60)
    ms = int(round((t - int(t)) * 1000))
    if ms == 1000:
        s += 1
        ms = 0
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _format_vtt_time(t: float) -> str:
    return _format_srt_time(t).replace(",", ".")


def cues_to_srt(cues: list[SRTCue]) -> str:
    """Serialize cues to SRT (UTF-8, CRLF newlines, 1-based indices)."""
    parts: list[str] = []
    for i, c in enumerate(cues, start=1):
        parts.append(str(i))
        parts.append(f"{_format_srt_time(c.start)} --> {_format_srt_time(c.end)}")
        parts.append(c.text.rstrip("\n"))
        parts.append("")  # blank separator
    return "\r\n".join(parts).rstrip("\r\n") + "\r\n"


def cues_to_vtt(cues: list[SRTCue]) -> str:
    """Serialize cues to WebVTT (UTF-8, LF newlines, no indices)."""
    parts: list[str] = ["WEBVTT", ""]
    for c in cues:
        parts.append(f"{_format_vtt_time(c.start)} --> {_format_vtt_time(c.end)}")
        parts.append(c.text.rstrip("\n"))
        parts.append("")
    return "\n".join(parts).rstrip("\n") + "\n"


_SRT_TIME_RE = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*"
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})"
)


def _parse_time(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000.0


def parse_srt(content: str) -> list[SRTCue]:
    """Parse SRT (or near-SRT) into cues.

    Tolerates Windows / Unix line endings and missing indices. The exact
    ``index`` attribute is always re-assigned 1..N after parsing so that a
    re-serialize round-trips cleanly.
    """
    text = content.replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n\s*\n+", text.strip())
    cues: list[SRTCue] = []
    next_idx = 1
    for block in blocks:
        lines = [line for line in block.splitlines() if line is not None]
        if not lines:
            continue
        time_line_idx = -1
        for i, line in enumerate(lines):
            if _SRT_TIME_RE.search(line):
                time_line_idx = i
                break
        if time_line_idx < 0:
            continue
        m = _SRT_TIME_RE.search(lines[time_line_idx])
        assert m
        start = _parse_time(*m.group(1, 2, 3, 4))
        end = _parse_time(*m.group(5, 6, 7, 8))
        body = "\n".join(lines[time_line_idx + 1 :]).strip()
        if not body:
            continue
        cues.append(SRTCue(index=next_idx, start=start, end=end, text=body))
        next_idx += 1
    return cues


# ---------------------------------------------------------------------------
# words → cues
# ---------------------------------------------------------------------------


def words_to_srt_cues(
    words: list[AsrWord],
    *,
    max_chars: int = DEFAULT_MAX_LINE_CHARS,
    max_duration: float = DEFAULT_MAX_CUE_DURATION_SEC,
    word_gap: float = DEFAULT_WORD_GAP_SEC,
) -> list[SRTCue]:
    """Pack word-level Paraformer output into reasonable cues.

    Heuristics:

    1. End a cue when the running text would exceed ``max_chars`` (CJK char
       width = 1, mirrors clip-sense subtitle convention).
    2. End a cue when its duration would exceed ``max_duration``.
    3. End a cue when the gap between the previous word's ``end_ms`` and the
       next word's ``start_ms`` exceeds ``word_gap``.
    4. Always end a cue at sentence-final punctuation (``。！？.!?``) carried
       in :pyattr:`AsrWord.punctuation`.
    5. Same speaker_id throughout the cue (never mix speakers in one cue).
    """
    if not words:
        return []
    cues: list[SRTCue] = []
    cur_words: list[AsrWord] = []
    cur_start_ms = words[0].start_ms

    def flush() -> None:
        nonlocal cur_words, cur_start_ms
        if not cur_words:
            return
        text = _stitch_words(cur_words)
        cues.append(
            SRTCue(
                index=len(cues) + 1,
                start=cur_words[0].start_ms / 1000.0,
                end=cur_words[-1].end_ms / 1000.0,
                text=text,
                speaker_id=cur_words[0].speaker_id,
            )
        )
        cur_words = []

    for w in words:
        if cur_words:
            prev = cur_words[-1]
            gap_sec = (w.start_ms - prev.end_ms) / 1000.0
            same_speaker = prev.speaker_id == w.speaker_id
            tentative_text = _stitch_words([*cur_words, w])
            tentative_dur = (w.end_ms - cur_start_ms) / 1000.0
            if (
                gap_sec >= word_gap
                or not same_speaker
                or _visible_len(tentative_text) > max_chars * 2
                or tentative_dur > max_duration
            ):
                flush()
        if not cur_words:
            cur_start_ms = w.start_ms
        cur_words.append(w)
        if w.punctuation and any(p in w.punctuation for p in "。！？.!?"):
            flush()
    flush()

    # Now wrap each cue's text into ≤2 lines respecting max_chars.
    for c in cues:
        c.text = _wrap_text(c.text, max_chars=max_chars)
    return cues


def _stitch_words(words: list[AsrWord]) -> str:
    """Join words preserving punctuation; collapse double spaces."""
    out: list[str] = []
    for w in words:
        out.append(w.text)
        if w.punctuation:
            out.append(w.punctuation)
    raw = "".join(out)
    return re.sub(r"[ \t]{2,}", " ", raw).strip()


def _visible_len(text: str) -> int:
    """Char count treating CJK and ASCII as equal width 1."""
    return sum(1 for ch in text if not ch.isspace()) + text.count(" ")


def _wrap_text(text: str, *, max_chars: int) -> str:
    """Wrap into ≤ 2 lines.

    Tries to break on whitespace first; if no whitespace exists (CJK), falls
    back to char-count split (P1-9). Long text beyond 2 lines is left as-is
    here — the cue-level split (``max_chars * 2`` in
    :func:`words_to_srt_cues`) prevents that case in practice.
    """
    text = text.strip()
    if _visible_len(text) <= max_chars:
        return text
    breakpoint_chars = int(max_chars * WRAP_AFTER_CHAR_RATIO)
    if " " in text:
        words = text.split(" ")
        line1: list[str] = []
        running = 0
        for w in words:
            if running + len(w) + 1 > max_chars and line1:
                break
            line1.append(w)
            running += len(w) + 1
        line2 = " ".join(words[len(line1) :])
        return " ".join(line1) + "\n" + line2
    # CJK / no whitespace
    cut = max(breakpoint_chars, len(text) // 2)
    return text[:cut] + "\n" + text[cut:]


# ---------------------------------------------------------------------------
# Repair
# ---------------------------------------------------------------------------


def repair_srt_cues(
    cues: list[SRTCue],
    *,
    min_duration: float = DEFAULT_MIN_CUE_DURATION_SEC,
    max_duration: float = DEFAULT_MAX_CUE_DURATION_SEC,
    overlap_gap: float = DEFAULT_OVERLAP_GAP_SEC,
    max_chars: int = DEFAULT_MAX_LINE_CHARS,
) -> tuple[list[SRTCue], dict[str, int]]:
    """Apply 5 standard subtitle hygiene fixes; returns (repaired, stats).

    Fixes (numbered to match docs §3):

    1. **Zero-length / reversed time** (P0-12): if ``end <= start``,
       set ``end = start + min_duration``.
    2. **Re-order**: sort by ``start`` ascending.
    3. **Overlap trim** (P1-8): if ``cur.end > next.start``, set
       ``cur.end = next.start - overlap_gap`` (clamped ≥ ``cur.start +
       min_duration``).
    4. **Short cue extension** (P1-7): cues with ``duration < min_duration``
       are extended to ``min_duration`` (without crossing the next cue).
    5. **Line wrap** (P1-9): cues exceeding ``max_chars`` get re-wrapped.

    ``stats`` reports per-fix counts so the UI can summarize what was done.
    """
    stats: dict[str, int] = {
        "fixed_zero_length": 0,
        "reordered": 0,
        "trimmed_overlap": 0,
        "extended_short": 0,
        "rewrapped": 0,
    }
    if not cues:
        return [], stats

    # Step 1: zero-length / reversed.
    fixed: list[SRTCue] = []
    for c in cues:
        if c.end <= c.start:
            stats["fixed_zero_length"] += 1
            c = replace(c, end=c.start + min_duration)
        fixed.append(c)

    # Step 2: sort.
    sort_check = list(fixed)
    fixed.sort(key=lambda x: x.start)
    if fixed != sort_check:
        stats["reordered"] = sum(1 for a, b in zip(sort_check, fixed, strict=True) if a is not b)

    # Step 3: trim overlap.
    for i in range(len(fixed) - 1):
        cur = fixed[i]
        nxt = fixed[i + 1]
        if cur.end > nxt.start:
            stats["trimmed_overlap"] += 1
            new_end = max(cur.start + min_duration, nxt.start - overlap_gap)
            fixed[i] = replace(cur, end=new_end)

    # Step 4: short-cue extension.
    for i, c in enumerate(fixed):
        if c.duration < min_duration:
            stats["extended_short"] += 1
            new_end = c.start + min_duration
            if i + 1 < len(fixed):
                new_end = min(new_end, fixed[i + 1].start - overlap_gap)
                new_end = max(c.start + 0.05, new_end)  # never collapse to 0
            fixed[i] = replace(c, end=new_end)

    # Step 5: line wrap (also splits cues over max_duration).
    rewrapped: list[SRTCue] = []
    for c in fixed:
        wrapped_text = _wrap_text(c.text, max_chars=max_chars)
        if wrapped_text != c.text:
            stats["rewrapped"] += 1
        rewrapped.append(replace(c, text=wrapped_text))

    # Re-index to 1..N.
    for i, c in enumerate(rewrapped, start=1):
        rewrapped[i - 1] = replace(c, index=i)

    # Check max_duration; we don't auto-split (preserves transcript intent),
    # but we record it for the UI.
    over_long = sum(1 for c in rewrapped if c.duration > max_duration)
    if over_long:
        stats["over_max_duration"] = over_long

    return rewrapped, stats


# ---------------------------------------------------------------------------
# FFmpeg burn (A path) — P0-8 + P0-16 single source of truth
# ---------------------------------------------------------------------------


def _ffmpeg_subtitles_arg(srt_path: Path | str) -> str:
    """Build the Windows-safe ``filename='...'`` arg for ffmpeg subtitles.

    P0-16 (VALIDATION.md §4): the bare positional form
    ``subtitles=C:/foo.srt:force_style=...`` mis-parses the drive colon as
    an option separator on ffmpeg ≥ 7.x. Always wrap with explicit
    ``filename=`` keyword. Safe to use on macOS / Linux too — the colon
    escape is a no-op when the path has no drive letter.
    """
    p = str(srt_path).replace("\\", "/")
    if len(p) >= 2 and p[1] == ":":
        p = p[0] + r"\:" + p[2:]
    return f"filename='{p}'"


def find_ffmpeg(explicit: str | None = None) -> str:
    """Resolve ffmpeg executable; raises ``FileNotFoundError`` if missing.

    Order: ``explicit`` arg → ``$FFMPEG_PATH`` env → ``shutil.which("ffmpeg")``.
    """
    candidates: list[str | None] = [explicit, os.environ.get("FFMPEG_PATH")]
    for c in candidates:
        if c and Path(c).exists():
            return c
    on_path = shutil.which("ffmpeg")
    if on_path:
        return on_path
    raise FileNotFoundError(
        "ffmpeg not found — set FFMPEG_PATH env var or pass ffmpeg_path in plugin Settings"
    )


async def burn_subtitles_ass(
    video_path: str | Path,
    srt_path: str | Path,
    output_path: str | Path,
    *,
    style: SubtitleStyle | str,
    ffmpeg_path: str | None = None,
    extra_args: list[str] | None = None,
    timeout_sec: float = 1800.0,
) -> str:
    """Burn subtitles into video using ffmpeg's ``subtitles`` filter.

    Returns the ``output_path`` as string on success. Raises
    :class:`subprocess.CalledProcessError` on non-zero exit (caller catches
    and surfaces as ``error_kind='dependency'`` or ``'format'``).
    """
    from subtitle_models import SUBTITLE_STYLES_BY_ID

    if isinstance(style, str):
        resolved = SUBTITLE_STYLES_BY_ID.get(style)
        if resolved is None:
            raise ValueError(f"Unknown style id {style!r}; valid: {sorted(SUBTITLE_STYLES_BY_ID)}")
        style_obj = resolved
    else:
        style_obj = style

    ffmpeg = find_ffmpeg(ffmpeg_path)
    sub_arg = _ffmpeg_subtitles_arg(srt_path)
    vf = f"subtitles={sub_arg}:force_style='{style_obj.to_force_style()}'"

    args = [
        ffmpeg,
        "-y",
        "-i",
        str(video_path),
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-c:a",
        "copy",
        *(extra_args or []),
        str(output_path),
    ]
    return await _run_ffmpeg(args, timeout_sec=timeout_sec, output_path=output_path)


async def _run_ffmpeg(args: list[str], *, timeout_sec: float, output_path: str | Path) -> str:
    """Run ffmpeg in a subprocess thread; return output_path on success."""

    def _run() -> tuple[int, str]:
        proc = subprocess.run(  # noqa: S603 — args are list, no shell
            args, capture_output=True, text=False, timeout=timeout_sec
        )
        return proc.returncode, proc.stderr.decode("utf-8", errors="replace")

    rc, err = await asyncio.to_thread(_run)
    if rc != 0:
        raise subprocess.CalledProcessError(
            returncode=rc, cmd=args, output=b"", stderr=err.encode("utf-8")
        )
    return str(output_path)


# ---------------------------------------------------------------------------
# Playwright HTML overlay (B path) — P0-13 lazy import + P0-14 singleton
# ---------------------------------------------------------------------------


class _PlaywrightSingleton:
    """Process-wide singleton to amortize the ~1.8 s Chromium launch.

    All state is class-level so ``Plugin.on_unload`` can call ``await
    _PlaywrightSingleton.close()`` from anywhere without holding a reference.
    """

    _lock = asyncio.Lock()
    _playwright: Any = None
    _browser: Any = None

    @classmethod
    async def get_browser(cls) -> Any:
        """Return a launched Chromium (cached). Lazy imports playwright."""
        if cls._browser is not None:
            return cls._browser
        async with cls._lock:
            if cls._browser is not None:
                return cls._browser
            from playwright.async_api import async_playwright  # P0-13

            cls._playwright = await async_playwright().start()
            cls._browser = await cls._playwright.chromium.launch()
            logger.info("Playwright Chromium launched (singleton)")
        return cls._browser

    @classmethod
    async def close(cls) -> None:
        async with cls._lock:
            if cls._browser is not None:
                try:
                    await cls._browser.close()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Playwright browser.close failed: %s", exc)
                cls._browser = None
            if cls._playwright is not None:
                try:
                    await cls._playwright.stop()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Playwright stop failed: %s", exc)
                cls._playwright = None
            logger.info("Playwright singleton closed")


async def burn_subtitles_html(
    video_path: str | Path,
    srt_path: str | Path,
    output_path: str | Path,
    *,
    style: SubtitleStyle | str,
    ffmpeg_path: str | None = None,
    timeout_sec: float = 1800.0,
    fallback_on_error: bool = True,
) -> str:
    """Burn subtitles via Playwright HTML overlay, falling back to ASS on error.

    Per VALIDATION.md §5 + P1-13: any failure (Playwright not installed,
    Chromium launch failure, font missing) is logged and we degrade to
    :func:`burn_subtitles_ass` so the pipeline still produces output.

    Performance note (val §5): single-frame render ≈ 340 ms; for a 1-min
    25 fps video this is ~510 s. Prefer the ASS path for long videos.
    """
    try:
        # Smoke test the singleton; if it fails we drop straight to ASS.
        await _PlaywrightSingleton.get_browser()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "burn_subtitles_html: Playwright unavailable (%s); falling back to ASS",
            exc,
        )
        if not fallback_on_error:
            raise
        return await burn_subtitles_ass(
            video_path,
            srt_path,
            output_path,
            style=style,
            ffmpeg_path=ffmpeg_path,
            timeout_sec=timeout_sec,
        )

    # Phase 2b ships the singleton + smoke test only; the actual frame-by-
    # frame render → overlay pipeline is intentionally deferred to v1.0.1
    # to avoid blocking the v1.0 release on ~340 ms × N-frames perf work
    # (see VALIDATION.md §5 — the budget for a 1-min video is 8.5 min,
    # which we don't want as the default).
    #
    # When fallback_on_error=True (default), this method behaves exactly
    # like burn_subtitles_ass; the only observable difference is the
    # singleton stays warm for the next call. Setting the option to False
    # raises NotImplementedError to surface the gap loudly in tests.
    if not fallback_on_error:
        raise NotImplementedError(
            "burn_subtitles_html full overlay path is deferred to v1.0.1; "
            "use fallback_on_error=True (default) to auto-degrade to ASS, "
            "or call burn_subtitles_ass directly."
        )
    logger.info(
        "burn_subtitles_html: Playwright warm; running ASS path "
        "(full HTML overlay deferred to v1.0.1)"
    )
    return await burn_subtitles_ass(
        video_path,
        srt_path,
        output_path,
        style=style,
        ffmpeg_path=ffmpeg_path,
        timeout_sec=timeout_sec,
    )
