"""dub-it — pure-logic core.

The pipeline (all stages injectable for tests):

  1. **review** — :func:`source_review.review_video` (D2.3) gates the
     input.  Hard errors short-circuit the job; warnings flow into
     verification.
  2. **extract** — pull the audio track to a wav/m4a using ffmpeg.
  3. **transcribe** — caller-supplied transcriber returns a list of
     ``DubSegment`` (start, end, text, language).  Production wires
     this to ``transcribe-archive`` or local whisper.
  4. **translate** — caller-supplied translator returns the same
     segments with ``translated_text`` filled in.
  5. **synthesize** — caller-supplied TTS returns a single audio
     bytes blob (or per-segment bytes; we expose both shapes).
  6. **mux** — ffmpeg muxes the new audio onto the original video
     (with optional ducking of the original track).
  7. **verify** — D2.10 verification envelope.

The module imports nothing heavy at module level; ffmpeg / source-review
are imported only inside the functions that use them so callers can
unit-test ``plan_dub`` and ``to_verification`` without the SDK
subprocess deps.
"""
# --- _shared bootstrap (auto-inserted by archive cleanup) ---
import sys as _sys
import pathlib as _pathlib
_archive_root = _pathlib.Path(__file__).resolve()
for _p in _archive_root.parents:
    if (_p / '_shared' / '__init__.py').is_file():
        if str(_p) not in _sys.path:
            _sys.path.insert(0, str(_p))
        break
del _sys, _pathlib, _archive_root
# --- end bootstrap ---

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from _shared import (
    KIND_NUMBER,
    KIND_OTHER,
    LowConfidenceField,
    ReviewReport,
    Verification,
    review_video,
)

logger = logging.getLogger(__name__)


__all__ = [
    "ALLOWED_TARGET_LANGUAGES",
    "DEFAULT_DUCK_DB",
    "DEFAULT_OUTPUT_FORMAT",
    "DubPlan",
    "DubResult",
    "DubSegment",
    "Transcriber",
    "Translator",
    "Synthesizer",
    "build_extract_audio_argv",
    "build_mux_argv",
    "default_translator",
    "plan_dub",
    "preflight_review",
    "run_dub",
    "to_verification",
]


ALLOWED_TARGET_LANGUAGES = (
    "zh-CN", "zh-TW", "en", "en-US", "en-GB",
    "ja", "ko", "es", "fr", "de", "ru", "pt",
)
DEFAULT_OUTPUT_FORMAT = "mp4"
DEFAULT_DUCK_DB = -18  # how much we lower original audio when overlaying.
ALLOWED_OUTPUT_FORMATS = ("mp4", "mov", "mkv", "webm")


# ── Models ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DubSegment:
    """One transcript segment (post-translation if filled)."""

    index: int
    start_sec: float
    end_sec: float
    text: str
    translated_text: str = ""
    language: str = ""

    @property
    def duration_sec(self) -> float:
        return max(0.0, self.end_sec - self.start_sec)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "start_sec": round(self.start_sec, 3),
            "end_sec": round(self.end_sec, 3),
            "text": self.text,
            "translated_text": self.translated_text,
            "language": self.language,
        }


@dataclass
class DubPlan:
    """Frozen plan: validated input + review report + thresholds."""

    source_video: Path
    target_language: str
    output_format: str
    output_path: Path
    duck_db: int
    keep_original_audio: bool
    review: ReviewReport
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_video": str(self.source_video),
            "target_language": self.target_language,
            "output_format": self.output_format,
            "output_path": str(self.output_path),
            "duck_db": self.duck_db,
            "keep_original_audio": self.keep_original_audio,
            "review": self.review.to_dict(),
            "extra": dict(self.extra),
        }


@dataclass
class DubResult:
    """Per-job output."""

    plan: DubPlan
    segments: list[DubSegment]
    extracted_audio_path: Path | None
    dubbed_audio_path: Path | None
    output_video_path: Path | None
    elapsed_sec: float
    bytes_output: int
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None and self.output_video_path is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan": self.plan.to_dict(),
            "segments": [s.to_dict() for s in self.segments],
            "extracted_audio_path": (
                str(self.extracted_audio_path) if self.extracted_audio_path else None
            ),
            "dubbed_audio_path": (
                str(self.dubbed_audio_path) if self.dubbed_audio_path else None
            ),
            "output_video_path": (
                str(self.output_video_path) if self.output_video_path else None
            ),
            "elapsed_sec": round(self.elapsed_sec, 3),
            "bytes_output": self.bytes_output,
            "succeeded": self.succeeded,
            "error": self.error,
        }


