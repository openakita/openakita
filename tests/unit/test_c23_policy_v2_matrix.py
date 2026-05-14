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


# ---------------------------------------------------------------------
# Per-row baseline invariants. These extend the previous "single key
# cell" guards (destructive×strict, unknown all-modes) into broader
# rules that catch wider classes of drift.
# ---------------------------------------------------------------------

# Mapping of class → list of (mode, expected_decision) pairs that MUST
# hold in the static matrix. Each entry corresponds to a property the
# user can rely on by reading the policy_v2 docs / plan §3 — if engine
# behaviour diverges from these, we want the test to fire so the UI is
# updated in lockstep.
INVARIANTS: list[tuple[str, str, str]] = [
    # readonly_* family — auto-allow in EVERY mode, including dont_ask
    # (dont_ask is cron-friendly: it kills *interactive* prompts, but
    # readonly tools never prompt so they pass through).
    ("readonly_scoped", "trust", "allow"),
    ("readonly_scoped", "default", "allow"),
    ("readonly_scoped", "accept_edits", "allow"),
    ("readonly_scoped", "strict", "allow"),
    ("readonly_scoped", "dont_ask", "allow"),
    ("readonly_global", "default", "allow"),
    ("readonly_global", "strict", "allow"),
    ("readonly_global", "dont_ask", "allow"),
    ("readonly_search", "default", "allow"),
    ("readonly_search", "strict", "allow"),
    ("readonly_search", "dont_ask", "allow"),
    # destructive in dont_ask: deny. dont_ask is the cron / unattended
    # mode — anything that needs confirmation is auto-denied, and
    # destructive ALWAYS needs confirmation in non-trust modes.
    ("destructive", "dont_ask", "deny"),
    # destructive in strict: deny (the single most-cited safety
    # guarantee; duplicated from test_destructive_strict_is_deny but
    # kept here so the invariant set is the single source of truth).
    ("destructive", "strict", "deny"),
    # exec_capable / control_plane — never auto-allow in any mode
    # except via custom override (engine step 9). The matrix must
    # show confirm in trust/default/accept_edits/strict; deny in
    # dont_ask.
    ("exec_capable", "trust", "confirm"),
    ("exec_capable", "strict", "confirm"),
    ("exec_capable", "dont_ask", "deny"),
    ("control_plane", "trust", "confirm"),
    ("control_plane", "strict", "confirm"),
    ("control_plane", "dont_ask", "deny"),
]


@pytest.mark.parametrize("klass,mode,expected", INVARIANTS)
def test_baseline_invariant(klass: str, mode: str, expected: str) -> None:
    """Per-(class, mode) baseline-decision invariant.

    Each tuple in :data:`INVARIANTS` documents a property of
    ``engine.py``'s default decision chain that's externally relied
    on. If you flip a cell in PolicyV2MatrixView.tsx without also
    flipping engine.py (or vice versa), the matching invariant fires.

    Format check: the matrix row literal puts all 5 decisions on one
    line (single-line per row); we grep for the row and assert the
    expected ``mode: "decision"`` substring is present on it.
    """
    src = FRONTEND_MATRIX.read_text(encoding="utf-8")
    needle_row = f'klass: "{klass}"'
    for line in src.splitlines():
        if needle_row in line:
            needle_cell = f'{mode}: "{expected}"'
            assert needle_cell in line, (
                f"Invariant violated: {klass} × {mode} should be "
                f"{expected.upper()} but the UI matrix says otherwise.\n"
                f"Row: {line.strip()}\n"
                f"If engine.py changed: update PolicyV2MatrixView.tsx "
                f"AND remove/adjust this invariant in tandem.\n"
                f"If engine.py did NOT change: the UI matrix drifted, "
                f"fix the .tsx file."
            )
            return
    pytest.fail(f"klass={klass!r} row not found in MATRIX")


def test_dont_ask_non_readonly_is_deny() -> None:
    """All non-readonly, non-interactive classes in dont_ask mode must
    be DENY. dont_ask is the cron / scheduled-task mode: any operation
    that would normally need a confirm prompt is dropped to deny
    (because there's no human to answer). Auto-allowing a write/exec
    in dont_ask would silently mutate the world during scheduled
    tasks without any audit trail beyond the decision log.

    Readonly classes are exempt — they don't trigger prompts to begin
    with. interactive is the literal ask_user tool; it has its own
    special-case behaviour in engine.py (dont_ask returns deny).
    """
    src = FRONTEND_MATRIX.read_text(encoding="utf-8")
    readonly_or_interactive = {
        "readonly_scoped",
        "readonly_global",
        "readonly_search",
        "interactive",
    }
    rows: list[tuple[str, str]] = []
    for line in src.splitlines():
        if 'klass: "' not in line or 'dont_ask:' not in line:
            continue
        # Extract klass + dont_ask value via simple substring slicing
        try:
            klass_start = line.index('klass: "') + len('klass: "')
            klass_end = line.index('"', klass_start)
            klass = line[klass_start:klass_end]
            dak_start = line.index('dont_ask: "') + len('dont_ask: "')
            dak_end = line.index('"', dak_start)
            decision = line[dak_start:dak_end]
            rows.append((klass, decision))
        except ValueError:
            continue
    bad = [(k, d) for (k, d) in rows if k not in readonly_or_interactive and d != "deny"]
    assert not bad, (
        f"dont_ask MUST be DENY for non-readonly classes; offenders: {bad}. "
        "If you intentionally relaxed a class in dont_ask mode, also "
        "verify engine.py mode_ruleset matches and remove the class "
        "from this invariant."
    )


def test_matrix_row_count_matches_approval_class_enum() -> None:
    """Defensive: the number of MATRIX rows must equal the number of
    ApprovalClass enum values. Combined with test_all_approval_classes_rendered,
    this catches the case where someone copies a row instead of editing
    it (duplicate klass with two values)."""
    src = FRONTEND_MATRIX.read_text(encoding="utf-8")
    # Count ``klass: "..."`` occurrences inside the MATRIX block. The
    # tsx file also emits ``klass: string;`` in the type definition
    # — that one uses no quotes around the value, so the pattern below
    # (with the literal quote) only catches data rows.
    import re

    klass_values = re.findall(r'klass: "([^"]+)"', src)
    expected = sorted(c.value for c in ApprovalClass)
    actual = sorted(klass_values)
    assert actual == expected, (
        f"MATRIX row count / values mismatch.\n"
        f"  ApprovalClass enum: {expected}\n"
        f"  MATRIX rows:        {actual}\n"
        "Likely cause: enum changed without UI update, OR a row was "
        "duplicated / deleted by accident."
    )
