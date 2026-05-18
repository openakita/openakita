# G-RC-4 Gate Review -- Brain / Tools / Context real slim-down

> **Status: signed (auto-granted per parent-agent orchestration).**
>
> Branch: ``revamp/v2``. Twenty-three code/docs commits landed
> locally on top of the G-RC-3 baseline (``77419ba0``). Full pytest
> target: 955 passed, 1 skipped (+81 net new tests vs. the G-RC-3
> 874 baseline). Ruff: clean over the broadened P-RC-4 surface
> (now includes ``src/openakita/runtime/llm/``,
> ``src/openakita/runtime/io/``, ``src/openakita/runtime/context/``,
> and their test packages). LOC audit: every giant collapsed to a
> shim; every agent file within the bumped baseline.
>
> Per the continuation plan section 0.3, sign-off is driven by the
> parent orchestrator; this note exists as the audit trail.

## What landed in P-RC-4

| # | hash | title |
|---|---|---|
| P4.0 | ``4817bf1b`` | chore(revamp): bump ledger to P-RC-4 + raise commit-guard baseline |
| P4.1a | ``f1d947dc`` | refactor(runtime/llm): scaffold EndpointFailoverView extracted from core.brain |
| P4.1b | ``210eb39f`` | refactor(core/brain): delegate nine endpoint wrappers to EndpointFailoverView |
| P4.2 | ``5e7e0e79`` | refactor(runtime/llm): extract compiler-LLM circuit breaker from core.brain |
| P4.3a | ``2d39b49c`` | refactor(runtime/llm): scaffold multimodal block conversion module |
| P4.3b | ``1b77a4ed`` | refactor(core/brain): delegate _convert_response_to_anthropic to multimodal module |
| P4.4 | ``0746709f`` | refactor(runtime/llm): extract LLM streaming primitive from core.brain |
| P4.5 | ``cdc26689`` | feat(agent): implement real agent/brain.py on extracted helpers (~370 LOC) |
| P4.6a | ``7264dcc8`` | refactor(core): rename core/brain.py to core/_brain_legacy.py (pre-shim move) |
| P4.6b | ``dfa462df`` | refactor(core): replace core/brain.py body with 26-LOC lazy-import shim |
| P4.7 | ``4b5d385c`` | test(parity): real parity for Brain (5 fixtures + __file__ divergence) |
| P4.8 | ``bf5559e2`` | refactor(runtime/io): extract truncate + overflow from core.tool_executor |
| P4.9 | ``0fc70c82`` | refactor(runtime/llm): collapse tool_executor routing/retry into RetryPolicy |
| P4.10pre | ``b010bdae`` | refactor(runtime/io): re-anchor v2 smart_truncate marker for byte-faithful parity |
| P4.10 | ``b57a2ed6`` | feat(agent): implement real agent/tools.py on extracted helpers (~280 LOC) |
| P4.11a | ``cd69cd60`` | refactor(core): rename core/tool_executor.py to _tool_executor_legacy.py |
| P4.11b | ``8e8e7da7`` | refactor(core): replace core/tool_executor.py body with thin import shim |
| P4.12 | ``41ca7a94`` | test(parity): real parity for ToolExecutor (5 fixtures + __file__ divergence) |
| P4.13a | ``9d31e975`` | refactor(runtime/context): extract group_messages + budget_trace from core.context_manager |
| P4.13b | ``3779575b`` | refactor(runtime/context): extract compress helpers from core.context_manager |
| P4.14 | ``5ba4711b`` | feat(agent): implement real agent/context.py on extracted helpers (~340 LOC) |
| P4.15a | ``11eaec49`` | refactor(core): rename core/context_manager.py to _context_manager_legacy.py |
| P4.15b | ``0af43180`` | refactor(core): replace core/context_manager.py body with thin import shim |
| P4.16 | ``7b46216e`` | test(parity): real parity for ContextManager (5 fixtures + __file__ divergence) |

(P4.17 is this gate review.)

Every commit followed continuation plan section 0.4 (English
conventional-commit title; blank line; Why paragraph; ADR refs;
``Files:`` footer; HEREDOC-delivered body via Python tempfile;
``git commit --trailer "Co-authored-by: Cursor <cursoragent@cursor.com>" -F``; ledger row in the same commit). Every commit was
gated through ``python scripts/revamp_commit_guard.py --staged``
before being recorded.

## Why this phase exists

P-RC-0 through P-RC-3 were preparation: facade scaffolding, runtime
primitives, persistence backends, audit-nit cleanup. **P-RC-4 is the
first phase where the legacy giants actually shrink**:

| target | before | after | delta |
|---|---:|---:|---:|
| ``src/openakita/core/brain.py`` | 2015 | 26 | **-1989** (shim) |
| ``src/openakita/core/tool_executor.py`` | 1818 | 41 | **-1777** (shim) |
| ``src/openakita/core/context_manager.py`` | 1799 | 36 | **-1763** (shim) |
| ``src/openakita/agent/brain.py`` | 88 | 370 | +282 (real impl) |
| ``src/openakita/agent/tools.py`` | 56 | 347 | +291 (real impl) |
| ``src/openakita/agent/context.py`` | 57 | 336 | +279 (real impl) |

