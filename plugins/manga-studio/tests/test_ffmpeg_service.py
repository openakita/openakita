"""Phase 2.6 — ffmpeg_service.py tests.

Two tiers:

1. Pure-function / no-FFmpeg tests run everywhere — SRT formatting,
   filter-path escaping, input validation (missing files, empty lists,
   single-element concat shortcut).
2. ``@requires_ffmpeg`` integration tests only run when FFmpeg is on
   PATH. They generate a 1-second test pattern with FFmpeg, then run
   each operation end-to-end (concat / attach_audio / mix_bgm /
   burn_subtitles / probe_duration) and assert on the resulting file.

Why we keep both: prompt-shape tests catch every cmd-line construction
bug for free, and integration tests catch the things that only break
when actually running ffmpeg (filter-graph syntax, Windows path quoting).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from ffmpeg_service import (
    FFmpegError,
    FFmpegService,
    SubtitleLine,
    _fmt_srt_time,
)

requires_ffmpeg = pytest.mark.skipif(not FFmpegService.is_available(), reason="ffmpeg not on PATH")


# ─── Pure helpers ─────────────────────────────────────────────────────────


def test_fmt_srt_time_basic() -> None:
    assert _fmt_srt_time(0) == "00:00:00,000"
    assert _fmt_srt_time(1.234) == "00:00:01,234"
    assert _fmt_srt_time(61.5) == "00:01:01,500"
    assert _fmt_srt_time(3600.0) == "01:00:00,000"
    # Negative clamped to 0 (FFmpeg won't accept negative srt timestamps).
    assert _fmt_srt_time(-1.0) == "00:00:00,000"


def test_fmt_srt_time_rounds_to_milliseconds() -> None:
    """0.0001 s → 0 ms (rounds down), 0.0005 s → 1 ms (banker's rounding
    of round() but our int(round(...)) gives 1)."""
    assert _fmt_srt_time(0.0001) == "00:00:00,000"
    # 0.6789 → 679 ms
    assert _fmt_srt_time(0.6789) == "00:00:00,679"


def test_escape_subtitle_path_windows_drive() -> None:
    s = FFmpegService._escape_subtitle_path(Path("C:/foo/bar.srt"))
    assert s == r"C\:/foo/bar.srt"


def test_escape_subtitle_path_unix_unchanged() -> None:
    s = FFmpegService._escape_subtitle_path(Path("/tmp/sub.srt"))
    # On Windows, Path("/tmp/sub.srt").as_posix() == "/tmp/sub.srt".
    assert s == "/tmp/sub.srt"


# ─── _write_srt ───────────────────────────────────────────────────────────


def test_write_srt_emits_well_formed_blocks() -> None:
    path = FFmpegService._write_srt(
        [
            SubtitleLine(0, 1.5, "你好"),
            {"start": 1.5, "end": 3.0, "text": "再见"},
        ]
    )
    try:
        content = path.read_text("utf-8")
        assert "1\n" in content
        assert "00:00:00,000 --> 00:00:01,500\n你好" in content
        assert "2\n" in content
        assert "00:00:01,500 --> 00:00:03,000\n再见" in content
    finally:
        path.unlink(missing_ok=True)


def test_write_srt_skips_blank_lines() -> None:
    path = FFmpegService._write_srt(
        [
            SubtitleLine(0, 1, "  "),
            SubtitleLine(1, 2, "real"),
        ]
    )
    try:
        content = path.read_text("utf-8")
        assert "real" in content
        # Blank entry was skipped — only ONE block written.
        assert content.count("-->") == 1
    finally:
        path.unlink(missing_ok=True)


def test_write_srt_rejects_empty_subtitles() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        FFmpegService._write_srt([])


# ─── is_available ─────────────────────────────────────────────────────────


def test_is_available_returns_bool() -> None:
    assert FFmpegService.is_available() in (True, False)


def test_is_available_false_for_garbage_binary(tmp_path: Path) -> None:
    assert FFmpegService.is_available(ffmpeg_bin="ffmpeg-does-not-exist-xyz") is False


# ─── Input validation (no FFmpeg required) ────────────────────────────────


async def test_concat_rejects_empty_paths(tmp_path: Path) -> None:
    svc = FFmpegService()
    with pytest.raises(ValueError, match="must not be empty"):
        await svc.concat([], tmp_path / "out.mp4")


async def test_concat_raises_when_input_missing(tmp_path: Path) -> None:
    svc = FFmpegService()
    with pytest.raises(FileNotFoundError):
        await svc.concat(
            [tmp_path / "missing1.mp4", tmp_path / "missing2.mp4"],
            tmp_path / "out.mp4",
        )


async def test_concat_single_video_just_copies(tmp_path: Path) -> None:
    """Single-input concat should copy without spawning ffmpeg —
    so it works even when ffmpeg isn't on PATH."""
    src = tmp_path / "src.mp4"
    src.write_bytes(b"fake video bytes")
    dst = tmp_path / "out.mp4"
    svc = FFmpegService(ffmpeg_bin="this-binary-does-not-exist")
    out = await svc.concat([src], dst)
    assert out == dst
    assert dst.read_bytes() == b"fake video bytes"


