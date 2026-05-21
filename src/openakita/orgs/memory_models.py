"""Memory entry models for the v2 OrgBlackboard (P-RC-9 P9.1).

Duplicates v1 ``openakita.orgs.models`` enums + dataclass under
the v2 namespace so ``runtime/orgs/blackboard.py`` has zero
``openakita.orgs.*`` imports (P-RC-9-PLAN section 0.3 invariant)
and v1 can be deleted wholesale at P9.9. Parity is enforced
byte-for-byte via ``to_dict()`` round-trip; the dataclass type
identity intentionally differs across the namespace split.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from openakita.memory.types import normalize_tags

__all__ = [
    "MemoryScope",
    "MemoryType",
    "OrgMemoryEntry",
    "new_memory_id",
    "now_iso",
]


def new_memory_id(prefix: str = "mem_") -> str:
    """Mint a fresh memory id; mirrors v1 ``_new_id`` semantics."""
    short = uuid.uuid4().hex[:12]
    return f"{prefix}{short}" if prefix else short


def now_iso() -> str:
    """ISO-8601 UTC timestamp string; mirrors v1 ``_now_iso``."""
    return datetime.now(UTC).isoformat()


class MemoryScope(StrEnum):
    """Three-tier blackboard scope."""

    ORG = "org"
    DEPARTMENT = "department"
    NODE = "node"


class MemoryType(StrEnum):
    """Semantic kind of memory entry."""

    FACT = "fact"
    DECISION = "decision"
    RULE = "rule"
    PROGRESS = "progress"
    LESSON = "lesson"
    RESOURCE = "resource"


@dataclass
class OrgMemoryEntry:
    """v2 memory entry; ``to_dict`` shape matches v1 byte-for-byte."""

    id: str = field(default_factory=lambda: new_memory_id("mem_"))
    org_id: str = ""
    scope: MemoryScope = MemoryScope.ORG
    scope_owner: str = ""
    memory_type: MemoryType = MemoryType.FACT
    content: str = ""
    source_node: str = ""
    source_message_id: str | None = None
    tags: list[str] = field(default_factory=list)
    attachments: list[dict] = field(default_factory=list)
    importance: float = 0.5
    ttl_hours: int | None = None
    created_at: str = field(default_factory=now_iso)
    last_accessed_at: str = field(default_factory=now_iso)
    access_count: int = 0

    def __post_init__(self) -> None:
        self.tags = normalize_tags(self.tags)
        try:
            self.importance = float(self.importance)
        except (ValueError, TypeError):
            self.importance = 0.5
        self.importance = max(0.0, min(1.0, self.importance))
        if self.ttl_hours is not None:
            try:
                self.ttl_hours = int(self.ttl_hours)
            except (ValueError, TypeError):
                self.ttl_hours = None

    def to_dict(self) -> dict:
        d: dict = {
            "id": self.id,
            "org_id": self.org_id,
            "scope": self.scope.value,
            "scope_owner": self.scope_owner,
            "memory_type": self.memory_type.value,
            "content": self.content,
            "source_node": self.source_node,
            "source_message_id": self.source_message_id,
            "tags": self.tags,
            "importance": self.importance,
            "ttl_hours": self.ttl_hours,
            "created_at": self.created_at,
            "last_accessed_at": self.last_accessed_at,
            "access_count": self.access_count,
        }
        if self.attachments:
            d["attachments"] = self.attachments
        return d

    @classmethod
    def from_dict(cls, d: dict) -> OrgMemoryEntry:
        d = dict(d)
        if "scope" in d and isinstance(d["scope"], str):
            try:
                d["scope"] = MemoryScope(d["scope"])
            except ValueError:
                d["scope"] = MemoryScope.ORG
        if "memory_type" in d and isinstance(d["memory_type"], str):
            try:
                d["memory_type"] = MemoryType(d["memory_type"])
            except ValueError:
                d["memory_type"] = MemoryType.FACT
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
