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
    task models, :class:`JsonProjectStore`,
    :class:`SqliteProjectStore`, and the
    ``get_default_project_store`` /
    ``reset_default_project_stores`` factory.
  - P9.3 ships v2 :class:`NodeSchedule` / :class:`ScheduleType`
    schedule models (P9.3a0), four Protocols
    (:class:`NodeSchedulerProtocol`,
    :class:`CommandDispatcher` -- the ADR-0011 cross-subsystem
    boundary; :class:`ScheduleStore`;
    :class:`SchedulerRuntimeProbe`), the
    :func:`compute_next_fire_time` pure helper (P-RC-9-PLAN
    section 5.2 1-ms parity gate), and the
    :class:`OrgNodeScheduler` skeleton (this commit, P9.3a;
    P9.3b lands the method bodies).
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
from .node_scheduler import (
    CLEAN_THRESHOLD,
    FREQUENCY_MULTIPLIER,
    MAX_FREQUENCY_FACTOR,
    RECHECK_DELAY,
    CommandDispatcher,
    NodeSchedulerProtocol,
    OrgNodeScheduler,
    SchedulerRuntimeProbe,
    ScheduleStore,
    build_schedule_prompt,
    compute_next_fire_time,
)
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
    get_default_project_store,
    reset_default_project_stores,
)
from .scheduler_models import NodeSchedule, ScheduleType, new_schedule_id
from .sqlite_store import SqliteOrgStore
from .store import JsonOrgStore, OrgNotFound, get_default_store, reset_default_store

__all__ = [
    "BlackboardBackendProtocol",
    "CLEAN_THRESHOLD",
    "CommandDispatcher",
    "FREQUENCY_MULTIPLIER",
    "JsonFileBlackboardBackend",
    "JsonOrgStore",
    "JsonProjectStore",
    "MAX_DEPT_MEMORIES",
    "MAX_FREQUENCY_FACTOR",
    "MAX_NODE_MEMORIES",
    "MAX_ORG_MEMORIES",
    "MemoryScope",
    "MemoryType",
    "NodeSchedule",
    "NodeSchedulerProtocol",
    "OrgBlackboard",
    "OrgMemoryEntry",
    "OrgNodeScheduler",
    "OrgNotFound",
    "OrgProject",
    "ProjectStatus",
    "ProjectStoreProtocol",
    "ProjectTask",
    "ProjectType",
    "RECHECK_DELAY",
    "ScheduleStore",
    "ScheduleType",
    "SchedulerRuntimeProbe",
    "SqliteBlackboardBackend",
    "SqliteOrgStore",
    "SqliteProjectStore",
    "TaskStatus",
    "build_schedule_prompt",
    "compute_next_fire_time",
    "get_default_blackboard_backend",
    "get_default_project_store",
    "get_default_store",
    "new_project_id",
    "new_schedule_id",
    "new_task_id",
    "reset_default_project_stores",
    "reset_default_store",
]
