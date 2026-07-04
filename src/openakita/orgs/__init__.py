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
  - P9.6c ships :class:`IdleProbeLoop` (v1
    ``_idle_probe_loop`` parity) in ``_runtime_watchdog.py``;
    DI-driven async loop with start / stop /
    graceful-shutdown semantics. (``CommandWatchdog`` was
    also shipped in P9.6c but removed in Sprint-9 -- the
    supervisor's :class:`StallDetector` now drives stuck
    detection on LLM-evaluated progress ledger signals.)
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
  - P9.6f1 ships :class:`AgentCache` +
    :class:`AgentBuilderProtocol` + :class:`AgentSpec`
    dataclass + :class:`ProfileResolver` in
    ``_runtime_agent_pipeline.py`` (~275 LOC). This is the
    agent-build / agent-cache scaffolding the executor
    (P9.6f2) consumes. v1 ``_get_or_create_agent`` /
    ``_node_agents`` dict / ``evict_node_agent`` /
    ``_build_profile_for_node`` / ``_get_shared_profile`` /
    ``_resolve_org_workspace`` / ``_prepare_unattended_session``
    (~150 v1 LOC) collapse here.
- P9.6f2 ships :class:`AgentPipelineExecutor` in the same
  sibling -- the activate-and-run loop. Replaces v1
  ``_activate_and_run`` + ``_activate_and_run_inner``
  (556 LOC) + ``_run_agent_task`` + ``_emit_llm_usage`` +
  ``_pause_org_for_quota`` + ``_is_quota_auth_error``
  (~800 v1 LOC) with one ~180 LOC class. Detects quota /
  auth errors and pauses the org via injected callback;
  emits ``agent_run_started`` / ``agent_run_finished`` /
  ``agent_run_failed`` / ``org_paused_quota`` /
  ``llm_usage`` events through the bus.
- P9.6g ships :class:`NodeStatusController` +
  :class:`NodeMessageRouter` + ``format_incoming_message``
  + ``is_stop_intent`` in ``_runtime_node_lifecycle.py``
  (~330 LOC). Lifts v1 ``_on_node_message`` (175 LOC) +
  ``_format_incoming_message`` (96 LOC) +
  ``_drain_node_pending`` (86 LOC) + ``_post_task_hook``
  (81 LOC) + ``_set_node_status`` / ``set_node_status`` /
  ``_mark_effective_action`` / ``_try_route_to_clone`` /
  ``_make_message_handler`` / ``_register_clone_in_messenger``
  / ``_on_inbound_for_node`` / ``_is_stop_intent``
  / ``evict_node_agent`` / ``_connect_node_mcp_servers``
  (~600 v1 LOC) into two focused classes + 4
  ``STATUS_*`` constants. Routes inbound messages through
  stop-intent detection, busy-queueing, agent pipeline
  delivery, and post-task hook orchestration.
- P9.6h1 ships :class:`PluginAssetRecorder` +
  :class:`ToolHandlerBridge` + :class:`PluginAsset`
  dataclass + helpers (``safe_asset_filename`` /
  ``ext_for_url`` / ``is_plugin_tool`` /
  ``plugin_id_for_tool``) in ``_runtime_plugin_assets.py``.
  Lifts v1 ``_record_plugin_asset_output`` (349 LOC) +
  ``_register_org_tool_handler`` (161 LOC) + 6 smaller
  helpers (~570 v1 LOC) into ~375 v2 LOC. The recorder
  emits ``plugin_asset_recorded`` events through the bus;
  the bridge adapts the legacy ``handle_org_tool``
  callable. P9.6h1b appended :class:`PluginAssetRecorder` (v1
  ``_record_plugin_asset_output`` 349 LOC -> ~120 v2
  LOC). P9.6h2 appended :class:`FileOutputRegistry` +
  :class:`TaskDeliverySynthesizer` + react-trace helpers
  (``react_trace_has_tool`` / ``collect_tool_stats_from_trace``
  / ``extract_accepted_chain_ids``) -- v1
  ``_register_file_output`` (156 LOC) +
  ``_record_file_output`` (101 LOC) +
  ``_synthesize_task_delivered_to_parent`` (107 LOC) +
  ``_react_trace_has_tool`` / ``_collect_tool_stats_from_trace``
  / ``_extract_accepted_chain_ids`` (~110 LOC) ~474 v1
  LOC absorbed.
