"""Re-export shim — working facts moved to ``agent.working_facts``.

The canonical home of :func:`extract_working_facts`,
:func:`merge_working_facts`, and :func:`format_working_facts` is now
:mod:`openakita.agent.working_facts`, per ADR-0003 and the Phase 2
sub-commit plan in ``docs/revamp/core_audit.md``. This shim keeps every
existing import path working — the legacy callers in
``sessions/session.py``, ``core/agent.py``, ``prompt/builder.py`` and
the test suite — until Phase 8 mechanically removes the legacy
``core/`` package.

Do not add new code here.
"""

from __future__ import annotations

from openakita.agent.working_facts import (
    extract_working_facts,
    format_working_facts,
    merge_working_facts,
)

__all__ = [
    "extract_working_facts",
    "format_working_facts",
    "merge_working_facts",
]
