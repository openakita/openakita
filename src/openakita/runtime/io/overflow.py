"""Tool overflow file persistence and cleanup.

Pure I/O helpers: ``save_overflow``
writes the dropped tool output to a timestamped file under
``data/tool_overflow/``; ``cleanup_overflow_files`` evicts the oldest
files beyond a configurable cap to bound disk growth.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from openakita.config import settings

logger = logging.getLogger(__name__)

_DEFAULT_OVERFLOW_DIR = Path("data/tool_overflow")
_DEFAULT_OVERFLOW_MAX_FILES = 200


def get_overflow_dir() -> Path:
    """Return the directory where overflow files land.

    Returns the default ``data/tool_overflow`` -- callers that need a
    test-isolated dir construct their own paths and pass them into
    :func:`save_overflow` / :func:`cleanup_overflow_files`.
    """
    return _DEFAULT_OVERFLOW_DIR


def get_overflow_max_files() -> int:
    """Cap on the number of overflow files kept on disk.

    Reads ``settings.tool_overflow_max_files`` with a floor of 10.
    Implements the runtime overflow-file limit contract.
    """
    try:
        return max(
            10,
            int(getattr(settings, "tool_overflow_max_files", _DEFAULT_OVERFLOW_MAX_FILES)),
        )
    except (TypeError, ValueError):
        return _DEFAULT_OVERFLOW_MAX_FILES


def save_overflow(
    tool_name: str,
    content: str,
    *,
    directory: Path | None = None,
    max_files: int | None = None,
) -> str:
    """Save ``content`` to a timestamped overflow file; return the path.

    Best-effort: any exception is logged and a placeholder string is
    returned so the calling tool result still renders. After every
    write the directory is pruned to ``max_files`` (oldest-first) so
    runaway tools cannot fill the disk.
    """
    target_dir = directory or _DEFAULT_OVERFLOW_DIR
    cap = max_files if max_files is not None else get_overflow_max_files()
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filepath = target_dir / f"{tool_name}_{ts}.txt"
        filepath.write_text(content, encoding="utf-8")
        cleanup_overflow_files(target_dir, cap)
        logger.info("[overflow] saved %d chars to %s", len(content), filepath)
        return str(filepath)
    except Exception as exc:
        logger.warning("[overflow] failed to save: %s", exc)
        return "(overflow save failed)"


def cleanup_overflow_files(directory: Path, max_files: int) -> None:
    """Evict the oldest ``*.txt`` files in ``directory`` beyond ``max_files``."""
    try:
        files = sorted(directory.glob("*.txt"), key=lambda f: f.stat().st_mtime)
        if len(files) > max_files:
            for f in files[: len(files) - max_files]:
                f.unlink(missing_ok=True)
    except Exception:
        pass


__all__ = [
    "cleanup_overflow_files",
    "get_overflow_dir",
    "get_overflow_max_files",
    "save_overflow",
]
