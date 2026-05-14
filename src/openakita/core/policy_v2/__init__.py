"""policy_v2 — Security Architecture v2 unified policy engine.

公共 API：

- 决策入口：``PolicyEngineV2.evaluate_tool_call()`` / ``evaluate_message_intent()``
- 上下文：``PolicyContext`` / ``get_current_context()`` / ``set_current_context()``
- 数据类：``PolicyDecisionV2`` / ``PolicyResult`` / ``DecisionStep`` / ``ToolCallEvent`` / ``MessageIntentEvent``
- 枚举：``ApprovalClass`` / ``SessionRole`` / ``ConfirmationMode`` / ``DecisionAction`` / ``DecisionSource``
- 矩阵：``lookup_matrix(role, mode, klass)`` → ``DecisionAction``
- 异常：``PolicyError`` / ``DeniedByPolicy`` / ``ConfirmationRequired`` / ``DeferredApprovalRequired``
- 分类器：``ApprovalClassifier`` / ``ClassificationResult``
- Shell 风险：``ShellRiskLevel`` / ``classify_shell_command``
- Workspace 路径：``is_inside_workspace`` / ``all_paths_inside_workspace`` / ``candidate_path_fields``

实施进度对照：``docs/policy_v2_research.md`` §12 commit 表。
当前进度：C0-C6 完成（决策层已切 v2，UI 状态留 v1 待 C9 重建；C8 删 v1 壳）。
"""

from .adapter import (
    build_policy_context,
    evaluate_message_intent_via_v2,
    evaluate_via_v2,
    mode_to_session_role,
)
from .classifier import ApprovalClassifier, ClassificationResult
from .confirm_resolution import apply_resolution
from .confirmation_mode import (
    coerce_v1_label_to_v2_mode,
    read_permission_mode_label,
)
from .context import (
    PolicyContext,
    ReplayAuthorization,
    TrustedPathOverride,
    get_current_context,
    reset_current_context,
    set_current_context,
)
from .death_switch import (
    DeathSwitchTracker,
    get_death_switch_tracker,
    reset_death_switch_tracker,
)
from .declared_class_trust import (
    DeclaredClassTrust,
    compute_effective_class,
    infer_mcp_declared_trust,
    infer_skill_declared_trust,
)
from .defaults import (
    default_blocked_commands,
    default_controlled_paths,
    default_forbidden_paths,
    default_protected_paths,
)
from .engine import PolicyEngineV2, build_engine_from_config
from .entry_point import (
    IM_WEBHOOK_CHANNELS,
    SSE_INTERACTIVE_CHANNELS,
    EntryClassification,
    apply_classification_to_session,
    classify_entry,
)
from .enums import (
    ApprovalClass,
    ConfirmationMode,
    DecisionAction,
    DecisionSource,
    SessionRole,
    most_strict,
    strictness,
)
from .exceptions import (
    ConfirmationRequired,
    DeferredApprovalRequired,
    DeniedByPolicy,
    PolicyError,
)
from .global_engine import (
    get_config_v2,
    get_engine_v2,
    is_initialized,
    make_preview_engine,
    rebuild_engine_v2,
    reset_engine_v2,
    reset_policy_v2_layer,
    set_engine_v2,
)
from .loader import (
    PolicyConfigError,
    load_policies_from_dict,
    load_policies_yaml,
)
from .matrix import lookup as lookup_matrix
from .migration import (
    MigrationReport,
    detect_schema_version,
    migrate_v1_to_v2,
)
from .models import (
    DecisionStep,
    MessageIntentEvent,
    PolicyDecisionV2,
    PolicyResult,
    ToolCallEvent,
)
from .safety_immune_defaults import (
    BUILTIN_SAFETY_IMMUNE_BY_CATEGORY,
    BUILTIN_SAFETY_IMMUNE_PATHS,
    expand_builtin_immune_paths,
)
from .schema import (
    ApprovalClassesConfig,
    AuditConfig,
    CheckpointConfig,
    ConfirmationConfig,
    DeathSwitchConfig,
    OwnerOnlyConfig,
    PolicyConfigV2,
    SafetyImmuneConfig,
    SandboxConfig,
    SessionRoleConfig,
    ShellRiskConfig,
    UnattendedConfig,
    UserAllowlistConfig,
    WorkspaceConfig,
)
from .session_allowlist import (
    SessionAllowlistManager,
    get_session_allowlist_manager,
    reset_session_allowlist_manager,
)
from .shell_risk import (
    DEFAULT_BLOCKED_COMMANDS,
    ShellRiskLevel,
    classify_shell_command,
)
from .skill_allowlist import (
    SkillAllowlistManager,
    get_skill_allowlist_manager,
    reset_skill_allowlist_manager,
)
from .user_allowlist import UserAllowlistManager, command_to_pattern
from .zones import (
    all_paths_inside_workspace,
    candidate_path_fields,
    is_inside_workspace,
)