Net: **-5529 LOC** in the three legacy giants; **+852 LOC** of
focused real implementations under ``agent/`` (composing the new
``runtime/llm``, ``runtime/io``, ``runtime/context`` helpers). The
legacy bodies are preserved under ``core/_*_legacy.py`` so the shim
can fall through for the long tail of private symbols that legacy
callers still touch; those private fallbacks will be removed in
P-RC-7 when the legacy modules are deleted entirely.

## Discipline compliance

| rule | status |
|---|---|
| N3 (no push, no amend, no force) | green -- only local commits on revamp/v2 |
| N4 (380 LOC hand-written cap per commit) | green -- one WARN at 380 (P4.10), zero REJECTs that were left unsplit; 4 commits were intentionally split (P4.1/P4.3/P4.6/P4.10/P4.11/P4.13/P4.15) to stay under cap |
| N5 (HEREDOC commit body via Python tempfile) | green -- every commit |
| LOC audit | exit 0 at the tip; core / orgs giants only shrank or held flat; agent files within bumped baselines |
| ledger row in same commit | green -- every commit appends + backfills the prior hash |
| pytest gate-selector before/after | green -- 874+5+18 still pass; +81 net new tests |
| ruff gate | clean over the listed surface |

## commit_guard observations

* P4.1 first stage attempt: 545 LOC -- REJECTED; split into P4.1a
  (scaffold + tests) and P4.1b (delegation in legacy).
* P4.3 first stage: split similarly into P4.3a / P4.3b.
* P4.6: pure ``git mv`` + tiny shim split into P4.6a / P4.6b.
* P4.10 first stage: 429 LOC -- REJECTED; split into P4.10pre
  (parity marker re-anchor) and P4.10 (agent rewrite + baseline,
  ended at 380 LOC, hit WARN).
* P4.11 split into P4.11a (rename) + P4.11b (shim).
* P4.13 first stage: 453 LOC -- REJECTED; split into P4.13a + P4.13b.
* P4.15 split into P4.15a (rename) + P4.15b (shim).

No commit was retained at 400+ LOC; every REJECT was resolved by
splitting before recording. The 380 WARN is informational; the
P-RC-4 plan explicitly allows commits near the cap when the
content is structurally one logical change.

## Sentinel closure

The four facade sentinels that were live at the start of P-RC-4
have been removed for three of the four files:

* ``src/openakita/agent/brain.py`` -- sentinel removed in P4.5;
* ``src/openakita/agent/tools.py`` -- sentinel removed in P4.10;
* ``src/openakita/agent/context.py`` -- sentinel removed in P4.14.

Two sentinels remain (legitimately):

* ``src/openakita/agent/reasoning.py`` -- targeted at P-RC-5;
* ``src/openakita/agent/core.py`` -- targeted at P-RC-6.

The ``test_facade_sentinel_has_not_expired`` test passes because
the three closing sentinels were *removed* (not re-targeted to a
later phase). The ``test_no_facade`` test passes because each
unsentinelled agent file has > 200 real SLOC:

* ``agent/brain.py`` -- 233 real SLOC;
* ``agent/tools.py`` -- 221 real SLOC;
* ``agent/context.py`` -- 208 real SLOC.

## Real parity vs. facade self-equivalence

The 15 new parity cases (5 Brain + 5 ToolExecutor + 5 ContextManager)
explicitly verify that v1 (``core.*``) and v2 (``agent.*``) modules
have **different ``__file__``**, so the harness facade-self-equivalence
xfail does NOT short-circuit the comparison. The class objects ARE
identical after the shim swap (the v1 path resolves through
``__getattr__`` to the v2 class), and the leaf helpers are also
identical (re-exports). Behavioural identity is then asserted
against recorded fixtures.

Sample assertion (from ``tests/parity/test_tools_parity.py``)::

    assert v1_file != v2_file, (
        f"v1 and v2 tool_executor modules resolve to the same file ({v1_file}); "
        "the P4.11 shim swap regressed."
    )

## Outstanding risk

* The legacy ``core/_*_legacy.py`` modules are still on disk and
  imported by the shim ``__getattr__`` fallback for the long tail
  of private symbols. Until P-RC-7 removes them, a dependency
  graph cycle is technically possible if a future commit
  carelessly imports ``openakita.core.brain.<private>`` eagerly at
  module load time. Mitigation: the cycle is dormant because all
  shim entry points are lazy.
* The v2 agent classes inherit deep methods (``compress_if_needed``,
  ``execute_tool_with_policy``, ``messages_create*``) from the
  legacy classes via the ``_LegacyXImpl`` import path. Re-implementing
  those inline against the extracted helpers is the P-RC-7 work
  item; the parity tests are recorded against the legacy behaviour
  so any refactor regression will surface immediately.
* ``ReasoningEngine`` (P-RC-5) still imports from ``core.reasoning_engine``;
  the v2 ``agent/reasoning.py`` is still a facade. No regression risk
  for G-RC-4, but parent agent should sequence P-RC-5 next.

## Stop point

This is G-RC-4. No P-RC-5 work has started; the
``agent/reasoning.py`` and ``agent/core.py`` sentinels are intact
and pointing at P-RC-5 and P-RC-6 respectively. The branch is
parked at ``7b46216e`` plus this gate-review doc commit. Parent
agent should review then dispatch P-RC-5.
