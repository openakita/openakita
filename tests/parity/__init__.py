"""V1 vs V2 parity test harness for the Phase 2 agent rewrite.

This package hosts the framework that lets us check "the new
agent/* implementation behaves the same as the old core/*
implementation for the same input". The REWRITE slices in Phase
2 commits 14–18 use it as an acceptance gate (G2 requires
≥95% parity over 30 cases).

Layout:

* :mod:`tests.parity.harness` — datatypes (``ParityCase``,
  ``ParityResult``) and assertion helpers (``assert_parity``).
* :mod:`tests.parity.runners` — small adapters that take a
  ``ParityCase`` and emit a ``ParityResult`` for either the v1
  or v2 path. They are intentionally pluggable: the REWRITE
  commits will swap in real reasoning-engine drivers without
  touching the cases or the test file.
* :mod:`tests.parity.cases` — registry of baseline cases.
* :mod:`tests.parity.test_baseline_parity` — pytest entry point
  that parametrises over the registry.

NOTE: tests under this package never call the network or real
LLMs. They exercise the deterministic boundary modules already
shared by v1 and v2 (permission engine, persona merge, token
budget, loop-budget guard, output-guard), so the parity test
suite stays fast and hermetic.
"""
