"""Syntax-guard test pinning the ``except ValueError: state.status = X``
force-write pattern population (plan §5.6 / v1.28.3-pre S5-A audit).

Why this exists
---------------
The v1.27.13 hotfix (06c67221) and historical reason_stream code carry
exactly **9** ``except ValueError: state.status = TaskStatus.X``
force-write blocks that paper over the ``completed -> reasoning`` (and
similar) race exposed by issue #572.  Post v1.28 S1+S3+S4+S5-A the
underlying race is architecturally prevented at the
``ConversationLifecycleManager`` entry, so these force-writes should be
unreachable in practice — but we cannot delete them until production
``inc_illegal_reasoning_entry`` telemetry confirms 2 weeks of zero hits
(see release-notes/v1.28.md "Stage 5-A" + "Audit findings").

This test enforces three invariants:

1. **No new force-write may be added without explicit opt-in.**
   Each existing force-write carries an inline ``# s5b-allow-force-write``
   token within ±5 lines of its ``except ValueError:`` keyword.
   A new force-write added without the token fails this test —
   contributors must explicitly acknowledge they're growing the
   S5-B backlog.

2. **No force-write may be added in *other* files.**
   Today every force-write lives in ``reasoning_engine.py``.  If
   ``agent_state.py``, ``agent.py``, ``ralph.py``, etc. grow a new
   force-write, the test fails — concentrating the debt makes
   S5-B's deletion mechanically tractable.

3. **The total count is pinned.**
   When S5-B lands, it deletes the force-writes and drops the
   expected count to 0.  Any drift in the interim (accidental
   delete by an unrelated refactor / accidental add by a hot-fix)
   triggers a precise diff in the failure message.

What counts as a "force-write"
------------------------------
An ``except ValueError`` handler that contains an ``Assign`` to
``state.status`` or ``self.status`` anywhere in its body.  The
status value can be a simple name (``TaskStatus.FOO``), a
conditional expression, or another expression — what matters is
that an assignment to ``.status`` bypasses ``transition()``.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "openakita"

# Two kinds of force-write are recognised, distinguished by inline
# opt-in token:
#
#   • ``s5b-allow-force-write`` — historical hot-fix / safety-net debt
#     that S5-B will delete after 2 weeks of zero
#     ``inc_illegal_reasoning_entry`` telemetry.  Lives only in
#     ``reasoning_engine.py``.  Pinned at exactly 9 occurrences.
#
#   • ``cancel-idempotent-force-write`` — ``TaskState.cancel()``'s
#     architecturally-permanent escape hatch.  cancel() MUST succeed
#     from ANY prior state including terminal ones; refusing to flip
#     to CANCELLED would break the abort tree.  Pinned at exactly 1.
#
# Each force-write must carry exactly one of these tokens within
# ``TOKEN_PROXIMITY_LINES`` lines of its ``except ValueError:`` keyword.
# A force-write in any other file fails the scan regardless of token.

S5B_BACKLOG_FILES = {
    SRC_ROOT / "core" / "reasoning_engine.py": 9,  # plan §5.3 + 5.4 + S5-A
}

ARCH_FORCE_WRITE_FILES = {
    SRC_ROOT / "core" / "agent_state.py": 1,  # TaskState.cancel()
}

ALLOWED_FORCE_WRITE_FILES = set(S5B_BACKLOG_FILES) | set(ARCH_FORCE_WRITE_FILES)

# Files we want to scan.  Includes both allowlisted files (to verify
# each force-write has a token + correct count) and adjacent files
# where we want to prevent new force-writes from sprouting.
SCAN_FILES = [
    SRC_ROOT / "core" / "reasoning_engine.py",
    SRC_ROOT / "core" / "agent_state.py",
    SRC_ROOT / "core" / "agent.py",
    SRC_ROOT / "core" / "ralph.py",
    SRC_ROOT / "core" / "pending_approvals.py",
]

S5B_TOKEN = "s5b-allow-force-write"
CANCEL_TOKEN = "cancel-idempotent-force-write"
RECOGNISED_TOKENS = (S5B_TOKEN, CANCEL_TOKEN)

TOKEN_PROXIMITY_LINES = 5


def _is_status_assign(node: ast.AST) -> bool:
    """Return True if ``node`` is an Assign / AugAssign / AnnAssign that
    targets ``state.status`` or ``self.status`` (writing the status field
    by name rather than via ``transition()``)."""
    if isinstance(node, ast.Assign):
        targets = node.targets
    elif isinstance(node, ast.AugAssign | ast.AnnAssign):
        targets = [node.target]
    else:
        return False
    for target in targets:
        if (
            isinstance(target, ast.Attribute)
            and target.attr == "status"
            and isinstance(target.value, ast.Name)
            and target.value.id in {"state", "self"}
        ):
            return True
    return False


def _find_force_writes(path: pathlib.Path) -> list[tuple[int, str]]:
    """AST-walk ``path`` and return a list of ``(lineno, surface_repr)``
    for every ``except ValueError`` handler whose body contains an
    assignment to ``state.status`` / ``self.status``.

    ``surface_repr`` is a short human-readable description like
    ``"-> TaskStatus.REASONING"`` extracted from the assignment RHS to
    make failure diffs actionable.
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    findings: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        for handler in node.handlers:
            exc_type = handler.type
            is_value_error = isinstance(exc_type, ast.Name) and exc_type.id == "ValueError"
            if not is_value_error:
                continue
            # Walk the handler body for any ``.status = ...`` assignment.
            for body_node in ast.walk(ast.Module(body=handler.body, type_ignores=[])):
                if _is_status_assign(body_node):
                    rhs_repr = ast.unparse(
                        body_node.value
                        if isinstance(body_node, ast.Assign)
                        else getattr(body_node, "value", ast.Constant(value="?"))
                    )
                    surface = f"-> {rhs_repr[:60]}"
                    findings.append((handler.lineno, surface))
                    break  # one finding per handler is enough
    return findings


