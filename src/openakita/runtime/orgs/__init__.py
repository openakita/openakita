"""Runtime v2 organisation surfaces.

* **Org entity persistence** (P-RC-3): :class:`JsonOrgStore` /
  :class:`SqliteOrgStore` -- duck-typed contract list / get /
  create / patch / delete + close. Default JSON; opt into SQLite
  via ``ORGS_V2_BACKEND=sqlite``.
* **Org subsystems** (P-RC-9): ADR-0011''s six subsystems,
  each Protocol-typed (1-5 Protocols per subsystem; ~15
  Protocol surfaces total once P9.6 lands; 5 of 6 subsystems
  shipped through P9.5 -- Blackboard / ProjectStore /
  NodeScheduler / OrgCommandService / OrgManager).

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
  - P9.6 ships v2 :class:`OrgRuntime` (charter subsystem #6;
    largest of ADR-0011's six). P9.6a (this commit) lands
    the skeleton + three NEW Protocols
    (:class:`RuntimeStateProtocol`,
    :class:`NodeLifecycleProtocol`, :class:`EventBusProtocol`)
    + default in-memory backends + ``__init__`` accepting
    six reused Protocols (composition from P9.1 / P9.3 /
    P9.4 / P9.5) + the three new ones. OrgRuntime
    **implements** :class:`CommandRuntimeProtocol` (the P9.4
    contract; six methods stubbed -- bodies ride P9.6beta).
    See ADR-0014 for the budget revision driving the
    seven-sibling decomposition.
  - P9.6b ships the :class:`InMemoryEventBus` /
    :class:`WebSocketEventBus` real-backend pair + the
    ``get_default_event_bus`` factory in
    ``_runtime_event_bus.py`` (the Protocol contract is in
    ``runtime.py`` P9.6a0).
  - P9.6c ships :class:`CommandWatchdog` (v1
    ``_command_watchdog`` parity) + :class:`IdleProbeLoop`
    (v1 ``_idle_probe_loop`` parity) in
    ``_runtime_watchdog.py``; both are DI-driven async
    loops with start / stop / graceful-shutdown semantics.
  - P9.6d ships :class:`OrgLifecycleManager` -- org
    state-machine + DI-callback orchestrator for
    start / stop / pause / resume / restart / delete /
    health-check verbs (v1 ~18 lifecycle methods absorbed)
    + five org-state constants
    (``STATE_CREATED`` / ``ACTIVE`` / ``PAUSED`` /
    ``STOPPED`` / ``DELETED``) +
    :class:`IllegalOrgTransition` guard exception in
    ``_runtime_lifecycle.py``.
  - P9.6e ships :class:`CommandDispatchManager` --
    send_command / cancel_user_command /
    get_command_tracker_snapshot / has_active_delegations /
    get_active_root_intent + chain helpers (v1 ~22
    dispatch / tracker / chain methods absorbed; the
    cross-cutting v1 ``tracker`` x 254 + ``chain_id`` x 221
    references collapse to one focused manager) +
    ``_CommandTracker`` dataclass + four ``TRACKER_*``
    state constants in ``_runtime_dispatch.py``. This is
    the manager that the P9.6 runtime.py main class
    composes to satisfy the four ``CommandRuntimeProtocol``
    stub methods left by P9.6a.
"""

from __future__ import annotations

