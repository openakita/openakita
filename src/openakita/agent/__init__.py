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
from .errors import UserCancelledError
from .identity import Identity
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
    "CODE_EXEC_TOOLS",
    "COORDINATOR_MODE_RULESET",
    "DEFAULT_RULESET",
    "DISCLAIMER_TEXT",
    "DeniedError",
    "Identity",
    "JSONFormatter",
    "MergedPersona",
    "OutputFormatter",
    "PERSONA_DIMENSIONS",
    "PLAN_MODE_RULESET",
    "PermissionDecision",
    "PermissionRule",
    "PersonaManager",
    "PersonaTrait",
    "Ruleset",
    "StreamJSONFormatter",
    "TextFormatter",
    "UserCancelledError",
    "ValidationContext",
    "ValidationReport",
    "ValidationResult",
    "ValidatorOutput",
    "ValidatorRegistry",
    "check_mode_permission",
    "check_path",
    "check_permission",
    "create_default_registry",
    "create_formatter",
    "detect_numeric_output",
    "detect_numeric_task",
    "extract_working_facts",
    "format_working_facts",
    "get_audit_logger",
    "merge_working_facts",
    "persist_trait_to_memory",
    "reset_audit_logger",
    "validate_no_fabricated_numbers",
]
