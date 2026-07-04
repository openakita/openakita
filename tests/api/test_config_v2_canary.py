"""Tests for the ``runtime_v2_canary_orgs`` settings field.

P-RC-1 commit 6. Covers the three cases the spec calls out:

* default empty (preserves Phase-7 behaviour: nobody is canary);
* CSV ``RUNTIME_V2_CANARY_ORGS=org_a,org_b`` env var parses to a
  :class:`set`;
* programmatic construction with a Python container passes through
  unchanged.

Plus a small whitespace / empty-fragment tidying check so a stray
trailing comma in ``.env`` does not produce a phantom org id.
"""

from __future__ import annotations

import pytest

from openakita.config import Settings


def test_default_canary_orgs_is_empty() -> None:
    s = Settings()
    assert s.runtime_v2_canary_orgs == set()


def test_csv_environment_value_parses_into_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNTIME_V2_CANARY_ORGS", "org_alpha,org_beta,org_gamma")
    s = Settings()
    assert s.runtime_v2_canary_orgs == {"org_alpha", "org_beta", "org_gamma"}


def test_programmatic_set_construction_is_accepted() -> None:
    s = Settings(runtime_v2_canary_orgs={"x", "y"})
    assert s.runtime_v2_canary_orgs == {"x", "y"}


def test_csv_strips_whitespace_and_drops_empty_fragments() -> None:
    s = Settings(runtime_v2_canary_orgs="  org_a , ,, org_b ,")
    assert s.runtime_v2_canary_orgs == {"org_a", "org_b"}


def test_list_input_also_works() -> None:
    s = Settings(runtime_v2_canary_orgs=["a", "b", "c"])
    assert s.runtime_v2_canary_orgs == {"a", "b", "c"}
