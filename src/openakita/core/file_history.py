"""Re-export shim — file history manager moved to ``agent.file_history``.

Canonical home: :mod:`openakita.agent.file_history`. Shim
preserved at the legacy path until Phase 8, per ADR-0003 and
``docs/revamp/core_audit.md``.

Do not add new code here.
"""

from __future__ import annotations

from openakita.agent.file_history import (
    HISTORY_BASE_DIR,
    MAX_SNAPSHOTS,
    BackupInfo,
    FileHistoryManager,
    FileSnapshot,
)

__all__ = [
    "HISTORY_BASE_DIR",
    "MAX_SNAPSHOTS",
    "BackupInfo",
    "FileHistoryManager",
    "FileSnapshot",
]
