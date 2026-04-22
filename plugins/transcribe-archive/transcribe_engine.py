"""transcribe-archive — chunked, cache-friendly, provider-agnostic ASR.

Why this engine exists (Sprint 11 / N1.3 from
``D:\\OpenAkita_AI_Video\\findings\\_summary_to_plan.md``):

    早期假设：复用 video-use ``helpers/transcribe.py`` 的整文件上传
    真实情况：``requests.post(..., timeout=1800)`` 把整个音频塞给 Scribe
    影响：30 分钟超时 + 无分片 + 无断点续传 → 长素材一旦失败全功尽弃

This module fixes those mistakes by construction:

* **Chunking by time window** (default 60 s, 5 s overlap) — every chunk
  is independently uploadable so a single 502 only loses the offending
  chunk, not the whole job.
* **Per-chunk cache** keyed on
  ``sha256(chunk_audio_bytes) + sha256(provider_args_json)`` — re-runs
  with the same provider/model skip every chunk that is already on
  disk.  Switching language or model invalidates the cache cleanly
  because the args hash changes.
* **Per-call ffmpeg ``timeout=``** (N1.4) — every subprocess invocation
  has a hard ceiling and the temp files are cleaned in ``finally``.
* **Provider adapters** — the engine never knows which ASR API it is
  talking to.  Plugins inject a ``TranscribeProvider`` callable, the
  built-in :class:`StubProvider` produces deterministic words for tests,
  and real providers (Whisper, Scribe, Tongyi-Audio) are added by
  composition.
* **Word-level timestamps** are the canonical output (``Word`` dataclass)
  — every renderer (SRT / VTT / plain text / JSON) is a pure function
  over a list of ``Word`` so the plugin layer doesn't need to know
  about subtitle formatting.
* **D2.10 verification** — the final transcript exposes a
  :class:`Verification` envelope with low-confidence words flagged so
  the host UI can highlight uncertain segments.

Public surface (everything else is private):

* :class:`Word` — one transcribed word with start/end/text/confidence
* :class:`Chunk` — one time window the engine sliced for ASR
* :class:`TranscriptResult` — final assembled output
* :class:`TranscribeProvider` — protocol every ASR adapter implements
* :class:`StubProvider` — deterministic fake for tests / smoke runs
* :func:`plan_chunks` — pure: file duration → list of ``Chunk``
* :func:`merge_words_with_overlap_dedup` — pure: chunked words → final
* :func:`to_srt` / :func:`to_vtt` / :func:`to_plain_text` —
  formatters
* :func:`to_verification` — D2.10 envelope from word confidences
* :func:`stub_transcribe_offline` — full pipeline that needs no network

This file MUST NOT depend on FastAPI, on the plugin API, or on the
host's brain.  It is pure logic so the same code can be used by
``plugin.py``, by CLI tools, and by other plugins (Sprint 12 bgm-mixer
will call ``plan_chunks`` to align beats with transcribed lyrics).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from openakita_plugin_sdk.contrib import (
    KIND_OTHER,
    KIND_QUOTE,
    LowConfidenceField,
    Verification,
)

logger = logging.getLogger(__name__)


# ── ffmpeg / ffprobe helpers ─────────────────────────────────────────


# N1.4 — every subprocess.run MUST have a timeout.  These constants live
# here so the plugin layer cannot accidentally call ffmpeg without one.
DEFAULT_FFPROBE_TIMEOUT_SEC = 30.0
DEFAULT_FFMPEG_CHUNK_TIMEOUT_SEC = 120.0  # per chunk; bigger files use more chunks, not longer ones


def ffmpeg_available() -> bool:
    """Return True only when both ``ffmpeg`` and ``ffprobe`` are on PATH.

    The engine depends on both: ffprobe reads duration, ffmpeg slices.
    Returning False here lets the plugin emit a friendly error
    (RenderedError style) instead of crashing on the first ASR call.
    """
    return bool(shutil.which("ffmpeg")) and bool(shutil.which("ffprobe"))


def probe_duration_seconds(audio_path: str | Path, *, timeout_sec: float = DEFAULT_FFPROBE_TIMEOUT_SEC) -> float:
    """Return the audio duration in seconds.

    Uses ffprobe with a hard timeout (N1.4).  Raises ``RuntimeError`` on
    any failure — the plugin layer is expected to wrap this in its
    error coach so the user sees "无法读取时长" with the ffprobe stderr.
    """
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(audio_path),
    ]
    try:
        out = subprocess.run(
            cmd, check=True, capture_output=True, text=True,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(
            f"ffprobe timed out after {timeout_sec}s on {audio_path}"
        ) from e
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"ffprobe failed: {e.stderr.strip()[:300]}"
        ) from e
    text = (out.stdout or "").strip()
    try:
        return float(text)
    except ValueError as e:
        raise RuntimeError(
            f"ffprobe returned non-numeric duration: {text!r}"
        ) from e


def slice_audio_chunk(
    audio_path: str | Path,
    *,
    start_sec: float,
    duration_sec: float,
    out_path: str | Path,
    sample_rate: int = 16000,
    timeout_sec: float = DEFAULT_FFMPEG_CHUNK_TIMEOUT_SEC,
) -> Path:
    """Extract one mono 16 kHz WAV chunk from ``audio_path``.

    16 kHz mono PCM is the format every modern ASR (Whisper, Paraformer,
    Scribe, ASR.cn) accepts natively — converting once at slice-time
    avoids per-provider re-encoding.

    Args:
        audio_path: Input file.
        start_sec: Slice start (seconds, may be fractional).
        duration_sec: Slice length (seconds).
        out_path: Where to write the chunk WAV.
        sample_rate: Output sample rate; 16000 is the safe default.
        timeout_sec: Per-call ffmpeg ceiling (N1.4 — never None).

    Returns:
        The path to the produced WAV (== ``out_path`` on success).
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", f"{start_sec:.3f}",
        "-t", f"{duration_sec:.3f}",
        "-i", str(audio_path),
        "-ac", "1",
        "-ar", str(int(sample_rate)),
        "-vn",
        str(out_path),
    ]
    try:
        subprocess.run(
            cmd, check=True, capture_output=True, text=True,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired as e:
        # Best-effort cleanup — never leave a half-written WAV on disk.
        out_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"ffmpeg timed out after {timeout_sec}s while slicing "
            f"[{start_sec:.2f}, {start_sec + duration_sec:.2f}]"
        ) from e
    except subprocess.CalledProcessError as e:
        out_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"ffmpeg slice failed: {e.stderr.strip()[:300]}"
        ) from e
    return out_path


