"""Pytest entry point for the revamp LOC invariant audit.

Per the post-RC continuation plan §0.1, every commit on
`revamp/v2` must keep the legacy `core/*` and `orgs/*`
giants from growing, and must keep the `agent/*` files within
their phase-specific cap. This test file pins
:mod:scripts.revamp_loc_audit into the regular pytest run so
the rule cannot be bypassed silently — a violating commit fails
`python -m pytest tests/revamp -q` deterministically.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _load_audit_module():
    """Import `scripts/revamp_loc_audit.py` by file path.

    `scripts/` is not a Python package (no `__init__.py`) and
    does not appear on `sys.path` automatically, so the test
    loads it via `importlib` to avoid surprising the rest of
    the test runner. Cached on the module attribute so repeated
    invocations stay cheap.
    """
    cached = getattr(_load_audit_module, "_cached", None)
    if cached is not None:
        return cached
    repo_root = Path(__file__).resolve().parents[2]
    audit_path = repo_root / "scripts" / "revamp_loc_audit.py"
    spec = importlib.util.spec_from_file_location(
        "revamp_loc_audit", audit_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load audit module from {audit_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["revamp_loc_audit"] = mod
    spec.loader.exec_module(mod)
    _load_audit_module._cached = mod  # type: ignore[attr-defined]
    return mod


def test_loc_invariants_hold(capsys: pytest.CaptureFixture[str]) -> None:
    """All tracked files must be within their per-phase LOC cap.

    The audit prints its table to stdout when it fails; capsys
    surfaces it inside the pytest failure output so a developer
    sees exactly which file blew through which cap without having
    to re-run the script by hand.
    """
    mod = _load_audit_module()
    rc = mod.audit(verbose=False)
    captured = capsys.readouterr()
    if rc != 0:
        # Re-print so pytest -q still shows the table.
        print(captured.out)
        print(captured.err)
    assert rc == 0, "LOC audit reported violations; see table above."


def test_baseline_contains_every_tracked_file() -> None:
    """The baseline JSON must list every file the audit tracks.

    Catches the merge bug where someone adds a new entry to
    :data:TRACKED_FILES without rerunning `--init` to seed
    its baseline. Without this guard the new file would silently
    audit against `baseline=0` and trip every commit.
    """
    mod = _load_audit_module()
    baseline = mod.load_baseline()
    missing = [p for p in mod.TRACKED_FILES if p not in baseline]
    assert not missing, f"Baseline JSON is missing entries for: {missing}"
