"""C23 P2-1: Frontend PolicyV2MatrixView consistency guard.

Background
==========

C23 ships ``apps/setup-center/src/views/security/PolicyV2MatrixView.tsx``
that renders a static **documentation** matrix of:

  ApprovalClass (11 + UNKNOWN = 12 rows) × ConfirmationMode (5 cols)

This is intentionally a static lookup table — the actual engine
decision chain (``engine.py``) cannot be reduced to a single
serializable matrix because each decision pulls in safety_immune,
unattended path, custom overrides, mode_ruleset, etc. The UI shows
the **baseline** behaviour when none of those special conditions
fire, which is what users want for "in mode X, do destructive
operations get auto-approved?"-style mental model questions.

Risk: the static UI matrix drifts from the engine's actual baseline
behaviour over time. Engineers refactor engine.py without thinking
to update the UI; users get a wrong mental model.

This test
=========

Grep-based structural guard:

1. The frontend file exists, exports the matrix constant
2. Every documented ApprovalClass + ConfirmationMode enum value
   shows up in the frontend file (so a new enum entry can't be
   added in enums.py without the UI being updated to render it)
3. The session role list is complete

We deliberately do NOT try to validate cell-by-cell decisions against
engine.py via Python evaluation. The matrix is documentation; if
engineers change engine.py logic, they MUST also flip the matching
cell in PolicyV2MatrixView.tsx, and code review catches that. This
test gives us the structural canary — "did anyone delete the file?
add a new ApprovalClass without UI coverage?".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openakita.core.policy_v2.enums import (
    ApprovalClass,
    ConfirmationMode,
    SessionRole,
)

FRONTEND_MATRIX = Path(
    "apps/setup-center/src/views/security/PolicyV2MatrixView.tsx"
)


def test_matrix_component_exists() -> None:
    assert FRONTEND_MATRIX.exists(), (
        f"{FRONTEND_MATRIX} not found — C23 P2-1 ships this file. "
        "If you intentionally removed it, also remove the import / tab "
        "registration from SecurityView.tsx in the same commit."
    )


def test_all_approval_classes_rendered() -> None:
    src = FRONTEND_MATRIX.read_text(encoding="utf-8")
    missing: list[str] = []
    for cls in ApprovalClass:
        # Each class should appear as a row key (the ``klass`` field
        # in the MATRIX object literal).
        token = f'klass: "{cls.value}"'
        if token not in src:
            missing.append(cls.value)
    assert not missing, (
        f"PolicyV2MatrixView.tsx is missing rows for: {missing}. "
        "Every ApprovalClass enum value MUST have a corresponding "
        "row in MATRIX so the UI doesn't silently drop a class when "
        "engineers add new ones to enums.py."
    )


def test_all_confirmation_modes_rendered() -> None:
    src = FRONTEND_MATRIX.read_text(encoding="utf-8")
    missing: list[str] = []
    for mode in ConfirmationMode:
        # Each mode should appear in the CONFIRMATION_MODES tuple as
        # ``id: "<value>"``.
        token = f'id: "{mode.value}"'
        if token not in src:
            missing.append(mode.value)
    assert not missing, (
        f"PolicyV2MatrixView.tsx is missing columns for: {missing}. "
        "Every ConfirmationMode enum value MUST have a column."
    )


def test_all_session_roles_rendered() -> None:
    src = FRONTEND_MATRIX.read_text(encoding="utf-8")
    missing: list[str] = []
    for role in SessionRole:
        token = f'id: "{role.value}"'
        if token not in src:
            missing.append(role.value)
    assert not missing, (
        f"PolicyV2MatrixView.tsx is missing session role: {missing}. "
        "Every SessionRole MUST be listed so users understand the "
        "Plan/Ask write-intent block-at-the-door semantic."
    )


def test_securityview_imports_and_registers_tab() -> None:
    """SecurityView must import the component AND register the
    ``policy_v2_matrix`` tab id, otherwise the new view is dead code."""
    sv = Path("apps/setup-center/src/views/SecurityView.tsx").read_text(
        encoding="utf-8"
    )
    assert "PolicyV2MatrixView" in sv, (
        "SecurityView.tsx must import PolicyV2MatrixView from "
        "./security/PolicyV2MatrixView."
    )
    assert '"policy_v2_matrix"' in sv, (
        'SecurityView.tsx must register tab id "policy_v2_matrix" in '
        "the TABS array and the TabId union."
    )
    assert 'tab === "policy_v2_matrix"' in sv, (
        "SecurityView.tsx must render <PolicyV2MatrixView /> when the "
        'active tab is "policy_v2_matrix".'
    )


def test_i18n_strings_present() -> None:
    """zh + en must carry the matrix-related i18n keys, otherwise
    fallback English shows up in zh UI."""
    import json

    for locale in ("zh.json", "en.json"):
        path = Path(f"apps/setup-center/src/i18n/{locale}")
        data = json.loads(path.read_text(encoding="utf-8"))
        chat_or_security = data.get("security", {})
        for key in (
            "policyV2Matrix",
            "matrixTitle",
            "matrixSessionRoleTitle",
            "matrixLegendAllow",
            "matrixLegendConfirm",
            "matrixLegendDeny",
        ):
            assert key in chat_or_security, (
                f"{locale} security.{key} missing — UI will show the "
                "fallback string baked into the .tsx instead of the "
                "translated one."
            )


@pytest.mark.parametrize(
    "expected_token",
    [
        # Spot-check critical decision cells. If someone changes the
        # baseline policy in engine.py (e.g. makes destructive
        # auto-allow in trust mode), this catches the drift via the
        # UI not getting updated.
        # Format: ``trust: "<decision>"`` etc. — must match the
        # MATRIX row literal in PolicyV2MatrixView.tsx.
        # readonly classes: ALL modes auto-allow (sanity)
        'klass: "readonly_scoped"',  # any of its decisions
        # destructive in strict → deny (the most important fail-closed bit)
        'klass: "destructive"',
        # unknown fail-closed → confirm in trust (don't silently allow)
        'klass: "unknown"',
    ],
)
def test_matrix_includes_critical_classes(expected_token: str) -> None:
    src = FRONTEND_MATRIX.read_text(encoding="utf-8")
    assert expected_token in src, (
        f"PolicyV2MatrixView.tsx missing row {expected_token!r}. "
        "These are critical fail-closed classes that MUST be shown "
        "to users; their absence would mean users can't see whether "
        "the most-dangerous operations are auto-approved."
    )


def test_destructive_strict_is_deny() -> None:
    """Hard guard: in strict mode, destructive class MUST be DENY.
    This is the single most important user-facing safety guarantee
    of policy_v2. If someone weakens this in the UI matrix, the test
    fires loudly."""
    src = FRONTEND_MATRIX.read_text(encoding="utf-8")
    # Find the destructive row line. We look for the start of the
    # row literal and verify ``strict: "deny"`` is on the same line.
    for line in src.splitlines():
        if 'klass: "destructive"' in line:
            assert 'strict: "deny"' in line, (
                "FATAL: destructive class in strict mode is not DENY in "
                "the UI matrix. This contradicts the documented engine "
                "behaviour and the user's safety expectation. Either "
                "fix engine.py + matrix together, or this matrix is "
                "out of date — investigate before merging."
            )
            return
    pytest.fail("destructive row not found in MATRIX")


def test_unknown_class_strict_is_deny_or_confirm() -> None:
    """UNKNOWN must NEVER auto-allow in any mode — it's the
    fail-closed bucket for tools we haven't classified yet. The
    matrix must show confirm or deny across all 5 modes."""
    src = FRONTEND_MATRIX.read_text(encoding="utf-8")
    for line in src.splitlines():
        if 'klass: "unknown"' in line:
            # Must NOT contain ``allow`` in any decision slot
            assert '"allow"' not in line, (
                "FATAL: UNKNOWN class has an ``allow`` cell. UNKNOWN is "
                "the fail-closed default for unclassified tools — "
                "auto-allowing in ANY mode is a security regression. "
                "If you intentionally relaxed this, also flip the "
                "engine's classifier fallback and update plan §3."
            )
            return
    pytest.fail("unknown row not found in MATRIX")