# ── data shape ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Word:
    """One transcribed word with timing.

    Frozen so a renderer cannot mutate the timeline mid-pipeline (a real
    refs/video-use bug we're avoiding by construction — a translator
    once shifted ``end`` by 0.05 s "to fix overlap" and de-synced every
    subsequent caption).

    Attributes:
        text: The word as the ASR returned it (unicode-normalised).
        start: Start time in seconds (relative to the original file).
        end: End time in seconds.  ``end > start`` is enforced.
        confidence: 0.0-1.0 score from the provider; defaults to 1.0
            when the provider does not expose word-level confidence
            (Whisper-tiny does not, Whisper-large does).
    """

    text: str
    start: float
    end: float
    confidence: float = 1.0

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(
                f"Word.end ({self.end}) must be >= start ({self.start})"
            )
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                f"Word.confidence must be in [0,1], got {self.confidence}"
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Chunk:
    """One slice the engine plans to send to the ASR provider."""

    index: int
    start_sec: float
    end_sec: float

    @property
    def duration_sec(self) -> float:
        return self.end_sec - self.start_sec

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TranscriptResult:
    """Final assembled transcript + cache stats + verification."""

    words: list[Word]
    duration_sec: float
    language: str
    chunks_total: int
    chunks_from_cache: int
    chunks_failed: int
    provider_id: str
    failed_chunk_indexes: list[int] = field(default_factory=list)
    notes: str = ""

    @property
    def cache_hit_rate(self) -> float:
        if self.chunks_total <= 0:
            return 0.0
        return self.chunks_from_cache / self.chunks_total

    @property
    def coverage_sec(self) -> float:
        return sum(w.end - w.start for w in self.words)

    def to_dict(self) -> dict[str, Any]:
        return {
            "words": [w.to_dict() for w in self.words],
            "duration_sec": self.duration_sec,
            "language": self.language,
            "chunks_total": self.chunks_total,
            "chunks_from_cache": self.chunks_from_cache,
            "chunks_failed": self.chunks_failed,
            "failed_chunk_indexes": list(self.failed_chunk_indexes),
            "provider_id": self.provider_id,
            "cache_hit_rate": self.cache_hit_rate,
            "coverage_sec": self.coverage_sec,
            "notes": self.notes,
        }


# ── provider protocol + stub ───────────────────────────────────────────