def _opt_in_token_near(path: pathlib.Path, except_lineno: int) -> str | None:
    """Return the opt-in token kind (``S5B_TOKEN`` or ``CANCEL_TOKEN``)
    found within ``TOKEN_PROXIMITY_LINES`` of the ``except`` keyword,
    or ``None`` if neither is present."""
    lines = path.read_text(encoding="utf-8").splitlines()
    start = max(0, except_lineno - TOKEN_PROXIMITY_LINES - 1)
    end = min(len(lines), except_lineno + TOKEN_PROXIMITY_LINES)
    window = "\n".join(lines[start:end])
    for token in RECOGNISED_TOKENS:
        if token in window:
            return token
    return None


class TestNoForceWriteStateTransitions:
    """Plan §5.6 syntax guard: pin the force-write population at
    9 S5-B-backlog hits in reasoning_engine.py + 1 architecturally-
    permanent hit in agent_state.py (TaskState.cancel())."""

    def test_force_writes_only_appear_in_allowlisted_files(self) -> None:
        """No force-write may sprout in agent.py / ralph.py /
        pending_approvals.py.  Concentrating the debt makes S5-B's
        deletion mechanically tractable."""
        for path in SCAN_FILES:
            if path in ALLOWED_FORCE_WRITE_FILES:
                continue
            findings = _find_force_writes(path)
            assert findings == [], (
                f"Force-writes detected in non-allowlisted file "
                f"{path.relative_to(REPO_ROOT)}: {findings}.  Use "
                f"TaskState.transition() or .ensure_ready_for_reasoning() "
                f"instead of ``state.status = X``."
            )

    def test_force_write_counts_per_file_are_pinned(self) -> None:
        """Per-file counts are fixed:
          • reasoning_engine.py — exactly 9 (S5-B backlog)
          • agent_state.py     — exactly 1 (cancel() idempotent)
        S5-B will drop the reasoning_engine.py number to 0.  Any drift
        (add or unintentional delete) fails this assertion with a
        per-file diff and the actionable next step."""
        expected = {**S5B_BACKLOG_FILES, **ARCH_FORCE_WRITE_FILES}
        actual: dict[pathlib.Path, list[tuple[int, str]]] = {
            path: _find_force_writes(path) for path in expected
        }
        diffs = []
        for path, expected_count in expected.items():
            found = actual[path]
            if len(found) != expected_count:
                diffs.append(
                    f"  {path.relative_to(REPO_ROOT)}: expected "
                    f"{expected_count}, found {len(found)} at lines "
                    f"{[lineno for lineno, _ in found]}"
                )
        assert diffs == [], (
            "Force-write count drifted:\n" + "\n".join(diffs) + "\n\n"
            "If you ADDED a force-write: think hard — S5-A's "
            "ensure_ready_for_reasoning() helper and the "
            "TaskState.transition() docstring contract forbid "
            "``state.status = X`` after ``except ValueError``.  If "
            "truly necessary, bump the count in S5B_BACKLOG_FILES + "
            "add the ``# s5b-allow-force-write`` token + add a "
            "release-note entry.\n"
            "If you DELETED a force-write: congratulations, you're "
            "doing S5-B's work — drop the count in S5B_BACKLOG_FILES "
            "and update release notes."
        )

    def test_every_force_write_has_recognised_opt_in_token(self) -> None:
        """Each force-write carries ONE of two tokens within ±5 lines
        of its ``except ValueError:`` keyword:

          • ``s5b-allow-force-write``       — temporary, S5-B deletes
          • ``cancel-idempotent-force-write`` — permanent, architectural

        Tokens serve as code-review prompts AND machine-checkable
        opt-in.  A fresh force-write added without either token trips
        this test even if the count test is silenced for unrelated
        reasons."""
        missing: list[tuple[str, int, str]] = []
        wrong_kind: list[tuple[str, int, str, str]] = []
        for path, kind_count_expected in {
            **{p: S5B_TOKEN for p in S5B_BACKLOG_FILES},
            **{p: CANCEL_TOKEN for p in ARCH_FORCE_WRITE_FILES},
        }.items():
            findings = _find_force_writes(path)
            for lineno, surface in findings:
                token = _opt_in_token_near(path, lineno)
                rel = str(path.relative_to(REPO_ROOT))
                if token is None:
                    missing.append((rel, lineno, surface))
                elif token != kind_count_expected:
                    wrong_kind.append((rel, lineno, surface, token))
        assert missing == [], (
            "Force-writes without any opt-in token within "
            f"±{TOKEN_PROXIMITY_LINES} lines of ``except ValueError:``:\n"
            + "\n".join(f"  {f}:{lineno} {surface}" for f, lineno, surface in missing)
            + "\n\n"
            "Add ``# s5b-allow-force-write`` (S5-B backlog) or "
            "``# cancel-idempotent-force-write`` (cancel() permanent) "
            "to the except-line, OR refactor to use "
            "TaskState.transition() / .ensure_ready_for_reasoning()."
        )
        assert wrong_kind == [], (
            "Force-writes labelled with the wrong token kind for "
            "their file:\n"
            + "\n".join(
                f"  {f}:{lineno} {surface} has {used_token!r}, "
                f"expected {S5B_TOKEN!r} (S5-B backlog) for "
                f"reasoning_engine.py or {CANCEL_TOKEN!r} (cancel())"
                for f, lineno, surface, used_token in wrong_kind
            )
        )

    def test_opt_in_tokens_only_appear_near_actual_force_writes(self) -> None:
        """Defensive: tokens can't be sprinkled freely to silence
        other tests.  Each token occurrence must sit near a real
        AST-detected force-write."""
        for path in ALLOWED_FORCE_WRITE_FILES:
            source = path.read_text(encoding="utf-8")
            lines = source.splitlines()
            token_linenos = [
                i + 1 for i, line in enumerate(lines) if any(tk in line for tk in RECOGNISED_TOKENS)
            ]
            findings = _find_force_writes(path)
            force_write_linenos = {lineno for lineno, _ in findings}

            for token_lineno in token_linenos:
                window_start = token_lineno - TOKEN_PROXIMITY_LINES
                window_end = token_lineno + TOKEN_PROXIMITY_LINES
                nearby = any(
                    window_start <= fw_line <= window_end for fw_line in force_write_linenos
                )
                # Allow tokens in docstrings / comments that describe
                # the protocol (not opt-in markers); detect by
                # checking the line starts with a ``"""`` or a ``#``
                # quote-like context.  Real markers always sit on or
                # next to an ``except ValueError:`` line.
                if not nearby:
                    line = lines[token_lineno - 1].lstrip()
                    is_doc_reference = (
                        (line.startswith("#") and "token" in line.lower())
                        or line.startswith('"""')
                        or "``" in line
                    )
                    if is_doc_reference:
                        continue
                    assert nearby, (
                        f"Stale opt-in token at "
                        f"{path.relative_to(REPO_ROOT)}:{token_lineno} "
                        f"— no force-write detected within "
                        f"±{TOKEN_PROXIMITY_LINES} lines, and the line "
                        f"doesn't look like a documentation reference. "
                        f"Remove the orphan token or restore the "
                        f"force-write it was meant to opt-in."
                    )


