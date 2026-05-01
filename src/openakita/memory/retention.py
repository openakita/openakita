"""Retention policy helpers for semantic memories."""

from __future__ import annotations

from datetime import datetime, timedelta

from .types import MemoryPriority, SemanticMemory

PRIORITY_TTL: dict[MemoryPriority, timedelta | None] = {
    MemoryPriority.TRANSIENT: timedelta(days=1),
    MemoryPriority.SHORT_TERM: timedelta(days=3),
    MemoryPriority.LONG_TERM: timedelta(days=30),
    MemoryPriority.PERMANENT: None,
}

DURATION_TTL: dict[str, timedelta | None] = {
    "permanent": None,
    "7d": timedelta(days=7),
    "24h": timedelta(hours=24),
    "session": timedelta(hours=2),
}


def apply_retention(memory: SemanticMemory, duration: str | None = None) -> None:
    """Set ``expires_at`` from an explicit duration or the memory priority."""
    if memory.expires_at is not None:
        return

    if duration and duration in DURATION_TTL:
        delta = DURATION_TTL[duration]
    else:
        delta = PRIORITY_TTL.get(memory.priority)

    memory.expires_at = (datetime.now() + delta) if delta else None