class TranscribeProvider(Protocol):
    """Adapter protocol implemented by every ASR backend.

    The engine calls ``transcribe_chunk`` once per :class:`Chunk` and
    expects a list of :class:`Word` whose timestamps are RELATIVE to
    the chunk start (the engine shifts them back to file-absolute).

    Implementations MUST be deterministic given the same audio bytes
    and ``args`` — the cache key includes both, so a non-deterministic
    provider would silently corrupt the cache.

    The ``args_for_cache_key`` returns a JSON-serialisable dict that
    the engine hashes into the cache key so changing language/model
    invalidates cleanly without the engine having to know each
    provider's knobs.
    """

    @property
    def provider_id(self) -> str:  # pragma: no cover - protocol
        ...

    def args_for_cache_key(self) -> dict[str, Any]:  # pragma: no cover - protocol
        ...

    def transcribe_chunk(self, audio_path: Path, *, language: str) -> list[Word]:  # pragma: no cover - protocol
        ...


@dataclass
class StubProvider:
    """Deterministic fake that produces N synthetic words per chunk.

    Used for:

    * Unit tests (no network, no real audio)
    * The plugin's "no API key configured" smoke run, so the user sees
      the full pipeline shape before paying for a real ASR call.
    * The integration test for the cache layer (StubProvider's words
      must be byte-identical across calls).

    The synthetic words are derived from the chunk path's hash so two
    chunks with identical audio produce identical words — that's what
    makes the cache testable.
    """

    words_per_chunk: int = 6
    confidence: float = 0.85
    language_label: str = "zh"

    @property
    def provider_id(self) -> str:
        return "stub"

    def args_for_cache_key(self) -> dict[str, Any]:
        return {
            "words_per_chunk": self.words_per_chunk,
            "confidence": self.confidence,
            "language_label": self.language_label,
        }

    def transcribe_chunk(self, audio_path: Path, *, language: str) -> list[Word]:
        # Hash-driven so two identical chunks → identical words (that is
        # the whole point of the cache, and the StubProvider's test
        # contract).  We do NOT use os.urandom or time.time().
        h = hashlib.sha256(audio_path.read_bytes()).hexdigest()
        out: list[Word] = []
        slot = 1.0  # synthetic 1 s per word in chunk-relative time
        for i in range(self.words_per_chunk):
            tag = h[i * 4:(i + 1) * 4]
            out.append(Word(
                text=f"chunk_{tag}",
                start=i * slot,
                end=(i + 1) * slot - 0.05,  # tiny gap so end > start strictly
                confidence=self.confidence,
            ))
        return out


# ── chunk planner ──────────────────────────────────────────────────────


# Range guards — keep the planner sane.  refs/video-use uses 30 s windows
# (too small → expensive; the per-call HTTP overhead dominates) and
# whisper-large's sweet spot is 30-60 s.  60 s default is the goldilocks
# choice for both Scribe and Whisper-cloud.
DEFAULT_CHUNK_DURATION_SEC = 60.0
DEFAULT_CHUNK_OVERLAP_SEC = 5.0
MIN_CHUNK_DURATION_SEC = 10.0
MAX_CHUNK_DURATION_SEC = 600.0
MIN_OVERLAP_SEC = 0.0
MAX_OVERLAP_RATIO = 0.5  # overlap can't exceed half a chunk


def plan_chunks(
    duration_sec: float,
    *,
    chunk_duration_sec: float = DEFAULT_CHUNK_DURATION_SEC,
    overlap_sec: float = DEFAULT_CHUNK_OVERLAP_SEC,
) -> list[Chunk]:
    """Slice ``[0, duration_sec]`` into overlapping windows.

    Pure function — no I/O — so the caller can plan the whole pipeline
    (cost preview, parallelism, time estimate) before touching ffmpeg.

    Edge cases handled deterministically:

    * ``duration_sec <= 0`` → returns ``[]`` (engine emits a friendly
      error rather than crashing).
    * ``duration_sec <= chunk_duration_sec`` → single chunk
      ``[0, duration_sec]`` (no overlap needed).
    * Overlap >= half the chunk → clamped to half (otherwise the same
      audio appears in 3+ chunks and we'd dedup the entire transcript).
    * Last chunk always extends to ``duration_sec`` exactly; we never
      produce a "leftover" tail chunk shorter than 1/3 of a window
      (instead we extend the previous chunk's end).
    """
    if duration_sec <= 0:
        return []
    if not (MIN_CHUNK_DURATION_SEC <= chunk_duration_sec <= MAX_CHUNK_DURATION_SEC):
        raise ValueError(
            f"chunk_duration_sec must be in [{MIN_CHUNK_DURATION_SEC}, "
            f"{MAX_CHUNK_DURATION_SEC}], got {chunk_duration_sec}"
        )
    if overlap_sec < MIN_OVERLAP_SEC:
        raise ValueError(f"overlap_sec must be >= 0, got {overlap_sec}")
    overlap = min(overlap_sec, chunk_duration_sec * MAX_OVERLAP_RATIO)

    if duration_sec <= chunk_duration_sec:
        return [Chunk(index=0, start_sec=0.0, end_sec=duration_sec)]

    step = chunk_duration_sec - overlap
    if step <= 0:
        # Defensive: clamping above guarantees this never happens, but
        # keep the assertion so a future refactor cannot accidentally
        # produce zero-step infinite loops.
        raise ValueError("internal error: chunk step <= 0")

    chunks: list[Chunk] = []
    cursor = 0.0
    idx = 0
    while cursor < duration_sec:
        end = min(cursor + chunk_duration_sec, duration_sec)
        chunks.append(Chunk(index=idx, start_sec=cursor, end_sec=end))
        idx += 1
        if end >= duration_sec:
            break
        cursor += step

    # Tail-chunk policy: if the last chunk is shorter than 1/3 of a
    # window, fold it back into the previous chunk by extending that
    # chunk's end and dropping the tail.  Avoids a 4 s tail chunk that
    # the ASR cannot produce useful words for.
    if len(chunks) >= 2 and chunks[-1].duration_sec < chunk_duration_sec / 3:
        prev = chunks[-2]
        merged = Chunk(index=prev.index, start_sec=prev.start_sec, end_sec=duration_sec)
        chunks = chunks[:-2] + [merged]

    return chunks