__all__ = [
    # enums
    "ApprovalClass",
    "ConfirmationMode",
    "DecisionAction",
    "DecisionSource",
    "SessionRole",
    "most_strict",
    "strictness",
    # exceptions
    "ConfirmationRequired",
    "DeferredApprovalRequired",
    "DeniedByPolicy",
    "PolicyError",
    # matrix
    "lookup_matrix",
    # models
    "DecisionStep",
    "MessageIntentEvent",
    "PolicyDecisionV2",
    "PolicyResult",
    "ToolCallEvent",
    # context
    "PolicyContext",
    "ReplayAuthorization",
    "TrustedPathOverride",
    "get_current_context",
    "reset_current_context",
    "set_current_context",
    # classifier
    "ApprovalClassifier",
    "ClassificationResult",
    # engine
    "PolicyEngineV2",
    "build_engine_from_config",
    # entry-point classifier (C14)
    "EntryClassification",
    "IM_WEBHOOK_CHANNELS",
    "SSE_INTERACTIVE_CHANNELS",
    "apply_classification_to_session",
    "classify_entry",
    # global singleton (C6)
    "get_config_v2",
    "get_engine_v2",
    "is_initialized",
    "make_preview_engine",
    "rebuild_engine_v2",
    "reset_engine_v2",
    "reset_policy_v2_layer",
    "set_engine_v2",
    # adapter (C6/C7; C8b-6b 删 v1 桥接 helper)
    "build_policy_context",
    "evaluate_message_intent_via_v2",
    "evaluate_via_v2",
    "mode_to_session_role",
    # zones
    "all_paths_inside_workspace",
    "candidate_path_fields",
    "is_inside_workspace",
    # shell_risk
    "DEFAULT_BLOCKED_COMMANDS",
    "ShellRiskLevel",
    "classify_shell_command",
    # schema (C4)
    "ApprovalClassesConfig",
    "AuditConfig",
    "CheckpointConfig",
    "ConfirmationConfig",
    "DeathSwitchConfig",
    "OwnerOnlyConfig",
    "PolicyConfigV2",
    "SafetyImmuneConfig",
    "SandboxConfig",
    "SessionRoleConfig",
    "ShellRiskConfig",
    "UnattendedConfig",
    "UserAllowlistConfig",
    "WorkspaceConfig",
    # loader (C4)
    "PolicyConfigError",
    "load_policies_from_dict",
    "load_policies_yaml",
    # migration (C4)
    "MigrationReport",
    "detect_schema_version",
    "migrate_v1_to_v2",
    # safety_immune defaults (C8)
    "BUILTIN_SAFETY_IMMUNE_BY_CATEGORY",
    "BUILTIN_SAFETY_IMMUNE_PATHS",
    "expand_builtin_immune_paths",
    # C8b-1 managers (preparation for v1 deletion)
    "DeathSwitchTracker",
    "SkillAllowlistManager",
    "UserAllowlistManager",
    "command_to_pattern",
    "get_death_switch_tracker",
    "get_skill_allowlist_manager",
    "reset_death_switch_tracker",
    "reset_skill_allowlist_manager",
    # C8b-2 defaults (UI/config 兜底值，迁自 v1 ``policy.py``)
    "default_blocked_commands",
    "default_controlled_paths",
    "default_forbidden_paths",
    "default_protected_paths",
    # C8b-3 session allowlist + UI confirm resolution helper
    "SessionAllowlistManager",
    "apply_resolution",
    "get_session_allowlist_manager",
    "reset_session_allowlist_manager",
    # C8b-4 v1 _frontend_mode shim replacement
    "coerce_v1_label_to_v2_mode",
    "read_permission_mode_label",
    # C15 §17.3 Skill/MCP declared_class trust rule
    "DeclaredClassTrust",
    "compute_effective_class",
    "infer_mcp_declared_trust",
    "infer_skill_declared_trust",
]