Transcriber = Callable[[Path, str], Awaitable[list[DubSegment]]]
"""``async (audio_path, source_language_hint) -> [DubSegment]``"""

Translator = Callable[[list[DubSegment], str], Awaitable[list[DubSegment]]]
"""``async (segments, target_language) -> [DubSegment(translated_text=...)]``"""

Synthesizer = Callable[[list[DubSegment], str, Path], Awaitable[Path]]
"""``async (segments, target_language, out_path) -> Path``"""


# ── Validation + plan ─────────────────────────────────────────────────


def _validate_inputs(
    *, source_video: Path, target_language: str, output_format: str,
    duck_db: int,
) -> None:
    if not source_video.exists():
        raise FileNotFoundError(f"source video does not exist: {source_video}")
    if target_language not in ALLOWED_TARGET_LANGUAGES:
        raise ValueError(
            f"target_language must be one of {list(ALLOWED_TARGET_LANGUAGES)}, "
            f"got {target_language!r}",
        )
    if output_format not in ALLOWED_OUTPUT_FORMATS:
        raise ValueError(
            f"output_format must be one of {list(ALLOWED_OUTPUT_FORMATS)}, "
            f"got {output_format!r}",
        )
    if not (-60 <= duck_db <= 0):
        raise ValueError(
            f"duck_db must be in [-60, 0], got {duck_db!r}",
        )


def preflight_review(
    source_video: Path | str,
    *,
    ffprobe: str = "ffprobe",
    ffprobe_timeout_sec: float = 15.0,
    review_fn: Callable[..., ReviewReport] | None = None,
) -> ReviewReport:
    """Run :func:`review_video` (D2.3) on the source.

    ``review_fn`` is injectable for tests so we never shell out during
    unit testing.
    """
    fn = review_fn or review_video
    return fn(
        source_video,
        ffprobe=ffprobe,
        ffprobe_timeout_sec=ffprobe_timeout_sec,
    )


def plan_dub(
    *,
    source_video: Path | str,
    target_language: str,
    output_path: Path | str,
    output_format: str = DEFAULT_OUTPUT_FORMAT,
    duck_db: int = DEFAULT_DUCK_DB,
    keep_original_audio: bool = True,
    extra: dict[str, Any] | None = None,
    review_fn: Callable[..., ReviewReport] | None = None,
    ffprobe: str = "ffprobe",
    ffprobe_timeout_sec: float = 15.0,
) -> DubPlan:
    """Validate + run source review (D2.3) and freeze a :class:`DubPlan`.

    Raises:
        FileNotFoundError: when ``source_video`` is missing.
        ValueError: when target language / format / duck_db are invalid.
    """
    src = Path(source_video)
    out = Path(output_path)
    _validate_inputs(
        source_video=src, target_language=target_language,
        output_format=output_format, duck_db=duck_db,
    )
    review = preflight_review(
        src, ffprobe=ffprobe, ffprobe_timeout_sec=ffprobe_timeout_sec,
        review_fn=review_fn,
    )
    return DubPlan(
        source_video=src,
        target_language=target_language,
        output_format=output_format,
        output_path=out,
        duck_db=duck_db,
        keep_original_audio=keep_original_audio,
        review=review,
        extra=dict(extra or {}),
    )


# ── ffmpeg argv builders ──────────────────────────────────────────────


def build_extract_audio_argv(
    source_video: Path,
    out_audio: Path,
    *,
    ffmpeg: str = "ffmpeg",
    sample_rate: int = 16000,
    channels: int = 1,
) -> list[str]:
    """Build ``ffmpeg`` argv to extract a mono PCM-WAV track suitable for ASR.

    The defaults (16 kHz mono) match Whisper's preferred input.
    """
    if sample_rate <= 0:
        raise ValueError(f"sample_rate must be > 0, got {sample_rate!r}")
    if channels not in (1, 2):
        raise ValueError(f"channels must be 1 or 2, got {channels!r}")
    return [
        ffmpeg, "-y",
        "-i", str(source_video),
        "-vn",                  # drop video
        "-ac", str(channels),
        "-ar", str(sample_rate),
        "-c:a", "pcm_s16le",
        str(out_audio),
    ]