# ── cache key derivation ───────────────────────────────────────────────


def chunk_cache_key(
    audio_chunk_bytes: bytes,
    *,
    provider_id: str,
    provider_args: dict[str, Any],
    language: str,
) -> str:
    """Derive the per-chunk cache key.

    Two SHA256s combined: one over the chunk audio, one over the
    provider+language args JSON.  Concatenating the two hex digests and
    re-hashing keeps the key fixed-length (64 chars) regardless of
    args size.

    Stability matters: changing this function invalidates EVERY cache
    on disk silently.  If you must change it, bump the engine version
    and let the plugin emit a "cache rebuild" warning.
    """
    audio_h = hashlib.sha256(audio_chunk_bytes).hexdigest()
    args_payload = json.dumps(
        {"provider_id": provider_id, "args": provider_args, "language": language},
        sort_keys=True, ensure_ascii=False,
    )
    args_h = hashlib.sha256(args_payload.encode("utf-8")).hexdigest()
    return hashlib.sha256(f"{audio_h}:{args_h}".encode("utf-8")).hexdigest()


def cache_path_for(cache_dir: Path, key: str) -> Path:
    """Return the on-disk path for a chunk cache entry.

    Splits the key into a 2-char prefix directory so a single cache dir
    never holds more than 256 sub-directories (avoids the Windows
    "too many files in one folder" slowdown that bit refs/video-use
    on long podcasts).
    """
    return cache_dir / key[:2] / f"{key}.json"


def load_cached_words(cache_dir: Path, key: str) -> list[Word] | None:
    """Read cached words for ``key`` or return None if not present /
    corrupted.

    A corrupted cache entry is silently treated as a miss (we re-call
    the provider) so a partial-write crash from a previous run cannot
    permanently poison the cache.
    """
    path = cache_path_for(cache_dir, key)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        words = [
            Word(
                text=str(d["text"]),
                start=float(d["start"]),
                end=float(d["end"]),
                confidence=float(d.get("confidence", 1.0)),
            )
            for d in data.get("words", [])
        ]
        return words
    except (OSError, ValueError, KeyError, TypeError):
        logger.warning("transcribe-archive: dropping corrupted cache entry %s", path)
        return None


