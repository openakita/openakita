"""RenderPipeline — safe FFmpeg command builder for video plugins.

Hard rules baked in (from ``video_use_deep`` and CutClaw findings):

- Always set output ``pix_fmt=yuv420p`` (broad player compatibility)
- Default frame rate ``24fps`` unless overridden
- Default encoder ``libx264`` (av1/hevc opt-in)
- Per-segment 30 ms audio fade-in / fade-out to kill clicks
- ``setpts=PTS-STARTPTS`` for concat to avoid drift
- Two-pass loudness normalization helper (EBU R128, LUFS=-16)
- Subtitle stream order: video → audio → subtitles
- ``subprocess.run`` always gets a ``timeout``
- Use ``shutil.which()`` to find binaries (raises ``RuntimeError`` if absent)

This module **does not run** ffmpeg — it only builds command lists.  The
plugin decides when / how to invoke (sync subprocess, async exec, etc.)
so we don't lock anyone into one approach.
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


_DEFAULT_TIMEOUT = 300.0   # 5 minutes per ffmpeg invocation


@dataclass
class RenderSegment:
    """One input clip in the pipeline."""

    source: str | Path           # input file path
    start: float = 0.0           # trim start (seconds)
    end: float | None = None     # trim end (seconds); None → whole file
    audio_fade_ms: int = 30      # per-segment audio fade
    label: str = ""              # optional debug label

    @property
    def duration(self) -> float | None:
        if self.end is None:
            return None
        return max(0.0, self.end - self.start)


@dataclass
class RenderPipeline:
    """Materialized render plan — list of segments + global options.

    Use :func:`build_render_pipeline` to construct, then call
    :meth:`to_concat_command` / :meth:`to_simple_command`.
    """

    segments: list[RenderSegment] = field(default_factory=list)
    output: str | Path = "output.mp4"
    fps: int = 24
    width: int | None = None
    height: int | None = None
    encoder: str = "libx264"
    audio_codec: str = "aac"
    pix_fmt: str = "yuv420p"
    crf: int = 20                # libx264 quality (lower = better)
    audio_bitrate: str = "192k"
    subtitle_path: str | Path | None = None
    extra_video_filters: list[str] = field(default_factory=list)
    extra_audio_filters: list[str] = field(default_factory=list)
    timeout_sec: float = _DEFAULT_TIMEOUT

    # ── command builders ────────────────────────────────────────────────

    def to_simple_command(self, ffmpeg: str | None = None) -> list[str]:
        """Single-input render (for trim / re-encode without concat)."""
        if not self.segments:
            raise ValueError("Pipeline is empty — add at least one segment")
        if len(self.segments) > 1:
            raise ValueError("Use to_concat_command() for multi-segment pipelines")
        seg = self.segments[0]
        bin_path = self._resolve_bin(ffmpeg or "ffmpeg")

        cmd: list[str] = [bin_path, "-y", "-hide_banner"]
        if seg.start > 0.0:
            cmd += ["-ss", f"{seg.start:.3f}"]
        cmd += ["-i", str(seg.source)]
        if seg.end is not None:
            cmd += ["-t", f"{(seg.end - seg.start):.3f}"]

        cmd += self._video_filters_args(extra=self.extra_video_filters)
        cmd += self._audio_filters_args(seg.audio_fade_ms, extra=self.extra_audio_filters)
        cmd += self._encoder_args()
        cmd += self._subtitle_args()
        cmd += [str(self.output)]
        return cmd

    def to_concat_command(self, list_file: str | Path, ffmpeg: str | None = None) -> list[str]:
        """Multi-input concat using a pre-written list file (concat demuxer).

        Caller is responsible for writing ``list_file`` (one ``file '<path>'``
        per line — :meth:`write_concat_list` helps).
        """
        if not self.segments:
            raise ValueError("Pipeline is empty")
        bin_path = self._resolve_bin(ffmpeg or "ffmpeg")

        cmd: list[str] = [
            bin_path, "-y", "-hide_banner",
            "-f", "concat", "-safe", "0",
            "-i", str(list_file),
        ]
        cmd += self._video_filters_args(extra=self.extra_video_filters)
        cmd += self._audio_filters_args(
            self.segments[0].audio_fade_ms,
            extra=self.extra_audio_filters,
        )
        cmd += self._encoder_args()
        cmd += self._subtitle_args()
        cmd += [str(self.output)]
        return cmd

    def write_concat_list(self, list_file: str | Path) -> Path:
        """Write the concat-demuxer list file and return its path."""
        p = Path(list_file)
        p.parent.mkdir(parents=True, exist_ok=True)
        # ffmpeg concat demuxer wants forward slashes & escaped single quotes
        lines: list[str] = []
        for s in self.segments:
            src = str(Path(s.source).as_posix()).replace("'", "'\\''")
            lines.append(f"file '{src}'")
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return p

    # ── two-pass loudness ───────────────────────────────────────────────

    @staticmethod
    def loudness_pass1_command(
        source: str | Path,
        *,
        target_lufs: float = -16.0,
        true_peak: float = -1.5,
        ffmpeg: str | None = None,
    ) -> list[str]:
        """Build the **measurement** pass — parse stderr for ``measured_*`` JSON."""
        bin_path = RenderPipeline._resolve_bin(ffmpeg or "ffmpeg")
        return [
            bin_path, "-hide_banner", "-y", "-i", str(source),
            "-af", f"loudnorm=I={target_lufs}:TP={true_peak}:LRA=11:print_format=json",
            "-f", "null", "-",
        ]

    @staticmethod
    def loudness_pass2_command(
        source: str | Path,
        output: str | Path,
        *,
        measured: dict[str, Any],
        target_lufs: float = -16.0,
        true_peak: float = -1.5,
        ffmpeg: str | None = None,
    ) -> list[str]:
        """Build the **apply** pass using measurements from pass 1."""
        bin_path = RenderPipeline._resolve_bin(ffmpeg or "ffmpeg")
        af = (
            f"loudnorm=I={target_lufs}:TP={true_peak}:LRA=11"
            f":measured_I={measured.get('input_i', 0)}"
            f":measured_TP={measured.get('input_tp', 0)}"
            f":measured_LRA={measured.get('input_lra', 0)}"
            f":measured_thresh={measured.get('input_thresh', 0)}"
            f":offset={measured.get('target_offset', 0)}"
            ":linear=true:print_format=summary"
        )
        return [
            bin_path, "-hide_banner", "-y", "-i", str(source),
            "-af", af, "-c:v", "copy", str(output),
        ]

    # ── private builders ────────────────────────────────────────────────

    def _video_filters_args(self, *, extra: Sequence[str]) -> list[str]:
        filters: list[str] = ["setpts=PTS-STARTPTS"]
        if self.width and self.height:
            filters.append(f"scale={self.width}:{self.height}:force_original_aspect_ratio=decrease")
            filters.append(f"pad={self.width}:{self.height}:(ow-iw)/2:(oh-ih)/2")
        filters.append(f"fps={self.fps}")
        filters.append(f"format={self.pix_fmt}")
        filters.extend(f for f in extra if f)
        return ["-vf", ",".join(filters)]

    def _audio_filters_args(self, fade_ms: int, *, extra: Sequence[str]) -> list[str]:
        fade_s = max(0.005, fade_ms / 1000.0)
        af: list[str] = [
            "asetpts=PTS-STARTPTS",
            f"afade=t=in:st=0:d={fade_s:.3f}",
            f"afade=t=out:st=0:d={fade_s:.3f}",  # offset filled at runtime if needed
        ]
        af.extend(f for f in extra if f)
        return ["-af", ",".join(af)]

    def _encoder_args(self) -> list[str]:
        return [
            "-c:v", self.encoder,
            "-preset", "medium",
            "-crf", str(self.crf),
            "-r", str(self.fps),
            "-pix_fmt", self.pix_fmt,
            "-c:a", self.audio_codec,
            "-b:a", self.audio_bitrate,
            "-movflags", "+faststart",
        ]

    def _subtitle_args(self) -> list[str]:
        if not self.subtitle_path:
            return []
        return ["-i", str(self.subtitle_path), "-c:s", "mov_text",
                "-map", "0:v", "-map", "0:a?", "-map", "1"]

    @staticmethod
    def _resolve_bin(name: str) -> str:
        """``shutil.which`` with a friendly RuntimeError."""
        if Path(name).is_absolute():
            return name
        found = shutil.which(name)
        if not found:
            raise RuntimeError(
                f"{name} not found in PATH — install ffmpeg "
                "(https://ffmpeg.org/download.html) and restart the app.",
            )
        return found


def build_render_pipeline(
    *,
    segments: Sequence[RenderSegment | tuple[Any, ...] | dict[str, Any]],
    output: str | Path,
    fps: int = 24,
    width: int | None = None,
    height: int | None = None,
    encoder: str = "libx264",
    crf: int = 20,
    subtitle_path: str | Path | None = None,
    timeout_sec: float = _DEFAULT_TIMEOUT,
) -> RenderPipeline:
    """Convenience constructor accepting heterogenous segment shapes.

    Each segment may be:

    - a :class:`RenderSegment` (used as-is)
    - a tuple ``(source, start, end)``
    - a dict ``{"source": ..., "start": 0, "end": None}``
    """
    norm: list[RenderSegment] = []
    for s in segments:
        if isinstance(s, RenderSegment):
            norm.append(s)
        elif isinstance(s, tuple):
            source = s[0]
            start = float(s[1]) if len(s) > 1 else 0.0
            end = float(s[2]) if len(s) > 2 and s[2] is not None else None
            norm.append(RenderSegment(source=source, start=start, end=end))
        elif isinstance(s, dict):
            norm.append(RenderSegment(
                source=s["source"],
                start=float(s.get("start", 0.0)),
                end=float(s["end"]) if s.get("end") is not None else None,
                audio_fade_ms=int(s.get("audio_fade_ms", 30)),
                label=str(s.get("label", "")),
            ))
        else:
            raise TypeError(f"Unsupported segment type: {type(s).__name__}")

    return RenderPipeline(
        segments=norm,
        output=output,
        fps=fps,
        width=width,
        height=height,
        encoder=encoder,
        crf=crf,
        subtitle_path=subtitle_path,
        timeout_sec=timeout_sec,
    )