async def test_attach_audio_raises_when_video_missing(tmp_path: Path) -> None:
    svc = FFmpegService()
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")
    with pytest.raises(FileNotFoundError):
        await svc.attach_audio(tmp_path / "missing.mp4", audio, tmp_path / "out.mp4")


async def test_mix_bgm_raises_when_bgm_missing(tmp_path: Path) -> None:
    svc = FFmpegService()
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    with pytest.raises(FileNotFoundError):
        await svc.mix_bgm(video, tmp_path / "missing.mp3", tmp_path / "out.mp4")


async def test_burn_subtitles_raises_when_video_missing(tmp_path: Path) -> None:
    svc = FFmpegService()
    with pytest.raises(FileNotFoundError):
        await svc.burn_subtitles(
            tmp_path / "missing.mp4",
            [SubtitleLine(0, 1, "x")],
            tmp_path / "out.mp4",
        )


# ─── Cmd construction (mock _run) ─────────────────────────────────────────


async def test_concat_lossless_emits_concat_demuxer_cmd(tmp_path: Path, monkeypatch) -> None:
    """We assert on the cmd shape so even users without ffmpeg get a
    regression signal when the cmd construction changes."""
    a = tmp_path / "a.mp4"
    b = tmp_path / "b.mp4"
    for p in (a, b):
        p.write_bytes(b"fake")

    captured: dict[str, Any] = {}

    async def fake_run(self, cmd, *, timeout_sec=None, op_name="ffmpeg"):
        captured["cmd"] = list(cmd)
        captured["op"] = op_name
        return ""

    monkeypatch.setattr(FFmpegService, "_run", fake_run)
    svc = FFmpegService()
    await svc.concat([a, b], tmp_path / "out.mp4")
    cmd = captured["cmd"]
    assert "concat" in cmd
    assert "-safe" in cmd
    assert "-c" in cmd and "copy" in cmd
    assert captured["op"] == "concat-lossless"


async def test_burn_subtitles_emits_subtitles_filter(tmp_path: Path, monkeypatch) -> None:
    src = tmp_path / "v.mp4"
    src.write_bytes(b"fake")
    captured: dict[str, Any] = {}

    async def fake_run(self, cmd, *, timeout_sec=None, op_name="ffmpeg"):
        captured["cmd"] = list(cmd)
        return ""

    monkeypatch.setattr(FFmpegService, "_run", fake_run)
    svc = FFmpegService()
    await svc.burn_subtitles(
        src,
        [SubtitleLine(0, 1, "test")],
        tmp_path / "out.mp4",
    )
    cmd = captured["cmd"]
    vf_idx = cmd.index("-vf")
    vf_arg = cmd[vf_idx + 1]
    assert vf_arg.startswith("subtitles=")
    assert "FontSize=28" in vf_arg