def store_cached_words(cache_dir: Path, key: str, words: list[Word]) -> None:
    """Atomically write words to the cache.

    Writes to ``<file>.tmp`` then renames — readers never see a partial
    JSON document even on crash.  No-op if writing fails; the engine
    just won't get a cache hit next time.
    """
    path = cache_path_for(cache_dir, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = {"words": [w.to_dict() for w in words]}
    try:
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        tmp.replace(path)
    except OSError as e:
        logger.warning("transcribe-archive: failed to write cache %s: %s", path, e)
        tmp.unlink(missing_ok=True)


# ── word merger (overlap dedup) ────────────────────────────────────────


def merge_words_with_overlap_dedup(
    chunks: list[Chunk],
    chunk_words: list[list[Word]],
    *,
    overlap_sec: float = DEFAULT_CHUNK_OVERLAP_SEC,
) -> list[Word]:
    """Concatenate per-chunk words into a single timeline, deduplicating
    the overlap region.

    The overlap exists so a word that spans a chunk boundary is captured
    by at least one chunk in full.  Both chunks transcribe it, so the
    merger drops the duplicate by keeping the higher-confidence copy
    when both fall inside ``[next_chunk.start, prev_chunk.end]``.

    Args:
        chunks: Plan returned by :func:`plan_chunks`, in order.
        chunk_words: Per-chunk word lists; each list's timestamps must
            already be shifted into FILE-absolute time (the engine does
            this before calling).  An empty inner list means "this
            chunk failed" — handled by simply skipping it (other chunks
            still merge).
        overlap_sec: The overlap window the planner used.  Used as the
            dedup tolerance so words within ``overlap_sec`` of a chunk
            boundary are eligible for the dup check.

    Returns:
        A flat list of :class:`Word` sorted by ``start``.  Adjacent
        words may still overlap by < 50 ms (different ASR providers
        emit slightly different boundaries) — the renderers cope, this
        is intentional.
    """
    if len(chunks) != len(chunk_words):
        raise ValueError(
            "chunks and chunk_words must have the same length, got "
            f"{len(chunks)} vs {len(chunk_words)}"
        )

    out: list[Word] = []
    for i, (chunk, words) in enumerate(zip(chunks, chunk_words)):
        if not words:
            continue
        if not out:
            out.extend(words)
            continue

        # Overlap region: [chunk.start_sec, chunk.start_sec + overlap_sec]
        # plus tolerance.  Words from the new chunk that fall in this
        # region AND collide with an existing word (text match + time
        # within overlap_sec) are dropped — the existing one wins
        # unless the new one has materially higher confidence.
        boundary = chunk.start_sec
        accept: list[Word] = []
        for w in words:
            if w.start > boundary + overlap_sec:
                # Past the overlap region — always accept.
                accept.append(w)
                continue
            dup = _find_duplicate(out, w, overlap_sec=overlap_sec)
            if dup is None:
                accept.append(w)
                continue
            # Keep whichever has higher confidence.  Tie → keep
            # existing (stability — re-running with the same provider
            # yields the same transcript).
            if w.confidence > dup.confidence + 0.05:
                out.remove(dup)
                accept.append(w)
        out.extend(accept)

    out.sort(key=lambda w: w.start)
    return out


def _find_duplicate(existing: list[Word], candidate: Word, *, overlap_sec: float) -> Word | None:
    """Return the existing word that most likely is a duplicate of
    ``candidate`` (same text, time within overlap), or None."""
    norm = _normalize_for_dedup(candidate.text)
    for w in reversed(existing):
        if w.start < candidate.start - overlap_sec - 1.0:
            # Far past the overlap window — stop scanning.
            break
        if abs(w.start - candidate.start) <= overlap_sec and _normalize_for_dedup(w.text) == norm:
            return w
    return None


def _normalize_for_dedup(text: str) -> str:
    """Strip punctuation + lowercase for the dedup key only.

    The original ``Word.text`` keeps casing/punctuation — this is just
    the comparison key so "Hello," and "hello" count as duplicates.
    """
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE).lower()


# ── renderers (pure formatting) ────────────────────────────────────────


def to_plain_text(words: list[Word]) -> str:
    """Concatenate words with a single space between them.

    This is the renderer the IM channels paste into a chat reply.
    Detects "no spaces between CJK" automatically by looking at the
    word texts — Chinese/Japanese/Korean lines render without spaces
    so the output looks native.
    """
    if not words:
        return ""
    has_cjk = any(_is_cjk(w.text) for w in words)
    sep = "" if has_cjk else " "
    return sep.join(w.text for w in words).strip()


_CJK_RANGES = (
    (0x4E00, 0x9FFF),
    (0x3000, 0x303F),
    (0x3400, 0x4DBF),
    (0xF900, 0xFAFF),
    (0xFF00, 0xFFEF),  # half/full-width
)


def _is_cjk(text: str) -> bool:
    for ch in text:
        code = ord(ch)
        for lo, hi in _CJK_RANGES:
            if lo <= code <= hi:
                return True
    return False