def build_mux_argv(
    source_video: Path,
    dubbed_audio: Path,
    out_video: Path,
    *,
    ffmpeg: str = "ffmpeg",
    duck_db: int = DEFAULT_DUCK_DB,
    keep_original_audio: bool = True,
) -> list[str]:
    """Build ``ffmpeg`` argv to mux ``dubbed_audio`` onto ``source_video``.

    When ``keep_original_audio=True`` the original track is *ducked*
    by ``duck_db`` and mixed under the dub.  Otherwise the original
    track is dropped entirely (clean dub).
    """
    if not (-60 <= duck_db <= 0):
        raise ValueError(f"duck_db must be in [-60, 0], got {duck_db!r}")

    cmd = [ffmpeg, "-y",
           "-i", str(source_video),
           "-i", str(dubbed_audio)]

    if keep_original_audio:
        # Quiet original [0:a], boost-normalise dub [1:a], mix together.
        # ``volume`` filter accepts dB strings ("-18dB").
        filter_complex = (
            f"[0:a]volume={duck_db}dB[orig];"
            "[1:a]volume=0dB[dub];"
            "[orig][dub]amix=inputs=2:duration=longest:dropout_transition=2[a]"
        )
        cmd += [
            "-filter_complex", filter_complex,
            "-map", "0:v:0",
            "-map", "[a]",
        ]
    else:
        cmd += [
            "-map", "0:v:0",
            "-map", "1:a:0",
        ]

    cmd += [
        "-c:v", "copy",
        "-c:a", "aac",
        "-shortest",
        str(out_video),
    ]
    return cmd


# ── Default translator (identity stub) ────────────────────────────────


async def default_translator(
    segments: list[DubSegment], target_language: str,
) -> list[DubSegment]:
    """No-op translator: copies ``text`` into ``translated_text``.

    Useful for smoke tests and when source language already matches
    target.  Real integrations swap this for an LLM call.
    """
    out: list[DubSegment] = []
    for s in segments:
        out.append(DubSegment(
            index=s.index, start_sec=s.start_sec, end_sec=s.end_sec,
            text=s.text,
            translated_text=s.translated_text or s.text,
            language=target_language,
        ))
    return out


# ── Run pipeline ──────────────────────────────────────────────────────


async def run_dub(
    plan: DubPlan,
    *,
    transcribe: Transcriber,
    synthesize: Synthesizer,
    translate: Translator | None = None,
    workdir: Path,
    on_stage: Callable[[str], None] | None = None,
    run_ffmpeg=None,  # injectable for tests
    ffmpeg: str = "ffmpeg",
    ffmpeg_timeout_sec: float = 1800.0,
    source_language_hint: str = "",
) -> DubResult:
    """Execute the full pipeline and return a :class:`DubResult`.

    Hard preconditions:
      * ``plan.review.passed`` must be True; otherwise we surface the
        first error issue and exit early without burning provider
        quota.

    All long-running stages are awaitable so a busy event loop never
    blocks.  ``run_ffmpeg`` defaults to the SDK's
    :func:`openakita_plugin_sdk.contrib.ffmpeg.run_ffmpeg`.
    """
    import time
    started = time.monotonic()

    if not plan.review.passed:
        first = plan.review.errors[0]
        return DubResult(
            plan=plan, segments=[],
            extracted_audio_path=None,
            dubbed_audio_path=None,
            output_video_path=None,
            elapsed_sec=time.monotonic() - started,
            bytes_output=0,
            error=f"source review failed: {first.code} — {first.message}",
        )

    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    extracted = workdir / f"{plan.source_video.stem}.extracted.wav"
    dubbed = workdir / f"{plan.source_video.stem}.dubbed.wav"

    if run_ffmpeg is None:
        from _shared import run_ffmpeg as _run_ffmpeg
        run_ffmpeg = _run_ffmpeg

    # 1. extract
    if on_stage:
        on_stage("extract")
    await run_ffmpeg(
        build_extract_audio_argv(plan.source_video, extracted, ffmpeg=ffmpeg),
        timeout_sec=ffmpeg_timeout_sec,
    )

    # 2. transcribe
    if on_stage:
        on_stage("transcribe")
    segments = await transcribe(extracted, source_language_hint)

    # 3. translate
    if on_stage:
        on_stage("translate")
    translator = translate or default_translator
    translated = await translator(segments, plan.target_language)

    # 4. synthesize
    if on_stage:
        on_stage("synthesize")
    dubbed_path = await synthesize(translated, plan.target_language, dubbed)
    dubbed = Path(dubbed_path)

    # 5. mux
    if on_stage:
        on_stage("mux")
    plan.output_path.parent.mkdir(parents=True, exist_ok=True)
    await run_ffmpeg(
        build_mux_argv(
            plan.source_video, dubbed, plan.output_path,
            ffmpeg=ffmpeg, duck_db=plan.duck_db,
            keep_original_audio=plan.keep_original_audio,
        ),
        timeout_sec=ffmpeg_timeout_sec,
    )

    bytes_output = (
        plan.output_path.stat().st_size if plan.output_path.is_file() else 0
    )
    return DubResult(
        plan=plan, segments=list(translated),
        extracted_audio_path=extracted,
        dubbed_audio_path=dubbed,
        output_video_path=plan.output_path,
        elapsed_sec=time.monotonic() - started,
        bytes_output=bytes_output,
    )


