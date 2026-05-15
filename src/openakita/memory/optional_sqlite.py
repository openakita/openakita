"""Shared helper for optional SQLite-backed subsystems."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TypeVar

from .exceptions import MemoryStorageUnavailable

logger = logging.getLogger(__name__)

T = TypeVar("T")


class OptionalSQLiteSubsystem:
    """Construct SQLite subsystems without letting storage failure kill boot."""

    def __init__(self, name: str, factory: Callable[[], T], fallback: Callable[[Exception], T]):
        self.name = name
        self.degraded = False
        self.reason: str | None = None
        self.details: str | None = None
        try:
            self.value = factory()
        except MemoryStorageUnavailable as e:
            self.degraded = True
            self.reason = e.reason
            self.details = e.details
            logger.warning(
                "[%s] SQLite subsystem degraded: %s %s",
                name,
                e.reason,
                e.details or "",
            )
            self.value = fallback(e)