def to_srt(words: list[Word], *, max_chars_per_cue: int = 42) -> str:
    """Render a list of words to a SubRip (.srt) subtitle file.

    Cue-grouping rule: pack words into a cue until the rendered text
    would exceed ``max_chars_per_cue`` OR a 0.7 s gap appears between
    consecutive words.  Both are the OpenMontage defaults
    (``slideshow_risk`` and ``delivery_promise`` use the same numbers).

    Pure function — feeds straight into ``Path(...).write_text(...)``.
    """
    cues = _group_into_cues(words, max_chars_per_cue=max_chars_per_cue)
    out: list[str] = []
    for i, cue in enumerate(cues, start=1):
        out.append(str(i))
        out.append(f"{_srt_ts(cue[0].start)} --> {_srt_ts(cue[-1].end)}")
        out.append(_join_cue_text(cue))
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def to_vtt(words: list[Word], *, max_chars_per_cue: int = 42) -> str:
    """Render to WebVTT (.vtt) — same chunking rule as SRT, different
    timestamp format and a ``WEBVTT`` header.  Browser-native (HTML5
    ``<track>``)."""
    cues = _group_into_cues(words, max_chars_per_cue=max_chars_per_cue)
    out: list[str] = ["WEBVTT", ""]
    for cue in cues:
        out.append(f"{_vtt_ts(cue[0].start)} --> {_vtt_ts(cue[-1].end)}")
        out.append(_join_cue_text(cue))
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def _group_into_cues(words: list[Word], *, max_chars_per_cue: int) -> list[list[Word]]:
    """Pack consecutive words into cue lists.

    Splits when:

    * adding the next word would exceed ``max_chars_per_cue``, OR
    * there is a > 0.7 s silence gap between consecutive words.

    Returns an empty list when ``words`` is empty.
    """
    if not words:
        return []
    cues: list[list[Word]] = [[]]
    for w in words:
        cur = cues[-1]
        if cur and (
            _cue_length(cur, extra=w.text) > max_chars_per_cue
            or w.start - cur[-1].end > 0.7
        ):
            cues.append([w])
        else:
            cur.append(w)
    return cues


def _cue_length(cue: list[Word], *, extra: str = "") -> int:
    base = _join_cue_text(cue)
    if not extra:
        return len(base)
    sep = "" if _is_cjk(extra) and _is_cjk(base or extra) else " "
    return len(base) + (len(sep) if base else 0) + len(extra)


def _join_cue_text(cue: list[Word]) -> str:
    if not cue:
        return ""
    has_cjk = any(_is_cjk(w.text) for w in cue)
    sep = "" if has_cjk else " "
    return sep.join(w.text for w in cue)


def _srt_ts(seconds: float) -> str:
    """``HH:MM:SS,mmm`` — comma is mandatory in SRT (different from VTT)."""
    s = max(0.0, seconds)
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = s - h * 3600 - m * 60
    whole = int(sec)
    ms = int(round((sec - whole) * 1000))
    if ms == 1000:
        whole += 1
        ms = 0
    return f"{h:02d}:{m:02d}:{whole:02d},{ms:03d}"


def _vtt_ts(seconds: float) -> str:
    """``HH:MM:SS.mmm`` — period separator (different from SRT)."""
    return _srt_ts(seconds).replace(",", ".")


# ── verification (D2.10) ───────────────────────────────────────────────


# Words below this confidence threshold are flagged in the verification
# envelope so the host UI can highlight them.  0.6 mirrors the AnyGen
# "fact yellow-highlight" threshold (D2.10) — high enough that the badge
# stays green for clean transcripts, low enough that "umm / maybe" type
# words show up.
LOW_CONFIDENCE_THRESHOLD = 0.6
MAX_FLAGGED_WORDS = 12  # cap so the UI never has to paginate


def to_verification(result: TranscriptResult) -> Verification:
    """Translate a :class:`TranscriptResult` into a D2.10 verification
    envelope.

    Flagging rules:

    * Any word with ``confidence < 0.6`` is flagged with KIND_QUOTE so
      the UI can render it as a yellow-highlighted span in the
      transcript pane.  Capped at 12 (oldest first) so a long-podcast
      with hundreds of low-confidence "uh"s does not blow up the
      payload.
    * If any chunks failed (``chunks_failed > 0``), flag
      ``$.transcript.coverage`` with KIND_OTHER so the UI shows a "N
      段失败" banner.
    * ``verifier_id`` is fixed to ``"transcribe_archive_self_check"``
      so a future host that wires in a real second-model verifier can
      compose with :func:`merge_verifications`.
    """
    fields: list[LowConfidenceField] = []
    low = [w for w in result.words if w.confidence < LOW_CONFIDENCE_THRESHOLD]
    for w in low[:MAX_FLAGGED_WORDS]:
        fields.append(LowConfidenceField(
            path=f"$.transcript.words[{result.words.index(w)}]",
            value=w.text,
            kind=KIND_QUOTE,
            reason=f"低置信度 {w.confidence:.2f} @ {w.start:.1f}s",
        ))
    if result.chunks_failed > 0:
        fields.append(LowConfidenceField(
            path="$.transcript.coverage",
            value=f"{result.chunks_total - result.chunks_failed}/{result.chunks_total}",
            kind=KIND_OTHER,
            reason=f"{result.chunks_failed} 段未能转写",
        ))
    notes_bits = []
    if result.chunks_from_cache > 0:
        notes_bits.append(
            f"{result.chunks_from_cache}/{result.chunks_total} 段命中缓存"
        )
    if low:
        notes_bits.append(f"{len(low)} 个低置信度词")
    return Verification(
        verified=result.chunks_failed == 0 and not low,
        verifier_id="transcribe_archive_self_check",
        low_confidence_fields=fields,
        notes="; ".join(notes_bits),
    )


