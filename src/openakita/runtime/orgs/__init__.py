"""Runtime v2 organisation surfaces.

* **Org entity persistence** (P-RC-3): :class:`JsonOrgStore` /
  :class:`SqliteOrgStore` -- duck-typed contract list / get /
  create / patch / delete + close. Default JSON; opt into SQLite
  via ``ORGS_V2_BACKEND=sqlite``.
* **Org subsystems** (P-RC-9): ADR-0011''s six Protocol-typed
  subsystems. P9.1 ships :class:`OrgBlackboard` -- three-tier
  shared memory -- plus the :class:`BlackboardBackendProtocol`
  abstraction and default :class:`JsonFileBlackboardBackend`.
  P9.1b adds :class:`SqliteBlackboardBackend` and the
  ``get_default_blackboard`` factory.
"""

from __future__ import annotations

from .blackboard import (
    MAX_DEPT_MEMORIES,
    MAX_NODE_MEMORIES,
    MAX_ORG_MEMORIES,
    BlackboardBackendProtocol,
    JsonFileBlackboardBackend,
    OrgBlackboard,
)
from .memory_models import MemoryScope, MemoryType, OrgMemoryEntry
from .sqlite_store import SqliteOrgStore
from .store import JsonOrgStore, OrgNotFound, get_default_store, reset_default_store

__all__ = [
    "BlackboardBackendProtocol",
    "JsonFileBlackboardBackend",
    "JsonOrgStore",
    "MAX_DEPT_MEMORIES",
    "MAX_NODE_MEMORIES",
    "MAX_ORG_MEMORIES",
    "MemoryScope",
    "MemoryType",
    "OrgBlackboard",
    "OrgMemoryEntry",
    "OrgNotFound",
    "SqliteOrgStore",
    "get_default_store",
    "reset_default_store",
]
