"""Shared fixtures for the orgs/ parity harness.

The fixtures here are stubs the P9.1..P9.6 commits will fill in.
Until then every fixture raises NotImplementedError so any test
that accidentally drops xfail without providing a real
fixture fails loudly rather than silently passing on an empty
input.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def sample_org_dir(tmp_path):
    """A scratch org dir under the pytest tmp_path.

    P9.1+ will populate this with a sample ``org.json`` +
    ``state.json`` so per-subsystem parity tests can compare
    v1 vs v2 reads against a stable baseline.
    """
    org_dir = tmp_path / "sample_org"
    org_dir.mkdir()
    return org_dir


@pytest.fixture
def sample_org_id() -> str:
    """A deterministic org_id used across the orgs/ parity suite."""
    return "org_parity_sample"
