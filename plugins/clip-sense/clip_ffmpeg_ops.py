"""FFmpeg operations for clip-sense: detect, cut, concat, silence, subtitles.

Reference: video-use render.py (cut/concat/subtitle templates)
Reference: CutClaw madmom_api.py:251-335 (silence detection RMS algorithm)
Reference: seedance system_deps.py:371-397 (Windows ffmpeg detection)

P0-4: concat demuxer requires identical encoding across segments
P0-5: Windows subtitles filter path needs : and ' escaping
P0-6: Silence detection uses relative threshold (relative to peak dB)
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import shutil
import struct
import tempfile
import wave
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_VERSION_RE = re.compile(r"version\s+(\S+)", re.IGNORECASE)

_CUT_VIDEO_ARGS = [
    "-c:v", "libx264", "-preset", "fast", "-crf", "22", "-pix_fmt", "yuv420p",
    "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
    "-movflags", "+faststart",
]

_SUBTITLE_VIDEO_ARGS = [
    "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
    "-c:a", "copy",
    "-movflags", "+faststart",
]


class FFmpegError(Exception):
    def __init__(self, message: str, *, kind: str = "dependency") -> None:
        super().__init__(message)
        self.kind = kind


class FFmpegOps:
    """Local ffmpeg operation wrapper (async subprocess, no shell=True)."""

    def __init__(self, ffmpeg_path: str | None = None) -> None:
        self._ffmpeg = ffmpeg_path or ""
        self._ffprobe = ""
        self._version = ""
        self._available = False
        self._detect()

    def _detect(self) -> None:
        path = self._ffmpeg or shutil.which("ffmpeg")
        if not path and os.name == "nt":
            _refresh_windows_path()
            path = shutil.which("ffmpeg")
        if path:
            self._ffmpeg = str(path)
            self._version = _get_version(self._ffmpeg)
            self._available = True
            probe = shutil.which("ffprobe")
            if probe:
                self._ffprobe = str(probe)
            else:
                ffdir = Path(self._ffmpeg).parent
                probe_candidate = ffdir / ("ffprobe.exe" if os.name == "nt" else "ffprobe")
                if probe_candidate.is_file():
                    self._ffprobe = str(probe_candidate)

    def detect(self) -> dict[str, Any]:
        self._detect()
        return {
            "available": self._available,
            "version": self._version,
            "path": self._ffmpeg,
        }

    @property
    def available(self) -> bool:
        return self._available

    def _require(self) -> str:
        if not self._available:
            raise FFmpegError("ffmpeg not found. Install ffmpeg >= 4.0.")
        return self._ffmpeg

    # ------------------------------------------------------------------
    # Duration
    # ------------------------------------------------------------------

    async def get_duration(self, video_path: str | Path) -> float:
        """Get video duration in seconds via ffprobe."""
        probe = self._ffprobe or self._require().replace("ffmpeg", "ffprobe")
        try:
            proc = await asyncio.create_subprocess_exec(
                probe,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(video_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            return float(stdout.decode().strip())
        except (ValueError, OSError) as e:
            logger.warning("ffprobe duration failed: %s", e)
            return 0.0

    # ------------------------------------------------------------------
    # Audio extraction
    # ------------------------------------------------------------------

    async def extract_audio(
        self, video_path: str | Path, output_path: str | Path, *, sample_rate: int = 16000
    ) -> Path:
        """Extract audio as 16kHz mono PCM WAV."""
        ffmpeg = self._require()
        out = Path(output_path)
        await self._run([
            ffmpeg, "-y", "-i", str(video_path),
            "-vn", "-ac", "1", "-ar", str(sample_rate), "-c:a", "pcm_s16le",
            str(out),
        ])
        return out

    # ------------------------------------------------------------------
    # Silence detection (pure Python RMS, reference: CutClaw madmom_api:251-335)
    # ------------------------------------------------------------------

    async def detect_silence(
        self,
        audio_path: str | Path,
        *,
        threshold_db: float = -40.0,
        min_silence_sec: float = 0.5,
        min_sound_sec: float = 0.05,
        padding_sec: float = 0.1,
    ) -> list[dict[str, Any]]:
        """Detect silence segments using frame-level RMS in dB.

        Returns list of silence intervals: [{"start": float, "end": float, "duration": float}]
        Uses relative threshold: thr = max(db_arr) + threshold_db (P0-6)
        """
        return await asyncio.to_thread(
            _detect_silence_sync,
            str(audio_path),
            threshold_db=threshold_db,
            min_silence_sec=min_silence_sec,
            min_sound_sec=min_sound_sec,
            padding_sec=padding_sec,
        )

    # ------------------------------------------------------------------
    # Video cutting and concatenation
    # ------------------------------------------------------------------

    async def cut_segments(
        self,
        video_path: str | Path,
        segments: list[dict[str, Any]],
        output_path: str | Path,
    ) -> Path:
        """Cut video into segments and concatenate.

        segments: [{"start": float, "end": float}, ...]
        P0-4: All segments re-encoded with identical params for concat compat.
        """
        ffmpeg = self._require()
        out = Path(output_path)
        tmp_dir = Path(tempfile.mkdtemp(prefix="clip_sense_cut_"))

        try:
            seg_files: list[Path] = []
            for i, seg in enumerate(segments):
                start = seg["start"]
                duration = seg["end"] - seg["start"]
                if duration <= 0:
                    continue
                seg_file = tmp_dir / f"seg_{i:04d}.mp4"

                fade_dur = min(0.03, duration / 4)
                af = f"afade=t=in:st=0:d={fade_dur},afade=t=out:st={duration - fade_dur}:d={fade_dur}"

                await self._run([
                    ffmpeg, "-y",
                    "-ss", f"{start:.3f}",
                    "-i", str(video_path),
                    "-t", f"{duration:.3f}",
                    *_CUT_VIDEO_ARGS,
                    "-af", af,
                    str(seg_file),
                ])
                if seg_file.exists():
                    seg_files.append(seg_file)

            if not seg_files:
                raise FFmpegError("No valid segments to concatenate", kind="format")

            if len(seg_files) == 1:
                seg_files[0].rename(out)
                return out

            list_file = tmp_dir / "concat_list.txt"
            list_file.write_text(
                "\n".join(f"file '{f.resolve()}'" for f in seg_files),
                encoding="utf-8",
            )
            await self._run([
                ffmpeg, "-y",
                "-f", "concat", "-safe", "0",
                "-i", str(list_file),
                "-c", "copy",
                "-movflags", "+faststart",
                str(out),
            ])
            return out
        finally:
            for f in tmp_dir.rglob("*"):
                try:
                    f.unlink()
                except OSError:
                    pass
            try:
                tmp_dir.rmdir()
            except OSError:
                pass

    async def remove_segments(
        self,
        video_path: str | Path,
        remove_list: list[dict[str, Any]],
        output_path: str | Path,
        total_duration: float | None = None,
    ) -> Path:
        """Remove specified segments (inverse of cut_segments)."""
        if total_duration is None:
            total_duration = await self.get_duration(video_path)
        if total_duration <= 0:
            raise FFmpegError("Cannot determine video duration", kind="format")

        sorted_removes = sorted(remove_list, key=lambda s: s["start"])
        keep_segments: list[dict[str, Any]] = []
        current = 0.0

        for rm in sorted_removes:
            if rm["start"] > current:
                keep_segments.append({"start": current, "end": rm["start"]})
            current = max(current, rm["end"])
        if current < total_duration:
            keep_segments.append({"start": current, "end": total_duration})

        keep_segments = [s for s in keep_segments if s["end"] - s["start"] > 0.05]

        if not keep_segments:
            raise FFmpegError("All content would be removed", kind="format")

        return await self.cut_segments(video_path, keep_segments, output_path)

    # ------------------------------------------------------------------
    # Subtitle burning (P0-5: Windows path escaping)
    # ------------------------------------------------------------------

    async def burn_subtitles(
        self,
        video_path: str | Path,
        srt_path: str | Path,
        output_path: str | Path,
    ) -> Path:
        """Burn SRT subtitles into video."""
        ffmpeg = self._require()
        out = Path(output_path)
        escaped = _escape_subtitle_path(str(Path(srt_path).resolve()))
        vf = f"subtitles='{escaped}'"

        await self._run([
            ffmpeg, "-y",
            "-i", str(video_path),
            "-vf", vf,
            *_SUBTITLE_VIDEO_ARGS,
            str(out),
        ])
        return out

    # ------------------------------------------------------------------
    # Thumbnail
    # ------------------------------------------------------------------

    async def extract_thumbnail(
        self, video_path: str | Path, time_sec: float = 1.0
    ) -> bytes:
        """Extract a single frame as JPEG bytes."""
        ffmpeg = self._require()
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            await self._run([
                ffmpeg, "-y",
                "-ss", f"{time_sec:.3f}",
                "-i", str(video_path),
                "-frames:v", "1", "-q:v", "4",
                "-vf", "scale=320:-2",
                tmp_path,
            ])
            return Path(tmp_path).read_bytes()
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # SRT generation (reference: video-use render.py)
    # ------------------------------------------------------------------

    @staticmethod
    def generate_srt(
        sentences: list[dict[str, Any]],
        segments: list[dict[str, Any]] | None = None,
    ) -> str:
        """Generate SRT subtitle content from transcript sentences.

        If segments provided, only include sentences within segment boundaries.
        Applies out_end fix: if end <= start, force end = start + 0.4
        """
        filtered = sentences
        if segments:
            seg_set = [(s["start"], s["end"]) for s in segments]
            filtered = [
                s for s in sentences
                if any(ss <= s.get("start", 0) and s.get("end", 0) <= se
                       for ss, se in seg_set)
            ]

        lines: list[str] = []
        cue_idx = 0
        for s in filtered:
            start = s.get("start", 0.0)
            end = s.get("end", 0.0)
            text = s.get("text", "")
            if not text.strip():
                continue
            if end <= start:
                end = start + 0.4
            cue_idx += 1
            lines.append(str(cue_idx))
            lines.append(f"{_srt_ts(start)} --> {_srt_ts(end)}")
            lines.append(text.strip())
            lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _run(self, cmd: list[str]) -> None:
        logger.debug("ffmpeg cmd: %s", " ".join(cmd))
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            err_text = stderr.decode(errors="replace")[-500:]
            raise FFmpegError(f"ffmpeg exited {proc.returncode}: {err_text}", kind="format")


# ======================================================================
# Module-level helpers
# ======================================================================

def _get_version(ffmpeg_path: str) -> str:
    import subprocess
    try:
        out = subprocess.run(
            [ffmpeg_path, "-version"],
            capture_output=True, text=True, timeout=5,
        )
        m = _VERSION_RE.search(out.stdout)
        return m.group(1) if m else ""
    except Exception:
        return ""


def _refresh_windows_path() -> None:
    """On Windows, read PATH from registry and update os.environ (P0-5 pattern)."""
    if os.name != "nt":
        return
    try:
        import winreg
        parts: list[str] = []
        for root, sub in [
            (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
            (winreg.HKEY_CURRENT_USER, r"Environment"),
        ]:
            try:
                with winreg.OpenKey(root, sub) as key:
                    val, _ = winreg.QueryValueEx(key, "Path")
                    parts.extend(os.path.expandvars(p) for p in str(val).split(";") if p.strip())
            except OSError:
                continue

        current = set(p.lower() for p in os.environ.get("PATH", "").split(";"))
        new_parts = [p for p in parts if p.lower() not in current]
        if new_parts:
            os.environ["PATH"] = os.environ.get("PATH", "") + ";" + ";".join(new_parts)
    except ImportError:
        pass


def _escape_subtitle_path(path: str) -> str:
    """Escape path for ffmpeg subtitles filter (P0-5)."""
    path = path.replace("\\", "/")
    path = path.replace(":", "\\:")
    path = path.replace("'", "\\'")
    return path


def _srt_ts(seconds: float) -> str:
    """Convert seconds to SRT timestamp format HH:MM:SS,mmm."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds * 1000) % 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _detect_silence_sync(
    audio_path: str,
    *,
    threshold_db: float = -40.0,
    min_silence_sec: float = 0.5,
    min_sound_sec: float = 0.05,
    padding_sec: float = 0.1,
    frame_length: int = 2048,
    hop_length: int = 512,
) -> list[dict[str, Any]]:
    """Pure-Python silence detection (no numpy). Returns silence intervals.

    Algorithm from CutClaw madmom_api.py:251-335, adapted for wave+struct.
    P0-6: Uses RELATIVE threshold (thr = max_db + threshold_db).
    """
    try:
        with wave.open(audio_path, "rb") as wf:
            n_channels = wf.getnchannels()
            sample_width = wf.getsampwidth()
            sr = wf.getframerate()
            n_frames = wf.getnframes()
            raw = wf.readframes(n_frames)
    except Exception as e:
        logger.warning("Cannot read audio for silence detection: %s", e)
        return []

    if sample_width == 2:
        fmt = f"<{n_frames * n_channels}h"
        try:
            samples_raw = struct.unpack(fmt, raw)
        except struct.error:
            return []
        scale = 1.0 / 32768.0
    elif sample_width == 1:
        samples_raw = [b - 128 for b in raw]
        scale = 1.0 / 128.0
    else:
        logger.warning("Unsupported sample width: %d", sample_width)
        return []

    if n_channels > 1:
        samples = [samples_raw[i] * scale for i in range(0, len(samples_raw), n_channels)]
    else:
        samples = [s * scale for s in samples_raw]

    total_samples = len(samples)

    if total_samples < frame_length:
        rms = math.sqrt(sum(s * s for s in samples) / max(len(samples), 1))
        db = 20.0 * math.log10(max(rms, 1e-12))
        if db > -120.0:
            return []
        return [{"start": 0.0, "end": total_samples / sr, "duration": total_samples / sr}]

    eps = 1e-12
    db_arr: list[float] = []
    frame_starts = range(0, total_samples - frame_length + 1, hop_length)

    for k in frame_starts:
        frame = samples[k : k + frame_length]
        mean_sq = sum(s * s for s in frame) / frame_length
        rms = math.sqrt(mean_sq)
        db_arr.append(20.0 * math.log10(max(rms, eps)))

    if not db_arr:
        return []

    max_db = max(db_arr)

    # If the loudest frame is below -80 dB, the entire file is effectively silent
    if max_db < -80.0:
        total_dur = total_samples / sr
        return [{"start": 0.0, "end": total_dur, "duration": total_dur}]

    thr = max_db + threshold_db

    mask = [db >= thr for db in db_arr]

    if not any(mask):
        total_dur = total_samples / sr
        return [{"start": 0.0, "end": total_dur, "duration": total_dur}]

    sound_intervals: list[list[float]] = []
    start_t: float | None = None

    for idx, is_sound in enumerate(mask):
        t = idx * hop_length / sr
        if is_sound:
            if start_t is None:
                start_t = t
        else:
            if start_t is not None:
                end_t = t + frame_length / sr
                sound_intervals.append([start_t, end_t])
                start_t = None
    if start_t is not None:
        end_t = (len(mask) - 1) * hop_length / sr + frame_length / sr
        sound_intervals.append([start_t, end_t])

    merged: list[list[float]] = []
    for interval in sound_intervals:
        if merged and interval[0] - merged[-1][1] <= min_silence_sec:
            merged[-1][1] = max(merged[-1][1], interval[1])
        else:
            merged.append(list(interval))

    final_sound: list[list[float]] = []
    for s, e in merged:
        if e - s >= min_sound_sec:
            ps = max(0.0, s - padding_sec)
            pe = e + padding_sec
            final_sound.append([ps, pe])

    total_dur = total_samples / sr
    silence_intervals: list[dict[str, Any]] = []
    current = 0.0

    for s, e in final_sound:
        if s > current + 0.01:
            silence_intervals.append({
                "start": round(current, 3),
                "end": round(s, 3),
                "duration": round(s - current, 3),
            })
        current = e

    if current < total_dur - 0.01:
        silence_intervals.append({
            "start": round(current, 3),
            "end": round(total_dur, 3),
            "duration": round(total_dur - current, 3),
        })

    return silence_intervals
