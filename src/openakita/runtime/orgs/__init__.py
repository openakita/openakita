"""Runtime v2 organisation surfaces.

* **Org entity persistence** (P-RC-3): :class:`JsonOrgStore` /
  :class:`SqliteOrgStore` -- duck-typed contract list / get /
  create / patch / delete + close. Default JSON; opt into SQLite
  via ``ORGS_V2_BACKEND=sqlite``.
* **Org subsystems** (P-RC-9): ADR-0011''s six Protocol-typed
  subsystems.

  - P9.1 ships :class:`OrgBlackboard` -- three-tier shared
    memory -- plus the :class:`BlackboardBackendProtocol`
    abstraction, default :class:`JsonFileBlackboardBackend`,
    :class:`SqliteBlackboardBackend` and the
    ``get_default_blackboard_backend`` factory.
  - P9.2 ships :class:`ProjectStoreProtocol`, v2 project /
    task models, :class:`JsonProjectStore`, and
    :class:`SqliteProjectStore`. The
    ``get_default_project_store`` factory lands in P9.2c2.
"""

from __future__ import annotations

from .blackboard import (
    MAX_DEPT_MEMORIES,
    MAX_NODE_MEMORIES,
    MAX_ORG_MEMORIES,
    BlackboardBackendProtocol,
    JsonFileBlackboardBackend,
    OrgBlackboard,
    SqliteBlackboardBackend,
    get_default_blackboard_backend,
)
from .memory_models import MemoryScope, MemoryType, OrgMemoryEntry
from .project_models import (
    OrgProject,
    ProjectStatus,
    ProjectTask,
    ProjectType,
    TaskStatus,
    new_project_id,
    new_task_id,
)
from .project_store import (
    JsonProjectStore,
    ProjectStoreProtocol,
    SqliteProjectStore,
)
from .sqlite_store import SqliteOrgStore
from .store import JsonOrgStore, OrgNotFound, get_default_store, reset_default_store

__all__ = [
    "BlackboardBackendProtocol",
    "JsonFileBlackboardBackend",
    "JsonOrgStore",
    "JsonProjectStore",
    "MAX_DEPT_MEMORIES",
    "MAX_NODE_MEMORIES",
    "MAX_ORG_MEMORIES",
    "MemoryScope",
    "MemoryType",
    "OrgBlackboard",
    "OrgMemoryEntry",
    "OrgNotFound",
    "OrgProject",
    "ProjectStatus",
    "ProjectStoreProtocol",
    "ProjectTask",
    "ProjectType",
    "SqliteBlackboardBackend",
    "SqliteOrgStore",
    "SqliteProjectStore",
    "TaskStatus",
    "get_default_blackboard_backend",
    "get_default_store",
    "new_project_id",
    "new_task_id",
    "reset_default_store",
]
