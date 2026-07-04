"""Backwards-compat shim for the pre-ADR-0003 import path.

The tool executor moved to the ``openakita.agent`` subpackage (canonical home:
``openakita.agent.tools.ToolExecutor``) with ``openakita.core._tool_executor_legacy``
holding the legacy implementation. This thin module re-exports the canonical
:class:`ToolExecutor` so legacy import paths
(``from openakita.core.tool_executor import ToolExecutor``) and upstream tests
keep resolving.

Importing :mod:`openakita.agent.tools` first preserves the safe lazy import
order used elsewhere. See ``docs/follow-ups/skipped-items-roadmap.md``.
"""

from __future__ import annotations

from openakita.agent.tools import ToolExecutor

__all__ = ["ToolExecutor"]
