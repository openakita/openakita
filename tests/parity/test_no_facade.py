"""Structural facade detector for the v2 `agent/*` rewrite targets.

The continuation plan section 0.2 calls out the
**facade-self-equivalence false positive**: a v2 module that is
nothing more than `from openakita.core.<x> import *` will trivially
"pass" parity tests because its public surface *is* the v1 surface.

This test file is the structural backstop for that loophole. For each
of the five Phase-2 REWRITE targets

    src/openakita/agent/core.py
    src/openakita/agent/reasoning.py
    src/openakita/agent/brain.py
    src/openakita/agent/tools.py
    src/openakita/agent/context.py

we require *one* of the following:

* the file declares its facade status with the sentinel comment

      # REVAMP-FACADE-ALLOWED-UNTIL: P-RC-4
      # REVAMP-FACADE-ALLOWED-UNTIL: P-RC-5
      # REVAMP-FACADE-ALLOWED-UNTIL: P-RC-6

  (these are removed by the corresponding P-RC-X commit when the
  real implementation lands), **or**

* the file has at least 200 "real" SLOC, where "real" means
  non-blank, non-comment, non-import lines outside any
  module-level docstring.

In P-RC-0 every facade carries the sentinel; the test passes. As
each phase rewrites a facade into a real implementation, the
sentinel is removed and the SLOC threshold takes over.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FACADE_FILES = [
    'src/openakita/agent/core.py',
    'src/openakita/agent/reasoning.py',
    'src/openakita/agent/brain.py',
    'src/openakita/agent/tools.py',
    'src/openakita/agent/context.py',
]

SENTINEL_RE = re.compile(
    r'^\s*#\s*REVAMP-FACADE-ALLOWED-UNTIL:\s*P-RC-(?P<phase>[4-6])\s*$',
    re.MULTILINE,
)
SLOC_FLOOR = 200


def _module_docstring_lineno_range(tree: ast.Module) -> tuple[int, int] | None:
    if not tree.body:
        return None
    first = tree.body[0]
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
        if isinstance(first.value.value, str):
            return (first.lineno, first.end_lineno or first.lineno)
    return None


def _real_sloc(text: str, tree: ast.Module) -> int:
    """Count non-blank, non-comment, non-import lines outside the module docstring."""
    docstring = _module_docstring_lineno_range(tree)
    docstring_lines = (
        set(range(docstring[0], docstring[1] + 1)) if docstring else set()
    )
    import_lines: set[int] = set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            start = node.lineno
            end = node.end_lineno or node.lineno
            import_lines.update(range(start, end + 1))
    n = 0
    for idx, line in enumerate(text.splitlines(), start=1):
        if idx in docstring_lines or idx in import_lines:
            continue
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith('#'):
            continue
        n += 1
    return n


@pytest.mark.parametrize('rel', FACADE_FILES, ids=lambda p: p.split('/')[-1])
def test_facade_files_either_declare_sentinel_or_have_real_body(rel: str) -> None:
    """Each rewrite-target file must either be a real implementation or
    carry the temporary facade sentinel."""
    path = REPO_ROOT / rel
    text = path.read_text(encoding='utf-8')
    tree = ast.parse(text, filename=str(path))

    if SENTINEL_RE.search(text):
        # Sentinel present -> the file is a known facade, allowed for
        # P-RC-0..3. No SLOC check needed in this branch.
        return

    sloc = _real_sloc(text, tree)
    assert sloc >= SLOC_FLOOR, (
        f'{rel}: {sloc} real SLOC, below floor {SLOC_FLOOR}; the file '
        'looks like a facade but is missing the '
        '"# REVAMP-FACADE-ALLOWED-UNTIL: P-RC-4|5|6" sentinel. Either '
        'add the sentinel (intentional facade) or grow the real '
        'implementation past the floor.'
    )


def test_known_facade_sentinel_phases_are_in_plan_range() -> None:
    """Sanity check: every sentinel currently in-tree references a phase
    we actually intend to remove the facade in."""
    seen: dict[str, str] = {}
    for rel in FACADE_FILES:
        text = (REPO_ROOT / rel).read_text(encoding='utf-8')
        m = SENTINEL_RE.search(text)
        if m:
            seen[rel] = 'P-RC-' + m.group('phase')
    # Every match must be one of the three planned phases. The regex
    # already restricts matches to 4..6; this assertion just makes the
    # failure mode explicit if someone widens the regex later.
    invalid = {k: v for k, v in seen.items() if v not in {'P-RC-4', 'P-RC-5', 'P-RC-6'}}
    assert not invalid, f'sentinel phases out of range: {invalid}'


@pytest.mark.parametrize('rel', FACADE_FILES, ids=lambda p: p.split('/')[-1])
def test_facade_sentinel_has_not_expired(rel: str) -> None:
    """Forbid a sentinel from outliving its declared phase.

    The continuation plan section 0.2 calls this the "P-RC-X facade
    allowance" -- ``# REVAMP-FACADE-ALLOWED-UNTIL: P-RC-N`` permits a
    thin facade body, but only until the phase counter advances past
    ``N``. The P-RC-0 auditor (N1) flagged that the sentinel had no
    real expiry: a commit could keep the facade alive past P-RC-N
    without the test catching it.

    Enforcement: the ledger header ``current_phase: P-RC-M`` is parsed
    via :mod:`tests.revamp._ledger`; if ``M > N`` for a file that
    still carries the sentinel, the test fails loudly.
    """
    from tests.revamp._ledger import current_phase, phase_as_int

    text = (REPO_ROOT / rel).read_text(encoding='utf-8')
    match = SENTINEL_RE.search(text)
    if not match:
        # No sentinel -> the SLOC-floor test
        # (test_facade_files_either_declare_sentinel_or_have_real_body)
        # already enforces a real body. Nothing to do here.
        return
    allowed_until_int = int(match.group('phase'))
    current_int = phase_as_int(current_phase())
    assert current_int <= allowed_until_int, (
        f'{rel}: facade allowance expired -- sentinel says '
        f'P-RC-{allowed_until_int} but ledger current_phase is '
        f'P-RC-{current_int}. Drop the sentinel and ship a real '
        f'implementation, or postpone the phase bump in '
        f'docs/revamp/PROGRESS_LEDGER.md.'
    )
