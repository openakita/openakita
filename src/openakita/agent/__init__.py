"""OpenAkita v2 agent package.

Replaces the legacy ``src/openakita/core/`` per ADR-0003. The package
is populated incrementally during Phase 2; the per-file plan lives in
``docs/revamp/core_audit.md``.

Public symbols are exported lazily as their modules land. The
canonical :class:`Agent` and :class:`AgentState` will be re-exported
from :mod:`openakita.agent.facade` once the rewrite slices land.
"""

from __future__ import annotations

from .audit import AuditLogger, get_audit_logger, reset_audit_logger
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
    PendingRiskConfirmation,
    PendingRiskConfirmationStore,
    get_confirmation_store,
    normalize_confirmation_answer,
)
from .errors import UserCancelledError
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
from .resource_budget import (
    BudgetAction,
    BudgetConfig,
    BudgetExceeded,
    BudgetStatus,
    ResourceBudget,
    create_budget_from_settings,
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
from .token_budget import TokenBudget, parse_token_budget
from .tool_result_budget import (
    DEFAULT_MAX_RESULT_CHARS,
    OVERFLOW_DIR,
    truncate_tool_result,
)
from .ui_confirm_bus import UIConfirmBus, get_ui_confirm_bus, reset_ui_confirm_bus
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
    "ASK_MODE_RULESET",
    "AuditLogger",
    "BaseValidator",
    "BudgetAction",
    "BudgetConfig",
    "BudgetExceeded",
    "BudgetStatus",
    "CODE_EXEC_TOOLS",
    "COORDINATOR_MODE_RULESET",
    "CallbackHook",
    "CapabilityDescriptor",
    "CapabilityKind",
    "CapabilityOrigin",
    "CapabilityVisibility",
    "ConfirmationDecision",
    "DEFAULT_MAX_RESULT_CHARS",
    "DEFAULT_RULESET",
    "DISCLAIMER_TEXT",
    "DeniedError",
    "HookEvent",
    "HookExecutor",
    "HookHandler",
    "HookResult",
    "Identity",
    "JSONFormatter",
    "LoopBudgetDecision",
    "LoopBudgetGuard",
    "MergedPersona",
    "OVERFLOW_DIR",
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
    "READONLY_EXPLORATION_TOOLS",
    "ResourceBudget",
    "Ruleset",
    "SKILL_GIT_CLONE_TIMEOUT_SECONDS",
    "SKILL_INSTALL_CIRCUIT_COOLDOWN_SECONDS",
    "SKILL_INSTALL_CIRCUIT_THRESHOLD",
    "ShellHook",
    "SkillManager",
    "StreamJSONFormatter",
    "TextFormatter",
    "TokenBudget",
    "UIConfirmBus",
    "UserCancelledError",
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
    "create_budget_from_settings",
    "create_default_registry",
    "create_formatter",
    "detect_numeric_output",
    "detect_numeric_task",
    "execute_controlled_action",
    "extract_working_facts",
    "format_working_facts",
    "get_audit_logger",
    "get_confirmation_store",
    "get_hook_executor",
    "get_pending_approvals_store",
    "get_ui_confirm_bus",
    "list_security_allowlist",
    "list_skill_external_allowlist",
    "maybe_broadcast_death_switch_reset",
    "maybe_refresh_skills",
    "merge_working_facts",
    "normalize_confirmation_answer",
    "normalize_slug",
    "parse_token_budget",
    "persist_trait_to_memory",
    "remove_security_allowlist_entry",
    "reset_audit_logger",
    "reset_death_switch",
    "reset_pending_approvals_store",
    "reset_ui_confirm_bus",
    "set_hook_executor",
    "set_skill_external_allowlist",
    "truncate_tool_result",
    "validate_no_fabricated_numbers",
]
