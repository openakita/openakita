"""Pytest entry point for the parity baseline.

Each case in :data:`BASELINE_CASES` is run through both the v1
and v2 runners and compared with :func:`assert_parity`. The
suite is intentionally hermetic — no LLM calls, no network, no
sleeps — so it stays fast (sub-second) and reliable on CI.

When commit 14 onwards swaps the v2 runner for real rewritten
modules, the cases here continue to act as guardrails. Commit 19
expands the registry to 30 cases for G2.
"""

from __future__ import annotations

import pytest

from .cases import BASELINE_CASES
from .harness import ParityCase, assert_parity
from .runners import run_v1, run_v2


@pytest.mark.parametrize("case", BASELINE_CASES, ids=[c.id for c in BASELINE_CASES])
def test_baseline_parity(case: ParityCase) -> None:
    v1 = run_v1(case)
    v2 = run_v2(case)
    assert_parity(v1, v2, case=case)


def test_registry_complete() -> None:
    """Every baseline case must have both a v1 and v2 runner registered."""
    from .runners import V1_RUNNERS, V2_RUNNERS

    needed = {case.kind for case in BASELINE_CASES}
    assert needed.issubset(V1_RUNNERS.keys()), needed - V1_RUNNERS.keys()
    assert needed.issubset(V2_RUNNERS.keys()), needed - V2_RUNNERS.keys()


def test_runners_handle_unknown_kind() -> None:
    """Dispatch must raise KeyError for an unregistered kind so a missing
    runner is loud, not silent."""
    fake = ParityCase(id="nope", kind="not_a_real_kind")
    with pytest.raises(KeyError):
        run_v1(fake)
    with pytest.raises(KeyError):
        run_v2(fake)