from ._runtime_event_bus import (
    InMemoryEventBus,
    WebSocketEventBus,
    get_default_event_bus,
)
from ._runtime_dispatch import (
    TRACKER_CANCELLED,
    TRACKER_DEADLOCK_STOPPED,
    TRACKER_FINALIZED,
    TRACKER_RUNNING,
    CommandDispatchManager,
)
from ._runtime_lifecycle import (
    STATE_ACTIVE,
    STATE_CREATED,
    STATE_DELETED,
    STATE_PAUSED,
    STATE_STOPPED,
    IllegalOrgTransition,
    OrgLifecycleManager,
)
from ._runtime_watchdog import CommandWatchdog, IdleProbeLoop
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
from .command_models import (
    ForwardTarget,
    OrgCommandConflict,
    OrgCommandError,
    OrgCommandRequest,
    OrgCommandResponse,
    OrgCommandSource,
    OrgCommandSurface,
    OrgOutputScope,
    default_scope_for_surface,
    new_command_id,
    origin_surface_label_cn,
)
from .command_service import (
    BrainProtocol,
    ChannelGatewayProtocol,
    CommandRuntimeProtocol,
    EventEmitterProtocol,
    OrgCommandService,
    OrgCommandServiceProtocol,
    OrgLookupProtocol,
    SessionManagerProtocol,
    get_command_service,
    set_command_service,
)
from .manager import (
    OrgFactoryProtocol,
    OrgLifecycleEmitterProtocol,
    OrgManager,
    OrgNameConflictError,
    OrgPersistenceProtocol,
    get_org_manager,
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
from .runtime import (
    EventBusProtocol,
    NodeLifecycleProtocol,
    OrgRuntime,
    RuntimeStateProtocol,
    get_runtime,
)
from .scheduler_models import NodeSchedule, ScheduleType, new_schedule_id
from .sqlite_store import SqliteOrgStore
from .store import JsonOrgStore, OrgNotFound, get_default_store, reset_default_store

__all__ = [
    "BlackboardBackendProtocol",
    "BrainProtocol",
    "CLEAN_THRESHOLD",
    "ChannelGatewayProtocol",
    "CommandWatchdog",
    "CommandDispatcher",
    "CommandDispatchManager",
    "CommandRuntimeProtocol",
    "EventEmitterProtocol",
    "FREQUENCY_MULTIPLIER",
    "ForwardTarget",
    "IdleProbeLoop",
    "IllegalOrgTransition",
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
    "OrgCommandConflict",
    "OrgCommandError",
    "OrgCommandRequest",
    "OrgCommandResponse",
    "OrgCommandService",
    "OrgCommandServiceProtocol",
    "OrgCommandSource",
    "OrgCommandSurface",
    "OrgFactoryProtocol",
    "OrgLifecycleManager",
    "OrgLifecycleEmitterProtocol",
    "OrgLookupProtocol",
    "OrgManager",
    "OrgMemoryEntry",
    "OrgNameConflictError",
    "OrgNodeScheduler",
    "OrgNotFound",
    "OrgOutputScope",
    "OrgPersistenceProtocol",
    "OrgProject",
    "ProjectStatus",
    "ProjectStoreProtocol",
    "ProjectTask",
    "ProjectType",
    "RECHECK_DELAY",
    "STATE_ACTIVE",
    "STATE_CREATED",
    "STATE_DELETED",
    "STATE_PAUSED",
    "STATE_STOPPED",
    "ScheduleStore",
    "ScheduleType",
    "SchedulerRuntimeProbe",
    "SessionManagerProtocol",
    "SqliteBlackboardBackend",
    "SqliteOrgStore",
    "SqliteProjectStore",
    "TRACKER_CANCELLED",
    "TRACKER_DEADLOCK_STOPPED",
    "TRACKER_FINALIZED",
    "TRACKER_RUNNING",
    "TaskStatus",
    "build_schedule_prompt",
    "compute_next_fire_time",
    "default_scope_for_surface",
    "get_command_service",
    "get_default_blackboard_backend",
    "get_default_project_store",
    "get_default_store",
    "get_org_manager",
    "get_default_event_bus",
    "get_runtime",
    "new_command_id",
    "new_project_id",
    "new_schedule_id",
    "new_task_id",
    "origin_surface_label_cn",
    "reset_default_project_stores",
    "reset_default_store",
    "set_command_service",
    "EventBusProtocol",
    "InMemoryEventBus",
    "NodeLifecycleProtocol",
    "OrgRuntime",
    "RuntimeStateProtocol",
    "WebSocketEventBus",
]
