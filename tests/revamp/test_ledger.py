"""Tests for :mod:`tests.revamp._ledger`.

These guards make sure the sentinel-expiry enforcement in
``tests/parity/test_no_facade.py`` has a trustworthy parser
underneath it. Two cases:

* the production ``PROGRESS_LEDGER.md`` parses cleanly to a
  ``P-RC-N`` string;
* a ledger missing the ``current_phase:`` header raises
  :class:`LedgerError` rather than silently returning a wrong
  phase.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.revamp._ledger import (
    PROGRESS_LEDGER_PATH,
    LedgerError,
    current_phase,
    phase_as_int,
)


def test_current_phase_parses_production_ledger() -> None:
    """The on-disk ledger must always advertise a parseable phase."""
    phase = current_phase()
    assert phase.startswith('P-RC-'), phase
    # phase_as_int must accept the same string the parser produced.
    assert phase_as_int(phase) >= 0
    # And the file the parser reads is the real one shipped in-repo.
    assert PROGRESS_LEDGER_PATH.exists()


def test_current_phase_raises_when_header_missing(tmp_path: Path) -> None:
    """A ledger without the header line is treated as a hard error."""
    bad = tmp_path / "PROGRESS_LEDGER.md"
    bad.write_text(
        '# Revamp Progress Ledger\n\nNo phase marker here.\n',
        encoding='utf-8',
    )
    with pytest.raises(LedgerError, match='missing'):
        current_phase(bad)
