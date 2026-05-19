"""Runtime v2 organisation surfaces.

* **Org entity persistence** (P-RC-3): :class:`JsonOrgStore` /
  :class:`SqliteOrgStore` -- duck-typed contract list / get /
  create / patch / delete + close. Default JSON; opt into SQLite
  via ``ORGS_V2_BACKEND=sqlite``.
* **Org subsystem models** (P-RC-9 P9.1a0): the shared
  :class:`MemoryScope` / :class:`MemoryType` /
  :class:`OrgMemoryEntry` dataclass used by the P9.1
  :class:`OrgBlackboard` (added in the next commit) and by the
  parity / contract test harnesses.
"""

from __future__ import annotations

from .memory_models import MemoryScope, MemoryType, OrgMemoryEntry
from .sqlite_store import SqliteOrgStore
from .store import JsonOrgStore, OrgNotFound, get_default_store, reset_default_store

__all__ = [
    "JsonOrgStore",
    "MemoryScope",
    "MemoryType",
    "OrgMemoryEntry",
    "OrgNotFound",
    "SqliteOrgStore",
    "get_default_store",
    "reset_default_store",
]
