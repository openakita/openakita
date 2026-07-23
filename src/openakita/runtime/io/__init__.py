"""Tool-output truncation and overflow persistence primitives."""

from __future__ import annotations

from .overflow import (
    cleanup_overflow_files,
    get_overflow_dir,
    save_overflow,
)
from .truncate import (
    DEFAULT_TOOL_RESULT_MAX_CHARS,
    MAX_TOOL_RESULT_CHARS,
    OVERFLOW_MARKER,
    smart_truncate,
)

__all__ = [
    "DEFAULT_TOOL_RESULT_MAX_CHARS",
    "MAX_TOOL_RESULT_CHARS",
    "OVERFLOW_MARKER",
    "cleanup_overflow_files",
    "get_overflow_dir",
    "save_overflow",
    "smart_truncate",
]