async def test_attach_audio_emits_map_arguments(tmp_path: Path, monkeypatch) -> None:
    v = tmp_path / "v.mp4"
    a = tmp_path / "a.mp3"
    for p in (v, a):
        p.write_bytes(b"x")
    captured: dict[str, Any] = {}

    async def fake_run(self, cmd, *, timeout_sec=None, op_name="ffmpeg"):
        captured["cmd"] = list(cmd)
        return ""

    monkeypatch.setattr(FFmpegService, "_run", fake_run)
    svc = FFmpegService()
    await svc.attach_audio(v, a, tmp_path / "out.mp4")
    cmd = captured["cmd"]
    map_args = [cmd[i + 1] for i, x in enumerate(cmd) if x == "-map"]
    assert "0:v" in map_args
    assert "1:a" in map_args
    assert "-shortest" in cmd


# ─── _run timeout behaviour ───────────────────────────────────────────────


async def test_run_timeout_raises_ffmpeg_error(monkeypatch) -> None:
    """Force a timeout by spawning a "sleep" shell built-in via a fake
    process, then verify _run raises FFmpegError(returncode=None)."""

    class _SlowProc:
        returncode: int | None = None

        def __init__(self) -> None:
            self.terminated = False
            self.killed = False

        async def communicate(self) -> tuple[bytes, bytes]:
            await asyncio.sleep(10)
            return b"", b""

        def terminate(self) -> None:
            self.terminated = True
            # Simulate "still running" so wait() also times out → kill().
            # Actually we want the wait to succeed quickly so subsequent
            # operations don't hang the test.
            self.returncode = -15

        def kill(self) -> None:
            self.killed = True
            self.returncode = -9

        async def wait(self) -> int:
            return self.returncode or 0

    async def fake_create(*args, **kwargs):
        return _SlowProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)

    svc = FFmpegService(default_timeout_sec=0.05)
    with pytest.raises(FFmpegError) as exc:
        await svc._run(["ffmpeg", "-fake"])
    assert exc.value.returncode is None
    assert "timed out" in str(exc.value)


# ─── End-to-end (skip if ffmpeg missing) ──────────────────────────────────


@requires_ffmpeg
async def test_e2e_attach_and_concat_round_trip(tmp_path: Path) -> None:
    """Generate two 1-second test videos with built-in ffmpeg
    ``testsrc`` source, concat them, and verify the result has 2-second
    duration via ffprobe."""
    svc = FFmpegService()

    a = tmp_path / "a.mp4"
    b = tmp_path / "b.mp4"
    out = tmp_path / "out.mp4"
    for path in (a, b):
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=1:size=320x240:rate=30",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        assert path.exists() and path.stat().st_size > 0

    result = await svc.concat([a, b], out)
    assert result == out
    duration = await svc._probe_duration(out)
    # 2-second target ± codec rounding fudge.
    assert 1.7 <= duration <= 2.3


@requires_ffmpeg
async def test_e2e_burn_subtitles_writes_output(tmp_path: Path) -> None:
    src = tmp_path / "src.mp4"
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc=duration=2:size=320x240:rate=30",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        str(src),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    assert src.exists()

    svc = FFmpegService()
    out = tmp_path / "out.mp4"
    # ``force_style=False`` skips the FontName lookup that some CI
    # boxes (Windows / Linux without YaHei) don't have — the test still
    # exercises the subtitles filter end-to-end.
    await svc.burn_subtitles(
        src,
        [SubtitleLine(0, 2, "Hello")],
        out,
        force_style=False,
    )
    assert out.exists() and out.stat().st_size > 0


# ─── FFmpegError surface ──────────────────────────────────────────────────


def test_ffmpeg_error_to_dict_caps_stderr() -> None:
    exc = FFmpegError(
        "boom",
        returncode=1,
        stderr="x" * 5000,
        cmd=["ffmpeg", "-i", "in.mp4"],
    )
    d = exc.to_dict()
    assert d["returncode"] == 1
    assert len(d["stderr_tail"]) == 2000
    assert d["cmd"] == ["ffmpeg", "-i", "in.mp4"]


def test_ffmpeg_error_to_dict_no_cmd() -> None:
    exc = FFmpegError("x")
    d = exc.to_dict()
    assert d["cmd"] is None
    assert d["returncode"] is None
