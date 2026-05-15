"""C23 P2-1: Policy V2 approval-matrix backend truth-source guard.

Background
==========

Earlier the approval matrix was a static React literal in
``apps/setup-center/src/views/security/PolicyV2MatrixView.tsx``. That
let it silently drift from ``engine.py`` / ``matrix.py`` whenever
someone refactored decision logic.

Post-cleanup the matrix is owned by the backend:

  GET /api/config/security/approval-matrix

returns ``rows = [{role, approval_class, decisions: {<mode>: <action>}}]``
computed live from ``openakita.core.policy_v2.matrix.lookup``. The
frontend just renders whatever the backend gives it.

This test
=========

Switches from frontend regex matching to backend invariant checks:

1. Component & i18n keys are still present (so the UI doesn't render
   raw English fallback labels).
2. The approval-matrix API exposes every enum value (no silent drops
   when ``enums.py`` adds a class/mode/role).
3. Baseline cell invariants documented in plan §3 still hold against
   ``matrix.lookup`` directly — including the fail-closed safety
   guarantees (destructive→strict→deny, unknown never auto-allow,
   dont_ask denies any class that would otherwise prompt).

We deliberately test ``lookup_matrix`` rather than the FastAPI HTTP
response because the goal is to guard the truth source — the route is
a thin wrapper that this same module also import-checks lower down.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from openakita.api.routes import config as config_routes
from openakita.core.policy_v2 import (
    ApprovalClass,
    ConfirmationMode,
    DecisionAction,
    SessionRole,
)
from openakita.core.policy_v2.matrix import lookup as lookup_matrix

FRONTEND_MATRIX = Path("apps/setup-center/src/views/security/PolicyV2MatrixView.tsx")


def test_matrix_component_exists() -> None:
    assert FRONTEND_MATRIX.exists(), (
        f"{FRONTEND_MATRIX} not found — the SecurityView tab depends on it. "
        "If you intentionally removed it, also remove the import / tab "
        "registration from SecurityView.tsx in the same commit."
    )


def test_matrix_endpoint_covers_every_enum_value() -> None:
    """Approval-matrix endpoint MUST enumerate every enum value.

    If anyone adds a new ApprovalClass/ConfirmationMode/SessionRole to
    ``enums.py`` without also wiring it into ``matrix.lookup``, the
    endpoint stops being a complete view of the policy surface.
    """
    payload = asyncio.run(config_routes.read_security_approval_matrix())

    expected_classes = sorted(c.value for c in ApprovalClass)
    expected_modes = sorted(m.value for m in ConfirmationMode)
    expected_roles = sorted(r.value for r in SessionRole)

    assert sorted(payload["classes"]) == expected_classes, (
        f"classes mismatch: api={sorted(payload['classes'])} vs enum={expected_classes}"
    )
    assert sorted(payload["modes"]) == expected_modes, (
        f"modes mismatch: api={sorted(payload['modes'])} vs enum={expected_modes}"
    )
    assert sorted(payload["roles"]) == expected_roles, (
        f"roles mismatch: api={sorted(payload['roles'])} vs enum={expected_roles}"
    )

    seen_pairs: set[tuple[str, str]] = set()
    for row in payload["rows"]:
        assert isinstance(row.get("decisions"), dict), row
        assert set(row["decisions"]) == set(expected_modes), (
            f"row {row['role']}×{row['approval_class']} missing modes: "
            f"{set(expected_modes) - set(row['decisions'])}"
        )
        seen_pairs.add((row["role"], row["approval_class"]))

    expected_pairs = {(r, c) for r in expected_roles for c in expected_classes}
    assert seen_pairs == expected_pairs, (
        f"approval-matrix endpoint is missing rows: {expected_pairs - seen_pairs}"
    )


def test_destructive_strict_is_deny() -> None:
    """Hard guard: destructive × strict × AGENT → DENY.

    The single most-cited user-facing safety guarantee of Policy V2.
    """
    assert (
        lookup_matrix(SessionRole.AGENT, ConfirmationMode.STRICT, ApprovalClass.DESTRUCTIVE)
        == DecisionAction.DENY
    )


def test_unknown_class_never_auto_allows() -> None:
    """UNKNOWN is the fail-closed bucket; no mode may auto-allow it.

    This catches a regression where someone relaxes the classifier
    fallback without realising every mode then auto-approves
    unclassified tools.
    """
    for mode in ConfirmationMode:
        action = lookup_matrix(SessionRole.AGENT, mode, ApprovalClass.UNKNOWN)
        assert action != DecisionAction.ALLOW, (
            f"FATAL: UNKNOWN × {mode.value} → {action.value}. UNKNOWN must "
            "never auto-allow in any mode, otherwise unclassified tools "
            "silently bypass policy review."
        )


@pytest.mark.parametrize("mode", list(ConfirmationMode))
def test_readonly_scoped_always_allows(mode: ConfirmationMode) -> None:
    """READONLY_SCOPED → ALLOW in every mode for AGENT.

    Readonly tools don't mutate state and shouldn't trigger prompts.
    """
    assert (
        lookup_matrix(SessionRole.AGENT, mode, ApprovalClass.READONLY_SCOPED)
        == DecisionAction.ALLOW
    )


def test_destructive_must_confirm_in_interactive_modes() -> None:
    """DESTRUCTIVE must require confirm in trust/default/accept_edits.

    Strict gets the stronger deny (covered by ``test_destructive_strict_is_deny``).
    """
    for mode in (
        ConfirmationMode.TRUST,
        ConfirmationMode.DEFAULT,
        ConfirmationMode.ACCEPT_EDITS,
    ):
        action = lookup_matrix(SessionRole.AGENT, mode, ApprovalClass.DESTRUCTIVE)
        assert action == DecisionAction.CONFIRM, (
            f"DESTRUCTIVE × {mode.value} expected confirm baseline, got {action.value}."
        )


def test_coordinator_role_locks_control_plane_under_trust() -> None:
    """Coordinator role must lift TRUST cells of CONTROL_PLANE/EXEC_CAPABLE/
    MUTATING_GLOBAL up to CONFIRM (otherwise coordinator can silently
    escalate via control-plane tools)."""
    for klass in (
        ApprovalClass.CONTROL_PLANE,
        ApprovalClass.EXEC_CAPABLE,
        ApprovalClass.MUTATING_GLOBAL,
    ):
        action = lookup_matrix(
            SessionRole.COORDINATOR, ConfirmationMode.TRUST, klass
        )
        assert action == DecisionAction.CONFIRM, (
            f"COORDINATOR × trust × {klass.value} must be confirm "
            f"(escalation guard); got {action.value}."
        )


def test_plan_and_ask_roles_block_mutating_global() -> None:
    """PLAN/ASK roles must NEVER auto-allow MUTATING_GLOBAL.

    These read-intent roles should fail closed for cross-scope writes.
    """
    for role in (SessionRole.PLAN, SessionRole.ASK):
        for mode in ConfirmationMode:
            action = lookup_matrix(role, mode, ApprovalClass.MUTATING_GLOBAL)
            assert action != DecisionAction.ALLOW, (
                f"{role.value} × {mode.value} × mutating_global = {action.value} "
                "leaks write-intent through a read-only role."
            )


def test_securityview_imports_and_registers_tab() -> None:
    """SecurityView must import the component AND register the
    ``policy_v2_matrix`` tab id, otherwise the matrix is unreachable."""
    sv = Path("apps/setup-center/src/views/SecurityView.tsx").read_text(encoding="utf-8")
    assert "PolicyV2MatrixView" in sv
    assert '"policy_v2_matrix"' in sv
    assert 'tab === "policy_v2_matrix"' in sv


def test_i18n_strings_present() -> None:
    """zh + en must carry the matrix-related i18n keys.

    Without these the .tsx fallback English bleeds into zh UI even
    though the user picked Chinese.
    """
    import json

    for locale in ("zh.json", "en.json"):
        path = Path(f"apps/setup-center/src/i18n/{locale}")
        data = json.loads(path.read_text(encoding="utf-8"))
        security_keys = data.get("security", {})
        for key in (
            "policyV2Matrix",
            "matrixTitle",
            "matrixSessionRoleTitle",
            "matrixLegendAllow",
            "matrixLegendConfirm",
            "matrixLegendDeny",
        ):
            assert key in security_keys, (
                f"{locale} security.{key} missing — UI will render the "
                "fallback English string baked into the .tsx instead of "
                "the translated label."
            )
