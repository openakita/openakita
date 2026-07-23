"""Public agent runtime APIs for OpenAkita."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .audit import AuditLogger, get_audit_logger, reset_audit_logger
from .brain import Brain, SupervisorBrain
from .brain import Context as BrainContext
from .brain import Response as BrainResponse
from .capabilities import (
    CapabilityDescriptor,
    CapabilityKind,
    CapabilityOrigin,
    CapabilityVisibility,
    build_capability_id,
    build_namespace,
    normalize_slug,
)
from .confirmation import (
    ConfirmationDecision,
    normalize_confirmation_answer,
)
from .context import (
    CHARS_PER_TOKEN,
    CHUNK_MAX_TOKENS,
    CONTEXT_BOUNDARY_MARKER,
    DEFAULT_MAX_CONTEXT_TOKENS,
    ContextManager,
    ContextPressure,
    estimate_tokens,
    get_max_context_tokens,
)
from .core import Agent, PromptStrategy, get_primary_agent, set_primary_agent
from .desktop_notify import (
    notify_task_completed,
    notify_task_completed_async,
    send_desktop_notification,
    send_desktop_notification_async,
)
from .docker_backend import (
    DockerBackend,
    DockerConfig,
    DockerResult,
    configure_docker,
    get_docker_backend,
)
from .domain_allowlist import Decision, DomainAllowlist, get_domain_allowlist
from .errors import UserCancelledError
from .file_history import (
    HISTORY_BASE_DIR,
    MAX_SNAPSHOTS,
    BackupInfo,
    FileHistoryManager,
    FileSnapshot,
)
from .hooks import (
    CallbackHook,
    HookEvent,
    HookExecutor,
    HookHandler,
    HookResult,
    ShellHook,
    get_hook_executor,
    set_hook_executor,
)
from .identity import Identity
from .loop_budget import (
    READONLY_EXPLORATION_TOOLS,
    LoopBudgetDecision,
    LoopBudgetGuard,
)
from .lsp_feedback import (
    Diagnostic,
    DiagnosticBackend,
    DiagnosticReport,
    LSPFeedbackCollector,
    RuffBackend,
    TypeScriptBackend,
)
from .output_formatter import (
    JSONFormatter,
    OutputFormatter,
    StreamJSONFormatter,
    TextFormatter,
    create_formatter,
)
from .output_guard import (
    CODE_EXEC_TOOLS,
    DISCLAIMER_TEXT,
    detect_numeric_output,
    detect_numeric_task,
    validate_no_fabricated_numbers,
)
from .pending_approvals import (
    PendingApproval,
    PendingApprovalsStore,
    get_pending_approvals_store,
    reset_pending_approvals_store,
)
from .permission import (
    ASK_MODE_RULESET,
    COORDINATOR_MODE_RULESET,
    DEFAULT_RULESET,
    PLAN_MODE_RULESET,
    DeniedError,
    PermissionDecision,
    PermissionRule,
    Ruleset,
    check_mode_permission,
    check_path,
    check_permission,
)
from .persona import (
    PERSONA_DIMENSIONS,
    MergedPersona,
    PersonaManager,
    PersonaTrait,
    persist_trait_to_memory,
)
from .ralph import RalphLoop, StopHook, Task, TaskResult, TaskStatus

# NOTE: ``.reasoning`` is intentionally NOT eagerly imported here -- it is
# exposed lazily via ``__getattr__`` (bottom of this module). Eagerly importing
# it re-introduces an import cycle whenever the private reasoning runtime is
# imported before ``openakita.agent``. See the notes beside ``__getattr__`` below.
if TYPE_CHECKING:
    from .reasoning import Checkpoint, DecisionType, ReasoningEngine
    from .reasoning import Decision as ReasoningDecision
from .resource_budget import (
    BudgetAction,
    BudgetConfig,
    BudgetExceeded,
    BudgetStatus,
    ResourceBudget,
    create_budget_from_settings,
)
from .sandbox import (
    CommandSandbox,
    SandboxExecutor,
    SandboxPolicy,
    SandboxResult,
    SandboxVerdict,
    get_sandbox_executor,
)
from .security_actions import (
    add_security_allowlist_entry,
    execute_controlled_action,
    list_security_allowlist,
    list_skill_external_allowlist,
    maybe_broadcast_death_switch_reset,
    maybe_refresh_skills,
    remove_security_allowlist_entry,
    reset_death_switch,
    set_skill_external_allowlist,
)
from .skill_manager import (
    SKILL_GIT_CLONE_TIMEOUT_SECONDS,
    SKILL_INSTALL_CIRCUIT_COOLDOWN_SECONDS,
    SKILL_INSTALL_CIRCUIT_THRESHOLD,
    SkillManager,
)
from .sse_replay import (
    DEFAULT_MAXLEN,
    DEFAULT_TTL_SECONDS,
    MAX_SESSIONS,
    SSEEvent,
    SSESession,
    SSESessionRegistry,
    format_sse_frame,
    get_registry,
    parse_last_event_id,
    reset_registry_for_testing,
)
from .token_budget import TokenBudget, parse_token_budget
from .tool_result_budget import (
    DEFAULT_MAX_RESULT_CHARS,
    OVERFLOW_DIR,
    truncate_tool_result,
)
from .tools import (
    DEFAULT_TOOL_RESULT_MAX_CHARS,
    MAX_TOOL_RESULT_CHARS,
    OVERFLOW_MARKER,
    ToolExecutor,
    ToolResultWithHint,
    ToolSkipped,
    save_overflow,
    smart_truncate,
)
from .trait_miner import (
    ANSWER_ANALYSIS_PROMPT,
    ANSWER_ANALYSIS_SYSTEM,
    TRAIT_MINING_PROMPT,
    TRAIT_MINING_SYSTEM,
    TraitMiner,
)
from .trusted_paths import (
    SESSION_KEY,
    clear_session_trust,
    consume_session_trust,
    get_session_overrides,
    grant_session_trust,
    is_trusted_workspace_path,
)
from .ui_confirm_bus import UIConfirmBus, get_ui_confirm_bus, reset_ui_confirm_bus
from .user_profile import (
    USER_PROFILE_ITEMS,
    USER_PROFILE_KEY_ALIASES,
    UserProfileItem,
    UserProfileManager,
    UserProfileState,
    get_profile_manager,
    resolve_profile_key,
)
from .validators import (
    BaseValidator,
    ValidationContext,
    ValidationReport,
    ValidationResult,
    ValidatorOutput,
    ValidatorRegistry,
    create_default_registry,
)
from .working_facts import (
    extract_working_facts,
    format_working_facts,
    merge_working_facts,
)

__all__ = [
    "ANSWER_ANALYSIS_PROMPT",
    "ANSWER_ANALYSIS_SYSTEM",
    "ASK_MODE_RULESET",
    "Agent",
    "AuditLogger",
    "BackupInfo",
    "BaseValidator",
    "Brain",
    "BrainContext",
    "BrainResponse",
    "BudgetAction",
    "BudgetConfig",
    "BudgetExceeded",
    "BudgetStatus",
    "CHARS_PER_TOKEN",
    "CHUNK_MAX_TOKENS",
    "CODE_EXEC_TOOLS",
    "CONTEXT_BOUNDARY_MARKER",
    "COORDINATOR_MODE_RULESET",
    "CallbackHook",
    "CapabilityDescriptor",
    "CapabilityKind",
    "CapabilityOrigin",
    "CapabilityVisibility",
    "Checkpoint",
    "CommandSandbox",
    "ConfirmationDecision",
    "ContextManager",
    "ContextPressure",
    "DEFAULT_MAX_CONTEXT_TOKENS",
    "DEFAULT_MAX_RESULT_CHARS",
    "DEFAULT_MAXLEN",
    "DEFAULT_RULESET",
    "DEFAULT_TOOL_RESULT_MAX_CHARS",
    "DEFAULT_TTL_SECONDS",
    "DISCLAIMER_TEXT",
    "Decision",
    "DecisionType",
    "DeniedError",
    "Diagnostic",
    "DiagnosticBackend",
    "DiagnosticReport",
    "DockerBackend",
    "DockerConfig",
    "DockerResult",
    "DomainAllowlist",
    "FileHistoryManager",
    "FileSnapshot",
    "HISTORY_BASE_DIR",
    "HookEvent",
    "HookExecutor",
    "HookHandler",
    "HookResult",
    "Identity",
    "JSONFormatter",
    "LSPFeedbackCollector",
    "LoopBudgetDecision",
    "LoopBudgetGuard",
    "MAX_SESSIONS",
    "MAX_SNAPSHOTS",
    "MAX_TOOL_RESULT_CHARS",
    "MergedPersona",
    "OVERFLOW_DIR",
    "OVERFLOW_MARKER",
    "OutputFormatter",
    "PERSONA_DIMENSIONS",
    "PLAN_MODE_RULESET",
    "PendingApproval",
    "PendingApprovalsStore",
    "PendingRiskConfirmation",
    "PendingRiskConfirmationStore",
    "PermissionDecision",
    "PermissionRule",
    "PersonaManager",
    "PersonaTrait",
    "PromptStrategy",
    "READONLY_EXPLORATION_TOOLS",
    "RalphLoop",
    "ReasoningDecision",
    "ReasoningEngine",
    "ResourceBudget",
    "RuffBackend",
    "Ruleset",
    "SESSION_KEY",
    "SKILL_GIT_CLONE_TIMEOUT_SECONDS",
    "SKILL_INSTALL_CIRCUIT_COOLDOWN_SECONDS",
    "SKILL_INSTALL_CIRCUIT_THRESHOLD",
    "SSEEvent",
    "SSESession",
    "SSESessionRegistry",
    "SandboxExecutor",
    "SandboxPolicy",
    "SandboxResult",
    "SandboxVerdict",
    "ShellHook",
    "SkillManager",
    "StopHook",
    "StreamJSONFormatter",
    "SupervisorBrain",
    "TRAIT_MINING_PROMPT",
    "TRAIT_MINING_SYSTEM",
    "Task",
    "TaskResult",
    "TaskStatus",
    "TextFormatter",
    "TokenBudget",
    "ToolExecutor",
    "ToolResultWithHint",
    "ToolSkipped",
    "TraitMiner",
    "TypeScriptBackend",
    "UIConfirmBus",
    "USER_PROFILE_ITEMS",
    "USER_PROFILE_KEY_ALIASES",
    "UserCancelledError",
    "UserProfileItem",
    "UserProfileManager",
    "UserProfileState",
    "ValidationContext",
    "ValidationReport",
    "ValidationResult",
    "ValidatorOutput",
    "ValidatorRegistry",
    "add_security_allowlist_entry",
    "build_capability_id",
    "build_namespace",
    "check_mode_permission",
    "check_path",
    "check_permission",
    "clear_session_trust",
    "configure_docker",
    "consume_session_trust",
    "create_budget_from_settings",
    "create_default_registry",
    "create_formatter",
    "detect_numeric_output",
    "detect_numeric_task",
    "estimate_tokens",
    "execute_controlled_action",
    "extract_working_facts",
    "format_sse_frame",
    "format_working_facts",
    "get_audit_logger",
    "get_confirmation_store",
    "get_docker_backend",
    "get_domain_allowlist",
    "get_hook_executor",
    "get_max_context_tokens",
    "get_pending_approvals_store",
    "get_primary_agent",
    "get_profile_manager",
    "get_registry",
    "get_sandbox_executor",
    "get_session_overrides",
    "get_ui_confirm_bus",
    "grant_session_trust",
    "is_trusted_workspace_path",
    "list_security_allowlist",
    "list_skill_external_allowlist",
    "maybe_broadcast_death_switch_reset",
    "maybe_refresh_skills",
    "merge_working_facts",
    "normalize_confirmation_answer",
    "normalize_slug",
    "notify_task_completed",
    "notify_task_completed_async",
    "parse_last_event_id",
    "parse_token_budget",
    "persist_trait_to_memory",
    "remove_security_allowlist_entry",
    "reset_audit_logger",
    "reset_death_switch",
    "reset_pending_approvals_store",
    "reset_registry_for_testing",
    "reset_ui_confirm_bus",
    "resolve_profile_key",
    "save_overflow",
    "send_desktop_notification",
    "send_desktop_notification_async",
    "set_hook_executor",
    "set_primary_agent",
    "set_skill_external_allowlist",
    "smart_truncate",
    "truncate_tool_result",
    "validate_no_fabricated_numbers",
]


# ---------------------------------------------------------------------------
# Lazy publication of the reasoning symbols (PEP 562) breaks an import cycle.
#
# Importing ``openakita.core._reasoning_runtime`` directly before
# ``openakita.agent`` enters ``sys.modules`` would otherwise trigger:
#
#   _reasoning_runtime -> ``from openakita.agent.errors import UserCancelledError``
#     -> runs THIS ``openakita/agent/__init__`` for the first time
#     -> eager ``from .reasoning import Checkpoint, ...``
#     -> agent.reasoning ``from openakita.core._reasoning_runtime import Checkpoint``
#     -> the runtime module is only partially initialized
#     -> ``ImportError: cannot import name 'Checkpoint'``.
#
# Deferring the ``.reasoning`` import to first attribute access prevents
# ``agent/__init__`` from re-entering the half-built runtime module, so the
# cycle cannot form regardless of import order.
_LAZY_REASONING_EXPORTS = {
    "Checkpoint": "Checkpoint",
    "DecisionType": "DecisionType",
    "ReasoningEngine": "ReasoningEngine",
    "ReasoningDecision": "Decision",
}
_LAZY_CONFIRMATION_EXPORTS = {
    "PendingRiskConfirmation",
    "PendingRiskConfirmationStore",
    "get_confirmation_store",
}


def __getattr__(name: str):  # PEP 562 module-level lazy attribute access
    target = _LAZY_REASONING_EXPORTS.get(name)
    if target is not None:
        from . import reasoning as _reasoning

        value = getattr(_reasoning, target)
        globals()[name] = value  # cache so __getattr__ runs at most once per name
        return value
    if name in _LAZY_CONFIRMATION_EXPORTS:
        from openakita.core import confirmation_state as _confirmation_state

        value = getattr(_confirmation_state, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
