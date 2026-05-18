"""OpenAkita v2 agent package.

Replaces the legacy ``src/openakita/core/`` per ADR-0003. The package
is populated incrementally during Phase 2; the per-file plan lives in
``docs/revamp/core_audit.md``.

Public symbols are exported lazily as their modules land. The
canonical :class:`Agent` and :class:`AgentState` will be re-exported
from :mod:`openakita.agent.facade` once the rewrite slices land.
"""

from __future__ import annotations

from .errors import UserCancelledError
from .identity import Identity
from .output_formatter import (
    JSONFormatter,
    OutputFormatter,
    StreamJSONFormatter,
    TextFormatter,
    create_formatter,
)
from .output_guard import (
    CODE_EXEC_TOOLS,
    DISCLAIMER_TEXT,
    detect_numeric_output,
    detect_numeric_task,
    validate_no_fabricated_numbers,
)
from .working_facts import (
    extract_working_facts,
    format_working_facts,
    merge_working_facts,
)

__all__ = [
    "CODE_EXEC_TOOLS",
    "DISCLAIMER_TEXT",
    "Identity",
    "JSONFormatter",
    "OutputFormatter",
    "StreamJSONFormatter",
    "TextFormatter",
    "UserCancelledError",
    "create_formatter",
    "detect_numeric_output",
    "detect_numeric_task",
    "extract_working_facts",
    "format_working_facts",
    "merge_working_facts",
    "validate_no_fabricated_numbers",
]