- P9.6i wires :class:`CommandDispatchManager` into
  :class:`OrgRuntime.__init__` (new ``dispatch`` DI
  param; defaults to in-process construction against
  the injected command service / lookup / event bus).
  The 4 :class:`CommandRuntimeProtocol` methods
  (``send_command`` / ``cancel_user_command`` /
  ``has_active_delegations`` /
  ``get_command_tracker_snapshot``) are now real
  delegations -- ``raise NotImplementedError`` is gone.
  After this commit P9.6beta closes; P9.6gamma
  (parity 20 fixtures + ~25 contract cases + G-RC-9.6
  mini-gate) rides the next turn.
"""

from __future__ import annotations

from ._default_agent_builder import BuilderUnavailable, DefaultAgentBuilder
from ._runtime_agent_pipeline import (
    MAX_DISPATCH_BLOCKS,
    MAX_DISPATCH_DEPTH,
    ORG_STATE_ACTIVE,
    ORG_STATE_PAUSED,
    AgentBuilderProtocol,
    AgentCache,
    AgentPipelineExecutor,
    AgentSpec,
    ProfileResolver,
    current_command_id_var,
    dispatch_depth_var,
)
from ._runtime_dispatch import (
    TRACKER_CANCELLED,
    TRACKER_DEADLOCK_STOPPED,
    TRACKER_FINALIZED,
    TRACKER_RUNNING,
    CommandDispatchManager,
)
from ._runtime_event_bus import (
    InMemoryEventBus,
    WebSocketEventBus,
    get_default_event_bus,
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
from ._runtime_node_lifecycle import (
    STATUS_BUSY,
    STATUS_ERROR,
    STATUS_IDLE,
    STATUS_STOPPED,
    NodeMessageRouter,
    NodeStatusController,
    format_incoming_message,
    is_stop_intent,
)
from ._runtime_plugin_assets import (
    FileOutput,
    FileOutputRegistry,
    PluginAsset,
    PluginAssetRecorder,
    SynthesizedDelivery,
    TaskDeliverySynthesizer,
    ToolHandlerBridge,
    collect_tool_stats_from_trace,
    ext_for_url,
    extract_accepted_chain_ids,
    is_plugin_tool,
    plugin_id_for_tool,
    react_trace_has_tool,
    safe_asset_filename,
)
from ._runtime_templates import (
    build_workbench_templates,
    ensure_builtin_templates,
    list_avatar_presets,
)
from ._runtime_watchdog import IdleProbeLoop
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
from .org_models import (
    EdgeType,
    NodeStatus,
    Organization,
    OrgEdge,
    OrgNode,
    OrgStatus,
    UserPersona,
    infer_agent_profile_id_for_node,
    new_org_id,
    now_iso,
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
from .store import (
    JsonOrgStore,
    OrgNotFound,
    get_default_store,
    reset_default_store,
    set_default_org_manager,
)

__all__ = [
    "AgentBuilderProtocol",
    "AgentCache",
    "AgentPipelineExecutor",
    "AgentSpec",
    "BlackboardBackendProtocol",
    "BrainProtocol",
    "BuilderUnavailable",
    "CLEAN_THRESHOLD",
    "ChannelGatewayProtocol",
    "CommandDispatchManager",
    "CommandDispatcher",
    "CommandRuntimeProtocol",
    "DefaultAgentBuilder",
    "EdgeType",
    "EventBusProtocol",
    "EventEmitterProtocol",
    "FREQUENCY_MULTIPLIER",
    "FileOutput",
    "FileOutputRegistry",
    "ForwardTarget",
    "IdleProbeLoop",
    "IllegalOrgTransition",
    "InMemoryEventBus",
    "JsonFileBlackboardBackend",
    "JsonOrgStore",
    "JsonProjectStore",
    "MAX_DEPT_MEMORIES",
    "MAX_DISPATCH_BLOCKS",
    "MAX_DISPATCH_DEPTH",
    "MAX_FREQUENCY_FACTOR",
    "MAX_NODE_MEMORIES",
    "MAX_ORG_MEMORIES",
    "MemoryScope",
    "MemoryType",
    "NodeLifecycleProtocol",
    "NodeMessageRouter",
    "NodeSchedule",
    "NodeSchedulerProtocol",
    "NodeStatus",
    "NodeStatusController",
    "ORG_STATE_ACTIVE",
    "ORG_STATE_PAUSED",
    "OrgBlackboard",
    "OrgCommandConflict",
    "OrgCommandError",
    "OrgCommandRequest",
    "OrgCommandResponse",
    "OrgCommandService",
    "OrgCommandServiceProtocol",
    "OrgCommandSource",
    "OrgCommandSurface",
    "OrgEdge",
    "OrgFactoryProtocol",
    "OrgLifecycleEmitterProtocol",
    "OrgLifecycleManager",
    "OrgLookupProtocol",
    "OrgManager",
    "OrgMemoryEntry",
    "OrgNameConflictError",
    "OrgNode",
    "OrgNodeScheduler",
    "OrgNotFound",
    "OrgOutputScope",
    "OrgPersistenceProtocol",
    "OrgProject",
    "OrgRuntime",
    "OrgStatus",
    "Organization",
    "PluginAsset",
    "PluginAssetRecorder",
    "ProfileResolver",
    "ProjectStatus",
    "ProjectStoreProtocol",
    "ProjectTask",
    "ProjectType",
    "RECHECK_DELAY",
    "RuntimeStateProtocol",
    "STATE_ACTIVE",
    "STATE_CREATED",
    "STATE_DELETED",
    "STATE_PAUSED",
    "STATE_STOPPED",
    "STATUS_BUSY",
    "STATUS_ERROR",
    "STATUS_IDLE",
    "STATUS_STOPPED",
    "ScheduleStore",
    "ScheduleType",
    "SchedulerRuntimeProbe",
    "SessionManagerProtocol",
    "SqliteBlackboardBackend",
    "SqliteOrgStore",
    "SqliteProjectStore",
    "SynthesizedDelivery",
    "TRACKER_CANCELLED",
    "TRACKER_DEADLOCK_STOPPED",
    "TRACKER_FINALIZED",
    "TRACKER_RUNNING",
    "TaskDeliverySynthesizer",
    "TaskStatus",
    "ToolHandlerBridge",
    "UserPersona",
    "WebSocketEventBus",
    "build_schedule_prompt",
    "build_workbench_templates",
    "collect_tool_stats_from_trace",
    "compute_next_fire_time",
    "current_command_id_var",
    "default_scope_for_surface",
    "dispatch_depth_var",
    "ensure_builtin_templates",
    "ext_for_url",
    "extract_accepted_chain_ids",
    "format_incoming_message",
    "get_command_service",
    "get_default_blackboard_backend",
    "get_default_event_bus",
    "get_default_project_store",
    "get_default_store",
    "get_org_manager",
    "get_runtime",
    "infer_agent_profile_id_for_node",
    "is_plugin_tool",
    "is_stop_intent",
    "list_avatar_presets",
    "new_command_id",
    "new_org_id",
    "new_project_id",
    "new_schedule_id",
    "new_task_id",
    "now_iso",
    "origin_surface_label_cn",
    "plugin_id_for_tool",
    "react_trace_has_tool",
    "reset_default_project_stores",
    "reset_default_store",
    "safe_asset_filename",
    "set_command_service",
    "set_default_org_manager",
]
