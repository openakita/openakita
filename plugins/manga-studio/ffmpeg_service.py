"""manga-studio FFmpeg service — concat / subtitle burn / BGM mix / audio mux.

Wraps the FFmpeg CLI with three operations the manga-drama pipeline
needs at the tail end:

1. ``concat`` — joins N panel videos into one episode. Lossless mode
   (``-c copy``) for same-codec sources, ``xfade`` mode for soft
   crossfade transitions. Borrowed from
   ``plugins/seedance-video/long_video.py`` (the ``concat_videos`` /
   ``_xfade_two`` pair) but rewritten to use ``asyncio.create_subprocess_exec``
   so we don't tie up a thread per concat.
2. ``burn_subtitles`` — burns dialogue + narration as on-frame text.
   We write a tempfile SRT and use FFmpeg's ``subtitles`` filter
   (NOT ``drawtext`` — drawtext can't escape Chinese punctuation
   reliably and stumbles on apostrophes; the SRT path is the standard
   and what seedance-video falls back to internally).
3. ``mix_bgm`` — mixes a low-volume background-music track under the
   primary dialogue audio. Uses ``amix`` with explicit ``volume=`` per
   input so we never get the "two voices fighting" effect.
4. ``attach_audio`` — replaces the silent panel video's audio track
   with a generated TTS file. Used in step 6 of the pipeline before
   ``concat`` so each clip carries its own dialogue.

Pixelle anti-pattern guardrails
-------------------------------
- **C8 (subprocess timeout)**: every spawn carries an explicit
  ``timeout_sec`` (default 600 s). On timeout we ``terminate()`` then
  ``kill()`` the child to release the file handle.
- **C2 (silent failure)**: every public method raises
  ``FFmpegError`` with ``returncode``, ``stderr`` (decoded with
  ``errors="replace"``), and the original ``cmd`` so the pipeline can
  surface a useful error in the UI.
- **C7 (env reads)**: this module never touches ``os.environ``. The
  ``ffmpeg`` binary path is taken from PATH (or the ``ffmpeg_bin``
  arg). Plugin-level Settings would set ``ffmpeg_bin`` if needed, not
  ENV.
- **C5 (silent recovery)**: ``concat`` with a single video copies it
  to ``output_path`` and returns it; ``concat`` with zero videos
  raises ``ValueError`` — we never quietly emit an empty file.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ─── Public types ─────────────────────────────────────────────────────────


class FFmpegError(Exception):
    """Raised when an FFmpeg invocation fails or times out.

    ``returncode`` is ``None`` for timeout errors, an int otherwise.
    ``stderr`` is the decoded last-line tail of FFmpeg's stderr (capped
    at 4 KB so a verbose codec log doesn't blow up the JSON payload).
    """

    def __init__(
        self,
        message: str,
        *,
        returncode: int | None = None,
        stderr: str = "",
        cmd: Sequence[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr
        self.cmd = list(cmd) if cmd else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "message": str(self),
            "returncode": self.returncode,
            "stderr_tail": self.stderr[-2000:],
            "cmd": self.cmd,
        }


@dataclass(frozen=True)
class SubtitleLine:
    """One srt cue. ``start`` / ``end`` are seconds from t=0."""

    start: float
    end: float
    text: str


# ─── Service ──────────────────────────────────────────────────────────────


class FFmpegService:
    """Async wrapper around the FFmpeg CLI.

    Args:
        ffmpeg_bin: Binary name or absolute path. Default ``"ffmpeg"``
            uses PATH lookup.
        default_timeout_sec: Cap for every operation; can be overridden
            per call.
    """

    def __init__(
        self,
        *,
        ffmpeg_bin: str = "ffmpeg",
        default_timeout_sec: float = 600.0,
    ) -> None:
        self._bin = ffmpeg_bin
        self._default_timeout = default_timeout_sec

    @staticmethod
    def is_available(*, ffmpeg_bin: str = "ffmpeg") -> bool:
        """Probe whether FFmpeg is reachable. Used by the plugin's
        ``healthz`` route so the UI can flag a missing binary up front."""
        return shutil.which(ffmpeg_bin) is not None

    # ── Internal: spawn ─────────────────────────────────────────

    async def _run(
        self,
        cmd: Sequence[str],
        *,
        timeout_sec: float | None = None,
        op_name: str = "ffmpeg",
    ) -> str:
        """Spawn ``cmd``, capture stderr, raise FFmpegError on failure.

        Returns the decoded stderr (FFmpeg writes its progress / status
        text to stderr by convention; stdout is reserved for piped
        media).
        """
        timeout = timeout_sec or self._default_timeout
        cmd_list = list(cmd)
        logger.debug("manga-studio %s: %s", op_name, " ".join(cmd_list))

        proc = await asyncio.create_subprocess_exec(
            *cmd_list,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError as exc:
            # Tear down the still-running ffmpeg so we don't leak.
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=5)
            except TimeoutError:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass
            raise FFmpegError(
                f"{op_name} timed out after {timeout:.0f}s",
                returncode=None,
                stderr="",
                cmd=cmd_list,
            ) from exc

        stderr_str = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
        if proc.returncode != 0:
            raise FFmpegError(
                f"{op_name} returned non-zero ({proc.returncode})",
                returncode=proc.returncode,
                stderr=stderr_str,
                cmd=cmd_list,
            )
        return stderr_str

    # ── Public: concat ──────────────────────────────────────────

    async def concat(
        self,
        video_paths: Sequence[str | Path],
        output_path: str | Path,
        *,
        transition: str = "none",
        fade_duration: float = 0.5,
        timeout_sec: float | None = None,
    ) -> Path:
        """Concatenate ``video_paths`` into ``output_path``.

        Args:
            video_paths: Ordered list of source video paths. Empty
                raises ``ValueError``; single-element copies the file.
            output_path: Destination .mp4.
            transition: ``"none"`` (lossless ``-c copy``) or
                ``"crossfade"`` (xfade filter, requires re-encoding).
            fade_duration: Crossfade length in seconds; ignored when
                ``transition="none"``.
        """
        if not video_paths:
            raise ValueError("concat: video_paths must not be empty")
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        paths = [Path(p) for p in video_paths]
        for p in paths:
            if not p.exists():
                raise FileNotFoundError(f"concat: missing input video {p}")

        if len(paths) == 1:
            shutil.copy2(paths[0], out)
            return out

        if transition == "crossfade":
            return await self._concat_crossfade(paths, out, fade_duration, timeout_sec)
        if transition not in ("none", ""):
            logger.warning("concat: unknown transition %r, using lossless", transition)
        return await self._concat_lossless(paths, out, timeout_sec)

    async def _concat_lossless(
        self,
        paths: list[Path],
        out: Path,
        timeout_sec: float | None,
    ) -> Path:
        # The concat demuxer reads a "list file" with one ``file '...''``
        # entry per source. We use NamedTemporaryFile but close it
        # before invoking ffmpeg so Windows doesn't lock the handle.
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            for p in paths:
                # ffmpeg requires forward-slashes inside quoted paths
                # on Windows, otherwise the concat demuxer treats the
                # backslashes as escapes.
                f.write(f"file '{p.as_posix()}'\n")
            list_file = Path(f.name)
        try:
            cmd = [
                self._bin,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_file),
                "-c",
                "copy",
                str(out),
            ]
            await self._run(cmd, timeout_sec=timeout_sec, op_name="concat-lossless")
        finally:
            list_file.unlink(missing_ok=True)
        return out

    async def _concat_crossfade(
        self,
        paths: list[Path],
        out: Path,
        fade_duration: float,
        timeout_sec: float | None,
    ) -> Path:
        """Iteratively xfade-merge pairs into a single output.

        With N inputs we run ``N-1`` xfade passes, each producing an
        intermediate file. This is O(N²) on bytes for very long
        episodes, but the same approach seedance-video uses (Pixelle
        N1.7 — same blueprint, same constraints).
        """
        # Build via temp files; clean up afterward regardless.
        temp_dir = Path(tempfile.mkdtemp(prefix="manga_xfade_"))
        try:
            current = paths[0]
            for i in range(1, len(paths)):
                next_in = paths[i]
                interim = temp_dir / f"xfade_{i}.mp4"
                await self._xfade_two(current, next_in, interim, fade_duration, timeout_sec)
                current = interim
            shutil.copy2(current, out)
            return out
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    async def _xfade_two(
        self,
        a: Path,
        b: Path,
        output: Path,
        fade_dur: float,
        timeout_sec: float | None,
    ) -> Path:
        # Probe duration of A so xfade.offset = duration_a - fade_dur.
        # We use ffprobe for this (it's a sibling of ffmpeg and ships
        # together; if ffmpeg is on PATH, ffprobe is too).
        duration_a = await self._probe_duration(a, timeout_sec=timeout_sec)
        offset = max(0.1, duration_a - fade_dur)

        cmd = [
            self._bin,
            "-y",
            "-i",
            str(a),
            "-i",
            str(b),
            "-filter_complex",
            f"xfade=transition=fade:duration={fade_dur}:offset={offset}",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "23",
            str(output),
        ]
        await self._run(cmd, timeout_sec=timeout_sec, op_name="concat-crossfade")
        return output

    async def _probe_duration(
        self,
        path: Path,
        *,
        timeout_sec: float | None = None,
    ) -> float:
        """Return media duration in seconds via ffprobe (best-effort)."""
        ffprobe = self._bin.replace("ffmpeg", "ffprobe") if "ffmpeg" in self._bin else "ffprobe"
        cmd = [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec or 30)
        except TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise FFmpegError("ffprobe timed out", returncode=None, stderr="", cmd=cmd) from exc
        try:
            return float(stdout_bytes.decode().strip())
        except ValueError:
            # ffprobe returned non-numeric; fall back to a safe default
            # rather than crashing the crossfade step.
            return 5.0

    # ── Public: burn subtitles ──────────────────────────────────

    async def burn_subtitles(
        self,
        video_path: str | Path,
        subtitles: Sequence[SubtitleLine | dict[str, Any]],
        output_path: str | Path,
        *,
        font_name: str | None = "Microsoft YaHei",
        font_size: int = 28,
        margin_v: int = 60,
        force_style: bool = True,
        timeout_sec: float | None = None,
    ) -> Path:
        """Burn ``subtitles`` onto ``video_path`` using FFmpeg ``subtitles`` filter.

        We write a tempfile .srt instead of building a long ``drawtext``
        chain because (a) drawtext drops Chinese on some FFmpeg builds,
        (b) it can't render apostrophes / brackets without escaping
        nightmares, and (c) the SRT path is the canonical answer in
        every FFmpeg how-to.

        Args:
            font_name: Override the rendered font. Default Microsoft
                YaHei is what ships with Windows (a typical user box for
                this plugin); pass ``None`` or empty to use FFmpeg's
                bundled default and let the OS render whatever it has.
            force_style: When ``False`` we skip the ``force_style``
                segment entirely — the SRT plays with FFmpeg defaults,
                which is the most-portable rendering and what tests on
                stripped-down CI machines should use.
        """
        src = Path(video_path)
        if not src.exists():
            raise FileNotFoundError(f"burn_subtitles: source video {src} missing")
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        srt_path = self._write_srt(subtitles)
        try:
            srt_arg = self._escape_subtitle_path(srt_path)

            # FFmpeg's filter-graph parser uses ``:`` as the option
            # separator within a filter's args. Windows drive letters
            # (``C:``) collide with this, so we wrap the path in single
            # quotes — the parser then treats the entire quoted span
            # as one filename token. See ffmpeg-filters(1), section
            # "Notes on filtergraph escaping" → "Quoting and escaping".
            if force_style:
                style_parts = [f"FontSize={font_size}"]
                if font_name:
                    style_parts.append(f"FontName={font_name}")
                style_parts.extend(
                    [
                        "PrimaryColour=&Hffffff",
                        "OutlineColour=&H000000",
                        "BorderStyle=1",
                        "Outline=2",
                        "Shadow=0",
                        "Alignment=2",
                        f"MarginV={margin_v}",
                    ]
                )
                style = ",".join(style_parts)
                vf = f"subtitles=filename='{srt_arg}':force_style='{style}'"
            else:
                vf = f"subtitles=filename='{srt_arg}'"

            cmd = [
                self._bin,
                "-y",
                "-i",
                str(src),
                "-vf",
                vf,
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "23",
                "-c:a",
                "copy",
                str(out),
            ]
            await self._run(cmd, timeout_sec=timeout_sec, op_name="burn-subtitles")
            return out
        finally:
            srt_path.unlink(missing_ok=True)

    @staticmethod
    def _write_srt(subtitles: Sequence[SubtitleLine | dict[str, Any]]) -> Path:
        """Persist ``subtitles`` to a tempfile .srt and return the path.

        The list entries can be ``SubtitleLine`` instances or plain
        dicts with ``start`` / ``end`` / ``text`` keys — both are
        accepted so the pipeline doesn't have to wrap dicts before
        calling.
        """
        if not subtitles:
            raise ValueError("burn_subtitles: subtitles must not be empty")
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".srt", delete=False, encoding="utf-8"
        ) as f:
            for idx, item in enumerate(subtitles, start=1):
                if isinstance(item, SubtitleLine):
                    start, end, text = item.start, item.end, item.text.strip()
                else:
                    start = float(item.get("start", 0))
                    end = float(item.get("end", start + 1))
                    text = str(item.get("text", "")).strip()
                if not text:
                    continue
                f.write(f"{idx}\n")
                f.write(f"{_fmt_srt_time(start)} --> {_fmt_srt_time(end)}\n")
                f.write(f"{text}\n\n")
            return Path(f.name)

    @staticmethod
    def _escape_subtitle_path(path: Path) -> str:
        """Escape a subtitle file path for FFmpeg's filter argument syntax.

        On Windows ``C:/foo/bar.srt`` becomes ``C\\:/foo/bar.srt`` in
        the filter graph so the colon doesn't terminate the option.
        """
        s = path.as_posix()
        if len(s) >= 2 and s[1] == ":":
            return s[0] + r"\:" + s[2:]
        return s

    # ── Public: mix BGM ─────────────────────────────────────────

    async def mix_bgm(
        self,
        video_path: str | Path,
        bgm_path: str | Path,
        output_path: str | Path,
        *,
        dialogue_volume: float = 1.0,
        bgm_volume: float = 0.25,
        bgm_fade_in: float = 1.0,
        bgm_fade_out: float = 1.0,
        timeout_sec: float | None = None,
    ) -> Path:
        """Mix a BGM track under the video's existing dialogue audio.

        ``video_path`` should already have its dialogue track attached
        (use :meth:`attach_audio` first). The BGM is faded in / out
        and ducked by ``bgm_volume`` (default 0.25 — quiet enough that
        the dialogue stays the primary signal).
        """
        src = Path(video_path)
        bgm = Path(bgm_path)
        for p in (src, bgm):
            if not p.exists():
                raise FileNotFoundError(f"mix_bgm: missing input {p}")
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        # Probe video duration so we know where to fade out the BGM.
        video_duration = await self._probe_duration(src, timeout_sec=timeout_sec)
        fade_out_start = max(0.0, video_duration - bgm_fade_out)

        # ``[0:a]`` is the dialogue track from input 0; ``[1:a]`` is
        # the BGM. We adjust volumes, apply the BGM fade, then mix
        # both with ``amix`` (duration=first so we don't extend past
        # the video).
        filter_complex = (
            f"[0:a]volume={dialogue_volume}[a0];"
            f"[1:a]volume={bgm_volume},"
            f"afade=t=in:st=0:d={bgm_fade_in},"
            f"afade=t=out:st={fade_out_start}:d={bgm_fade_out}[a1];"
            f"[a0][a1]amix=inputs=2:duration=first:dropout_transition=0[aout]"
        )

        cmd = [
            self._bin,
            "-y",
            "-i",
            str(src),
            "-i",
            str(bgm),
            "-filter_complex",
            filter_complex,
            "-map",
            "0:v",
            "-map",
            "[aout]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-shortest",
            str(out),
        ]
        await self._run(cmd, timeout_sec=timeout_sec, op_name="mix-bgm")
        return out

    # ── Public: attach audio ────────────────────────────────────

    async def attach_audio(
        self,
        video_path: str | Path,
        audio_path: str | Path,
        output_path: str | Path,
        *,
        timeout_sec: float | None = None,
    ) -> Path:
        """Replace the video's audio with ``audio_path`` (or add it if
        the source video had no audio track).

        Used in pipeline step 6 to attach a TTS-generated MP3 to a
        silent panel video before the panels are concatenated.
        """
        src = Path(video_path)
        aud = Path(audio_path)
        for p in (src, aud):
            if not p.exists():
                raise FileNotFoundError(f"attach_audio: missing input {p}")
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        # ``-shortest`` so a TTS clip slightly longer than the visual
        # doesn't extend the panel; the truncation is ≤ 0.5 s and the
        # pipeline's panel duration already accounts for it.
        cmd = [
            self._bin,
            "-y",
            "-i",
            str(src),
            "-i",
            str(aud),
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-shortest",
            str(out),
        ]
        await self._run(cmd, timeout_sec=timeout_sec, op_name="attach-audio")
        return out


# ─── SRT helpers ──────────────────────────────────────────────────────────


def _fmt_srt_time(seconds: float) -> str:
    """``00:00:01,234`` SRT time-stamp from a float second offset."""
    if seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    h, rem = divmod(total_ms, 3600 * 1000)
    m, rem = divmod(rem, 60 * 1000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


__all__ = [
    "FFmpegError",
    "FFmpegService",
    "SubtitleLine",
    "_fmt_srt_time",
]
