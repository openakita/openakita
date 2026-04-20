"""ffmpeg / ffprobe execution helpers with mandatory timeouts.

This module is the **execution** counterpart to
:mod:`openakita_plugin_sdk.contrib.render_pipeline` (which only **builds**
command lists).  Plugins should always call ``run_ffmpeg`` instead of
``subprocess.run([...ffmpeg...])`` directly so that:

- A timeout is **always** present (no more hung renders on huge inputs — the
  exact failure mode reported in ``video-use`` ``transcribe.py:75-82`` and
  ``timeline_view.py:267-268``).
- Output is captured uniformly so :class:`FFmpegError` carries enough context
  for ``ErrorCoach`` to render a helpful message.
- The binary is resolved via :func:`resolve_binary` which raises a
  user-friendly ``RuntimeError`` if ffmpeg is not on ``PATH`` (so the
  ``dep_gate.js`` UI can prompt installation).

Design rules (audit3):

- **No mandatory timeout default** — the parameter is required and validated
  to be positive, mirroring CutClaw ``audio/madmom_api.py`` discipline.
- **Async by default** (uses ``asyncio.to_thread``) so a long render does
  not block the event loop.  A sync helper :func:`run_ffmpeg_sync` is kept
  for non-async callers.
- **Zero extra deps**: stdlib only (``subprocess``, ``shutil``, ``json``,
  ``asyncio``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "FFmpegError",
    "FFmpegResult",
    "ffprobe_json",
    "ffprobe_json_sync",
    "resolve_binary",
    "run_ffmpeg",
    "run_ffmpeg_sync",
]


class FFmpegError(RuntimeError):
    """Raised on ffmpeg / ffprobe failure or timeout.

    Attributes:
        cmd: The argv list that was run.
        returncode: Process return code (or ``None`` on timeout).
        stderr_tail: Last ~2KB of stderr (helpful for ErrorCoach matching).
        timed_out: ``True`` if the call hit the timeout.
    """

    def __init__(
        self,
        message: str,
        *,
        cmd: list[str],
        returncode: int | None,
        stderr_tail: str = "",
        timed_out: bool = False,
    ) -> None:
        super().__init__(message)
        self.cmd = list(cmd)
        self.returncode = returncode
        self.stderr_tail = stderr_tail
        self.timed_out = timed_out


@dataclass(frozen=True)
class FFmpegResult:
    """Successful ffmpeg run."""

    cmd: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration_sec: float


def resolve_binary(name: str) -> str:
    """Find a binary on ``PATH`` and return its absolute path.

    Args:
        name: Binary name (``"ffmpeg"``, ``"ffprobe"``) or absolute path.

    Returns:
        Absolute path string suitable for ``subprocess`` argv[0].

    Raises:
        RuntimeError: If the binary is not absolute and not found on PATH.
            The error message is actionable so plugins can surface it via
            ``ErrorCoach`` directly.
    """
    if not name or not isinstance(name, str):
        raise ValueError(f"binary name must be a non-empty string, got {name!r}")
    p = Path(name)
    if p.is_absolute():
        if not p.exists():
            raise RuntimeError(
                f"{name} does not exist — verify the path is correct.",
            )
        return name
    found = shutil.which(name)
    if not found:
        raise RuntimeError(
            f"{name} not found in PATH — install it via the dependency gate "
            "(see docs/dependency-gate.md) or add it to PATH manually.",
        )
    return found


def _validate_timeout(timeout_sec: float) -> float:
    if timeout_sec is None or not isinstance(timeout_sec, (int, float)):
        raise ValueError("timeout_sec is required (must be a positive number)")
    if timeout_sec <= 0:
        raise ValueError(f"timeout_sec must be > 0, got {timeout_sec}")
    return float(timeout_sec)


def _tail(text: str | bytes | None, limit: int = 2048) -> str:
    if text is None:
        return ""
    s = text.decode("utf-8", errors="replace") if isinstance(text, bytes) else text
    return s[-limit:] if len(s) > limit else s


def run_ffmpeg_sync(
    cmd: list[str],
    *,
    timeout_sec: float,
    check: bool = True,
    capture: bool = True,
    input_bytes: bytes | None = None,
) -> FFmpegResult:
    """Synchronous ffmpeg/ffprobe runner with mandatory timeout.

    Args:
        cmd: Full argv list (argv[0] should already be resolved via
            :func:`resolve_binary` or just ``"ffmpeg"`` for ``shutil.which``
            to handle).
        timeout_sec: **Required.**  Hard wall-clock timeout in seconds.
            Raises ``ValueError`` if missing or non-positive.
        check: When True (default), non-zero exit raises :class:`FFmpegError`.
        capture: Capture stdout/stderr (default True).  Set False for
            interactive use (rare for ffmpeg in a server context).
        input_bytes: Optional bytes piped to stdin.

    Returns:
        :class:`FFmpegResult` on success.

    Raises:
        ValueError: If ``timeout_sec`` is missing or invalid.
        FFmpegError: On timeout or, if ``check=True``, non-zero exit.
    """
    timeout = _validate_timeout(timeout_sec)
    if not cmd:
        raise ValueError("cmd must not be empty")

    import time
    started = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            input=input_bytes,
            capture_output=capture,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise FFmpegError(
            f"{cmd[0]} timed out after {timeout:.1f}s",
            cmd=cmd,
            returncode=None,
            stderr_tail=_tail(e.stderr),
            timed_out=True,
        ) from e
    except FileNotFoundError as e:
        raise FFmpegError(
            f"binary not found: {cmd[0]} — {e}",
            cmd=cmd,
            returncode=None,
        ) from e

    elapsed = time.monotonic() - started
    stdout = (proc.stdout or b"").decode("utf-8", errors="replace") if isinstance(proc.stdout, bytes) else (proc.stdout or "")
    stderr = (proc.stderr or b"").decode("utf-8", errors="replace") if isinstance(proc.stderr, bytes) else (proc.stderr or "")

    if check and proc.returncode != 0:
        raise FFmpegError(
            f"{cmd[0]} exited with {proc.returncode}",
            cmd=cmd,
            returncode=proc.returncode,
            stderr_tail=_tail(stderr),
        )
    return FFmpegResult(
        cmd=list(cmd),
        returncode=proc.returncode,
        stdout=stdout,
        stderr=stderr,
        duration_sec=elapsed,
    )


async def run_ffmpeg(
    cmd: list[str],
    *,
    timeout_sec: float,
    check: bool = True,
    capture: bool = True,
    input_bytes: bytes | None = None,
) -> FFmpegResult:
    """Async wrapper around :func:`run_ffmpeg_sync` (uses ``asyncio.to_thread``).

    Identical contract to :func:`run_ffmpeg_sync` but does not block the
    event loop.  Plugins should prefer this in async handlers.
    """
    return await asyncio.to_thread(
        run_ffmpeg_sync,
        cmd,
        timeout_sec=timeout_sec,
        check=check,
        capture=capture,
        input_bytes=input_bytes,
    )


def _ffprobe_argv(media_path: str | Path, ffprobe: str) -> list[str]:
    bin_path = resolve_binary(ffprobe)
    return [
        bin_path,
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(media_path),
    ]


def ffprobe_json_sync(
    media_path: str | Path,
    *,
    timeout_sec: float = 15.0,
    ffprobe: str = "ffprobe",
) -> dict[str, Any]:
    """Synchronous ffprobe → parsed JSON dict.

    Args:
        media_path: Path to the media file.
        timeout_sec: Defaults to 15s (probing is fast); still capped.
        ffprobe: Binary name or absolute path.

    Returns:
        Parsed ffprobe JSON (``{"format": {...}, "streams": [...]}``).
        Returns an empty dict if ffprobe succeeds but produces no JSON.

    Raises:
        FFmpegError: On non-zero exit, timeout, or missing binary.
    """
    cmd = _ffprobe_argv(media_path, ffprobe)
    result = run_ffmpeg_sync(cmd, timeout_sec=timeout_sec, check=True, capture=True)
    if not result.stdout.strip():
        return {}
    try:
        return json.loads(result.stdout)
    except (ValueError, TypeError) as e:
        raise FFmpegError(
            f"ffprobe returned non-JSON output: {e}",
            cmd=cmd,
            returncode=result.returncode,
            stderr_tail=_tail(result.stderr),
        ) from e


async def ffprobe_json(
    media_path: str | Path,
    *,
    timeout_sec: float = 15.0,
    ffprobe: str = "ffprobe",
) -> dict[str, Any]:
    """Async wrapper around :func:`ffprobe_json_sync`."""
    return await asyncio.to_thread(
        ffprobe_json_sync,
        media_path,
        timeout_sec=timeout_sec,
        ffprobe=ffprobe,
    )
