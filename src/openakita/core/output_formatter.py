"""Re-export shim — output formatters moved to ``agent.output_formatter``.

The canonical home is now :mod:`openakita.agent.output_formatter`,
per ADR-0003 and the Phase 2 sub-commit plan in
``docs/revamp/core_audit.md``. This shim keeps every existing import
path working until Phase 8 mechanically removes the legacy ``core/``
tree.

Do not add new code here.
"""

from __future__ import annotations

from openakita.agent.output_formatter import (
    JSONFormatter,
    OutputFormatter,
    StreamJSONFormatter,
    TextFormatter,
    create_formatter,
)

__all__ = [
    "JSONFormatter",
    "OutputFormatter",
    "StreamJSONFormatter",
    "TextFormatter",
    "create_formatter",
]
