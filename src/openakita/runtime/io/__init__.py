"""V2 IO helpers extracted from ``core.tool_executor`` (continuation plan section 5).

The legacy ToolExecutor carried two pure helpers -- ``smart_truncate``
for compressing oversized tool output and ``save_overflow`` /
``_cleanup_overflow_files`` for persisting the dropped content to a
sidecar file -- as module-level functions in
``openakita.core.tool_executor``. They were imported by:

* the legacy ToolExecutor itself;
* a few tool handlers that needed to truncate their own output;
* the v2 facade ``openakita.agent.tools`` (re-export).

This package is the v2 home for those primitives.
"""

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
