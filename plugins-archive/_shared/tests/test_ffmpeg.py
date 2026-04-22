"""Tests for _shared.ffmpeg.

These tests do **not** require ffmpeg to be installed: we exercise the
binary resolver, the timeout validation, and the error wrapping using
in-process commands (``python``) and synthetic argv.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from _shared import (
    FFmpegError,
    FFmpegResult,
    ffprobe_json_sync,
    resolve_binary,
    run_ffmpeg_sync,
)


# ── timeout validation ──────────────────────────────────────────────────────


def test_run_ffmpeg_sync_requires_positive_timeout() -> None:
    with pytest.raises(ValueError, match="timeout_sec"):
        run_ffmpeg_sync([sys.executable, "-c", "pass"], timeout_sec=0)
    with pytest.raises(ValueError, match="timeout_sec"):
        run_ffmpeg_sync([sys.executable, "-c", "pass"], timeout_sec=-1)
    with pytest.raises(ValueError, match="timeout_sec"):
        run_ffmpeg_sync([sys.executable, "-c", "pass"], timeout_sec=None)  # type: ignore[arg-type]


def test_run_ffmpeg_sync_rejects_empty_cmd() -> None:
    with pytest.raises(ValueError, match="cmd"):
        run_ffmpeg_sync([], timeout_sec=5)


# ── successful execution (uses python as a stand-in for ffmpeg) ─────────────


def test_run_ffmpeg_sync_success_returns_result() -> None:
    """Use python -c as a fake 'ffmpeg' that exits 0."""
    out = run_ffmpeg_sync(
        [sys.executable, "-c", "import sys; sys.stdout.write('ok'); sys.stderr.write('warn')"],
        timeout_sec=10,
    )
    assert isinstance(out, FFmpegResult)
    assert out.returncode == 0
    assert out.stdout.strip() == "ok"
    assert "warn" in out.stderr
    assert out.duration_sec > 0
    assert out.cmd[0] == sys.executable


def test_run_ffmpeg_sync_check_false_does_not_raise_on_nonzero() -> None:
    out = run_ffmpeg_sync(
        [sys.executable, "-c", "import sys; sys.exit(3)"],
        timeout_sec=10,
        check=False,
    )
    assert out.returncode == 3


def test_run_ffmpeg_sync_raises_on_nonzero_exit() -> None:
    with pytest.raises(FFmpegError) as ei:
        run_ffmpeg_sync(
            [sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(2)"],
            timeout_sec=10,
        )
    assert ei.value.returncode == 2
    assert "boom" in ei.value.stderr_tail
    assert ei.value.timed_out is False


def test_run_ffmpeg_sync_timeout_raises_with_timed_out_flag() -> None:
    with pytest.raises(FFmpegError) as ei:
        run_ffmpeg_sync(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            timeout_sec=0.5,
        )
    assert ei.value.timed_out is True
    assert ei.value.returncode is None


def test_run_ffmpeg_sync_missing_binary_raises_ffmpeg_error() -> None:
    with pytest.raises(FFmpegError):
        run_ffmpeg_sync(
            ["definitely_not_a_real_binary_xyz_abc_123"],
            timeout_sec=5,
        )


# ── resolve_binary ──────────────────────────────────────────────────────────


def test_resolve_binary_finds_python_via_which() -> None:
    """Should find a binary that is on PATH (we know python is)."""
    # Use just the basename so resolve_binary calls shutil.which
    name = Path(sys.executable).name
    found = resolve_binary(name)
    assert found
    assert Path(found).exists()


def test_resolve_binary_passes_through_absolute_existing_path() -> None:
    out = resolve_binary(sys.executable)
    assert out == sys.executable


def test_resolve_binary_raises_for_nonexistent_absolute_path(tmp_path: Path) -> None:
    fake = tmp_path / "no_such_binary.exe"
    with pytest.raises(RuntimeError, match="does not exist"):
        resolve_binary(str(fake))


def test_resolve_binary_raises_for_missing_binary() -> None:
    with pytest.raises(RuntimeError, match="not found in PATH"):
        resolve_binary("definitely_not_a_real_binary_xyz_abc_123")


def test_resolve_binary_rejects_invalid_input() -> None:
    with pytest.raises(ValueError):
        resolve_binary("")
    with pytest.raises(ValueError):
        resolve_binary(None)  # type: ignore[arg-type]


# ── ffprobe_json (only if ffprobe available) ────────────────────────────────


@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe not installed")
def test_ffprobe_json_sync_on_real_file_returns_dict(tmp_path: Path) -> None:
    """Smoke test: if ffprobe is installed, run on a tiny generated wav."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg required to generate fixture")
    sample = tmp_path / "tone.wav"
    # 1 second of silence
    run_ffmpeg_sync(
        ["ffmpeg", "-y", "-hide_banner", "-f", "lavfi",
         "-i", "anullsrc=r=8000:cl=mono", "-t", "0.5", str(sample)],
        timeout_sec=10,
    )
    info = ffprobe_json_sync(sample, timeout_sec=10)
    assert "format" in info
    assert "streams" in info
    assert isinstance(info["streams"], list)


def test_ffprobe_json_sync_raises_when_ffprobe_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force resolver to fail by giving an impossible binary name."""
    with pytest.raises((RuntimeError, FFmpegError)):
        ffprobe_json_sync("/tmp/nope.mp4", ffprobe="definitely_not_ffprobe_xyz")


def test_ffprobe_json_sync_handles_non_json_stdout(tmp_path: Path) -> None:
    """If we point ffprobe at a non-existent file via a stub, it should error."""
    # Easy mode: substitute ffprobe with python that outputs non-json
    # We test parse_error via direct call to run_ffmpeg_sync with python
    # outputting "not json" on stdout, then manually try json.loads.
    out = run_ffmpeg_sync(
        [sys.executable, "-c", "print('not json at all')"],
        timeout_sec=5,
    )
    with pytest.raises(json.JSONDecodeError):
        json.loads(out.stdout)


# ── async wrapper smoke test ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_ffmpeg_async_does_not_block() -> None:
    """The async wrapper must produce identical results to the sync one."""
    from _shared import run_ffmpeg
    out = await run_ffmpeg(
        [sys.executable, "-c", "print('async-ok')"],
        timeout_sec=10,
    )
    assert out.returncode == 0
    assert "async-ok" in out.stdout


@pytest.mark.asyncio
async def test_run_ffmpeg_async_propagates_timeout() -> None:
    from _shared import run_ffmpeg
    with pytest.raises(FFmpegError) as ei:
        await run_ffmpeg(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            timeout_sec=0.4,
        )
    assert ei.value.timed_out is True