# ── verification (D2.10) ──────────────────────────────────────────────


def to_verification(result: DubResult) -> Verification:
    """Yellow-flag the things that commonly go wrong with dubbing."""
    fields: list[LowConfidenceField] = []

    if not result.succeeded:
        fields.append(LowConfidenceField(
            path="$.error",
            value=result.error,
            kind=KIND_OTHER,
            reason=(
                result.error
                or "dub job did not produce an output video"
            ),
        ))
        return Verification(
            verified=False,
            verifier_id="dub_it_self_check",
            low_confidence_fields=fields,
        )

    if result.output_video_path is not None and result.bytes_output == 0:
        fields.append(LowConfidenceField(
            path="$.bytes_output",
            value=0,
            kind=KIND_NUMBER,
            reason=(
                "ffmpeg reported success but the output file is 0 bytes — "
                "check disk space and that the input video has at least "
                "one usable audio stream"
            ),
        ))

    if not result.segments:
        fields.append(LowConfidenceField(
            path="$.segments",
            value=0,
            kind=KIND_NUMBER,
            reason=(
                "transcriber returned 0 segments — the dub will be silent; "
                "verify audio extraction or use a longer source clip"
            ),
        ))
    else:
        empty_translations = sum(
            1 for s in result.segments if not s.translated_text.strip()
        )
        if empty_translations > 0:
            fields.append(LowConfidenceField(
                path="$.segments[*].translated_text",
                value=empty_translations,
                kind=KIND_NUMBER,
                reason=(
                    f"{empty_translations} of {len(result.segments)} segments "
                    "have no translated text — the LLM translator may have "
                    "rate-limited or rejected the input"
                ),
            ))

    if result.plan.review.warnings:
        codes = ", ".join(w.code for w in result.plan.review.warnings)
        fields.append(LowConfidenceField(
            path="$.plan.review.warnings",
            value=len(result.plan.review.warnings),
            kind=KIND_NUMBER,
            reason=(
                f"source_review surfaced warnings ({codes}) — output may "
                "be lower quality than the input"
            ),
        ))

    return Verification(
        verified=not fields,
        verifier_id="dub_it_self_check",
        low_confidence_fields=fields,
    )


# ── helpers used by the plugin layer ──────────────────────────────────


def humanise_segment_summary(segments: list[DubSegment]) -> str:
    """Readable progress string for the brain tool."""
    if not segments:
        return "(0 segments)"
    total_chars = sum(len(s.translated_text) for s in segments)
    duration = segments[-1].end_sec - segments[0].start_sec
    return (
        f"{len(segments)} 段 · 共 {duration:.1f}s · "
        f"译文 {total_chars} 字"
    )


def safe_workdir_name(source_video: Path) -> str:
    """Sanitised stem usable as a workdir name."""
    stem = source_video.stem
    return "".join(c for c in stem if c.isalnum() or c in ("-", "_"))[:60] or "dub"


def _is_audio_stream_present(review: ReviewReport) -> bool:
    """Helper used by the plugin layer to surface a friendly error."""
    md = review.metadata or {}
    # review_video does not include audio metadata; absence of an
    # error issue is our best signal.
    return not any(
        i.code in ("audio.no_stream", "video.no_stream") for i in review.issues
    )


def env_int(name: str, default: int) -> int:
    """Tolerant env-int parser used by the plugin's config layer."""
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except (ValueError, TypeError):
        return default
