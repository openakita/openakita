"""Lightweight reader for docs/revamp/PROGRESS_LEDGER.md.

The continuation plan threads a single ``current_phase: P-RC-N``
line through the top of ``PROGRESS_LEDGER.md`` so the rest of the
test suite (notably ``tests/parity/test_no_facade.py``) can pin
phase-scoped sentinels against a real, machine-readable phase
counter instead of trusting that humans will manually remove the
sentinel before the phase expires.

This module is the canonical parser. It is intentionally tiny and
has no dependencies outside the standard library so that it can
load even when test collection is the only thing running.
"""

from __future__ import annotations

import re
from pathlib import Path

__all__ = [
    "LedgerError",
    "PROGRESS_LEDGER_PATH",
    "current_phase",
    "phase_as_int",
]

REPO_ROOT = Path(__file__).resolve().parents[2]
PROGRESS_LEDGER_PATH = REPO_ROOT / "docs" / "revamp" / "PROGRESS_LEDGER.md"

_PHASE_LINE_RE = re.compile(
    r"^current_phase:\s*(?P<phase>P-RC-(?P<num>\d+))\s*$",
    re.MULTILINE,
)


class LedgerError(RuntimeError):
    """Raised when the ledger is missing the ``current_phase`` header."""


def current_phase(path: Path | None = None) -> str:
    """Return the ledger's declared phase, e.g. ``"P-RC-1"``.

    Args:
        path: optional override for ``PROGRESS_LEDGER.md``; mostly
            used in tests to point the parser at a temporary file.

    Raises:
        LedgerError: when the file is missing the
            ``current_phase: P-RC-N`` line. Failing loudly here is the
            whole point of the helper -- the sentinel enforcement test
            relies on it.
    """
    target = path or PROGRESS_LEDGER_PATH
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise LedgerError(
            f"PROGRESS_LEDGER.md not found at {target}"
        ) from exc
    match = _PHASE_LINE_RE.search(text)
    if not match:
        raise LedgerError(
            f"PROGRESS_LEDGER.md at {target} is missing a "
            "\"current_phase: P-RC-N\" header line"
        )
    return match.group("phase")


def phase_as_int(phase: str) -> int:
    """Parse ``"P-RC-N"`` into ``N``. Raises on bad input."""
    match = re.fullmatch(r"P-RC-(\d+)", phase)
    if not match:
        raise LedgerError(f"unrecognised phase string: {phase!r}")
    return int(match.group(1))