# ── full pipeline (chunk → ASR → merge → render) ───────────────────────


def transcribe_file(
    audio_path: str | Path,
    *,
    provider: TranscribeProvider,
    cache_dir: str | Path,
    language: str = "zh",
    chunk_duration_sec: float = DEFAULT_CHUNK_DURATION_SEC,
    overlap_sec: float = DEFAULT_CHUNK_OVERLAP_SEC,
    progress_cb: Callable[[int, int], None] | None = None,
) -> TranscriptResult:
    """Run the full pipeline against a real audio file on disk.

    Pipeline:

    1. ffprobe the duration (N1.4 — bounded).
    2. Plan chunks via :func:`plan_chunks`.
    3. For each chunk:
       a. Slice into a temp WAV (16 kHz mono).
       b. Compute the cache key from chunk bytes + provider args.
       c. If the cache hits, load the words; else call the provider
          and store the result.
       d. Shift word timestamps from chunk-relative to file-absolute.
    4. Merge with overlap dedup.

    Failures of individual chunks are logged and the chunk is recorded
    in ``failed_chunk_indexes`` — the rest still runs (N1.1: never
    silently drop).

    The ``progress_cb`` is invoked as ``progress_cb(done, total)`` after
    every chunk (cache hit OR provider call) so the plugin can stream
    progress to the UI.
    """
    audio_path = Path(audio_path)
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    if not audio_path.is_file():
        raise FileNotFoundError(f"audio not found: {audio_path}")
    if not ffmpeg_available():
        raise RuntimeError(
            "ffmpeg / ffprobe not on PATH — install ffmpeg or use "
            "stub_transcribe_offline() for tests"
        )

    duration = probe_duration_seconds(audio_path)
    chunks = plan_chunks(
        duration,
        chunk_duration_sec=chunk_duration_sec,
        overlap_sec=overlap_sec,
    )
    provider_args = provider.args_for_cache_key()

    chunk_words: list[list[Word]] = []
    failed_idx: list[int] = []
    cache_hits = 0

    with tempfile.TemporaryDirectory(prefix="transcribe_archive_") as tmp:
        tmp_dir = Path(tmp)
        for i, chunk in enumerate(chunks):
            try:
                wav = slice_audio_chunk(
                    audio_path,
                    start_sec=chunk.start_sec,
                    duration_sec=chunk.duration_sec,
                    out_path=tmp_dir / f"chunk_{i:04d}.wav",
                )
                key = chunk_cache_key(
                    wav.read_bytes(),
                    provider_id=provider.provider_id,
                    provider_args=provider_args,
                    language=language,
                )
                cached = load_cached_words(cache_dir, key)
                if cached is not None:
                    cache_hits += 1
                    words = cached
                else:
                    words = provider.transcribe_chunk(wav, language=language)
                    store_cached_words(cache_dir, key, words)
                # Shift to file-absolute time.  Done here, NOT in the
                # provider, so providers stay simple (chunk-relative
                # only) and so the cache stores chunk-relative times
                # which makes cache entries portable across files.
                shifted = [
                    Word(
                        text=w.text,
                        start=w.start + chunk.start_sec,
                        end=w.end + chunk.start_sec,
                        confidence=w.confidence,
                    )
                    for w in words
                ]
                chunk_words.append(shifted)
            except Exception as e:  # noqa: BLE001 — fail per-chunk
                logger.warning(
                    "transcribe-archive: chunk %d (%.1f-%.1fs) failed: %s",
                    i, chunk.start_sec, chunk.end_sec, e,
                )
                chunk_words.append([])
                failed_idx.append(i)
            if progress_cb is not None:
                progress_cb(i + 1, len(chunks))

    merged = merge_words_with_overlap_dedup(
        chunks, chunk_words, overlap_sec=overlap_sec,
    )

    return TranscriptResult(
        words=merged,
        duration_sec=duration,
        language=language,
        chunks_total=len(chunks),
        chunks_from_cache=cache_hits,
        chunks_failed=len(failed_idx),
        provider_id=provider.provider_id,
        failed_chunk_indexes=failed_idx,
        notes="" if not failed_idx else f"{len(failed_idx)} chunks failed; see failed_chunk_indexes",
    )