@pytest.mark.parametrize(
    "expected_target",
    [
        "REASONING",
        "VERIFYING",
        "ACTING",
        "WAITING_USER",
        "OBSERVING",
        "FAILED",
        "MODEL_SWITCHING",
    ],
)
def test_each_known_force_write_target_is_present(expected_target: str) -> None:
    """Inventory pin: the 7 distinct TaskStatus values currently
    force-written (across the 9 force-write sites).  When S5-B
    deletes a force-write, the corresponding parametrize entry
    should be removed from this list.

    Concrete site → target mapping at v1.28.3-pre:

    | site            | target(s) |
    |-----------------|-----------|
    | 4462 (S5-A)     | REASONING |
    | 5030 ternary    | FAILED / COMPLETED |
    | 5073            | VERIFYING |
    | 5088            | ACTING |
    | 5490            | WAITING_USER |
    | 6309            | OBSERVING |
    | 6650            | FAILED |
    | 6734            | FAILED |
    | 9023 (model_sw) | MODEL_SWITCHING |
    """
    path = SRC_ROOT / "core" / "reasoning_engine.py"
    findings = _find_force_writes(path)
    surfaces = " ".join(s for _, s in findings)
    assert expected_target in surfaces, (
        f"Expected force-write target TaskStatus.{expected_target} "
        f"not found in {path.relative_to(REPO_ROOT)}.\n"
        f"Findings:\n" + "\n".join(f"  line {lineno}: {s}" for lineno, s in findings) + "\n\n"
        f"If S5-B intentionally deleted the {expected_target} force-write, "
        f"remove ``{expected_target}`` from this parametrize list."
    )
