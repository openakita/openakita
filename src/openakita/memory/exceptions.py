"""Memory subsystem exceptions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MemoryStorageUnavailable(Exception):
    """Raised when the memory database cannot be opened safely.

    The caller should degrade the memory subsystem instead of failing backend
    startup.
    """

    reason: str
    details: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        if self.details:
            return f"{self.reason}: {self.details}"
        return self.reason