def stub_transcribe_offline(
    *,
    duration_sec: float,
    chunk_duration_sec: float = DEFAULT_CHUNK_DURATION_SEC,
    overlap_sec: float = DEFAULT_CHUNK_OVERLAP_SEC,
    words_per_chunk: int = 6,
    confidence: float = 0.85,
    language: str = "zh",
    seed_text: str = "stub",
) -> TranscriptResult:
    """Fully-offline pipeline — no audio, no ffmpeg, no provider call.

    Used by:

    * The plugin's "no audio uploaded yet" preview, so the user can
      see the SRT/VTT shape before paying for a real transcription.
    * Unit tests that need a deterministic ``TranscriptResult`` to
      drive the renderers / verification path.

    The synthetic words are derived from ``seed_text`` so two calls
    with the same args produce identical transcripts (the
    StubProvider's deterministic contract, lifted to the file level).
    """
    if duration_sec <= 0:
        raise ValueError(f"duration_sec must be > 0, got {duration_sec}")
    chunks = plan_chunks(
        duration_sec,
        chunk_duration_sec=chunk_duration_sec,
        overlap_sec=overlap_sec,
    )
    chunk_words: list[list[Word]] = []
    for chunk in chunks:
        h = hashlib.sha256(
            f"{seed_text}|{chunk.index}|{words_per_chunk}".encode("utf-8")
        ).hexdigest()
        words: list[Word] = []
        slot = max(0.5, chunk.duration_sec / max(1, words_per_chunk))
        for i in range(words_per_chunk):
            tag = h[i * 4:(i + 1) * 4]
            words.append(Word(
                text=f"chunk_{chunk.index}_{tag}",
                start=chunk.start_sec + i * slot,
                end=min(chunk.end_sec, chunk.start_sec + (i + 1) * slot - 0.05),
                confidence=confidence,
            ))
        chunk_words.append(words)

    merged = merge_words_with_overlap_dedup(
        chunks, chunk_words, overlap_sec=overlap_sec,
    )
    return TranscriptResult(
        words=merged,
        duration_sec=duration_sec,
        language=language,
        chunks_total=len(chunks),
        chunks_from_cache=0,
        chunks_failed=0,
        provider_id="stub_offline",
        failed_chunk_indexes=[],
    )


# ── archive bundle (the "archive" in transcribe-archive) ───────────────


@dataclass(frozen=True)
class ArchiveBundle:
    """Self-contained export bundle the plugin returns to the host.

    ``json`` is the canonical record (round-trips through
    ``TranscriptResult.to_dict``); the other formats are convenience
    renderings the user can copy/download directly.
    """

    json: str
    txt: str
    srt: str
    vtt: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def to_archive_bundle(result: TranscriptResult, *, max_chars_per_cue: int = 42) -> ArchiveBundle:
    """Render a :class:`TranscriptResult` into the four canonical
    archive formats in one call.

    Pure function over the result — no I/O.  The plugin route uses
    this to assemble ``GET /tasks/{id}/archive.json`` in a single shot
    instead of recomputing each format on demand.
    """
    return ArchiveBundle(
        json=json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
        txt=to_plain_text(result.words),
        srt=to_srt(result.words, max_chars_per_cue=max_chars_per_cue),
        vtt=to_vtt(result.words, max_chars_per_cue=max_chars_per_cue),
    )


__all__ = [
    "ArchiveBundle",
    "Chunk",
    "DEFAULT_CHUNK_DURATION_SEC",
    "DEFAULT_CHUNK_OVERLAP_SEC",
    "DEFAULT_FFMPEG_CHUNK_TIMEOUT_SEC",
    "DEFAULT_FFPROBE_TIMEOUT_SEC",
    "LOW_CONFIDENCE_THRESHOLD",
    "MAX_CHUNK_DURATION_SEC",
    "MAX_FLAGGED_WORDS",
    "MIN_CHUNK_DURATION_SEC",
    "StubProvider",
    "TranscribeProvider",
    "TranscriptResult",
    "Word",
    "cache_path_for",
    "chunk_cache_key",
    "ffmpeg_available",
    "load_cached_words",
    "merge_words_with_overlap_dedup",
    "plan_chunks",
    "probe_duration_seconds",
    "slice_audio_chunk",
    "store_cached_words",
    "stub_transcribe_offline",
    "to_archive_bundle",
    "to_plain_text",
    "to_srt",
    "to_verification",
    "to_vtt",
    "transcribe_file",
]
