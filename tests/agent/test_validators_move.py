"""Move-compatibility tests for ``openakita.agent.validators``.

Phase 2 ports ``core/validators.py`` to ``agent/validators.py``
and leaves a re-export shim. ``isinstance`` checks and registry
lookups on the legacy path must keep matching after the move; the
identity assertions below guarantee that.

The full behavioural suite is
``tests/unit/test_filesystem_move_file.py`` and
``tests/unit/test_org_delegation_validator.py`` — both keep
importing from the legacy path and are unchanged by this commit.
"""

from __future__ import annotations


def test_validator_classes_are_same_via_both_paths() -> None:
    from openakita.agent.validators import (
        ArtifactValidator as AArtifact,
    )
    from openakita.agent.validators import (
        BaseValidator as ABase,
    )
    from openakita.agent.validators import (
        CompletePlanValidator as AComplete,
    )
    from openakita.agent.validators import (
        FileValidator as AFile,
    )
    from openakita.agent.validators import (
        OrgDelegationValidator as AOrg,
    )
    from openakita.agent.validators import (
        PlanValidator as APlan,
    )
    from openakita.agent.validators import (
        ToolSuccessValidator as ATool,
    )
    from openakita.core.validators import (
        ArtifactValidator as CArtifact,
    )
    from openakita.core.validators import (
        BaseValidator as CBase,
    )
    from openakita.core.validators import (
        CompletePlanValidator as CComplete,
    )
    from openakita.core.validators import (
        FileValidator as CFile,
    )
    from openakita.core.validators import (
        OrgDelegationValidator as COrg,
    )
    from openakita.core.validators import (
        PlanValidator as CPlan,
    )
    from openakita.core.validators import (
        ToolSuccessValidator as CTool,
    )

    assert ABase is CBase
    assert APlan is CPlan
    assert AArtifact is CArtifact
    assert ATool is CTool
    assert AFile is CFile
    assert AComplete is CComplete
    assert AOrg is COrg


def test_validation_context_is_same_dataclass_via_both_paths() -> None:
    from openakita.agent.validators import ValidationContext as Agent
    from openakita.core.validators import ValidationContext as Core

    assert Agent is Core


def test_validation_report_is_same_dataclass_via_both_paths() -> None:
    from openakita.agent.validators import ValidationReport as Agent
    from openakita.core.validators import ValidationReport as Core

    assert Agent is Core


def test_validation_result_enum_is_same_via_both_paths() -> None:
    from openakita.agent.validators import ValidationResult as Agent
    from openakita.core.validators import ValidationResult as Core

    assert Agent is Core


def test_create_default_registry_is_same_function_via_both_paths() -> None:
    from openakita.agent.validators import create_default_registry as agent_fn
    from openakita.core.validators import create_default_registry as core_fn

    assert agent_fn is core_fn


def test_agent_namespace_re_exports_validator_symbols() -> None:
    from openakita import agent

    for sym in (
        "BaseValidator",
        "ValidationContext",
        "ValidationReport",
        "ValidationResult",
        "ValidatorOutput",
        "ValidatorRegistry",
        "create_default_registry",
    ):
        assert hasattr(agent, sym), sym
        assert sym in agent.__all__, sym
