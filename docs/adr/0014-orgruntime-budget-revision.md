# ADR-0014 -- OrgRuntime budget revision (P9.6)

- **Status**: Accepted
- **Date**: 2026-05-19
- **Phase**: P-RC-9 P9.6.plan (will inform G-RC-9.6 mini-gate)
- **Decision owner**: project owner
- **Implementer**: AI agent on ``revamp/v3-orgs``

## Context

P-RC-9-PLAN.md section 4 P9.6 originally allocated **1 200 src
LOC** + **600 test LOC** + 10-15 commits for the OrgRuntime
subsystem -- the largest item on the charter (v1
``src/openakita/orgs/runtime.py``).

Empirical recon in the **P9.6 turn-1 escape-hatch report**
(2026-05-19, ledger row immediately after P9.6.nit ``e4b59137``)
showed the original budget was incompatible with reality:

* v1 ``runtime.py`` is **6 355 LOC across 132 methods** in
  one ``OrgRuntime`` class plus a small ``_CachedAgent``
  helper.
* Top-10 methods alone are **~2 400 LOC** (the single largest
  method ``_activate_and_run_inner`` is 556 LOC).
* Cross-cutting state references: **254 occurrences of
  ``tracker`` + 221 ``chain_id``** -- the tracker / chain
  finalisation logic is woven through dispatch, lifecycle,
  message routing, and watchdog code paths.
* ``__init__`` alone is 168 LOC wiring 10+ subsystems
  (manager / messenger / event_store / heartbeat / inbox /
  notifier / scaler / reporter / scheduler / failure_diagnoser
  / plugin_assets / tool_handler).
* 27 inline (lazy) cross-package imports indicate v1 already
  uses lazy-import as a workaround for circular dependencies.

Compressing 6 355 LOC of behaviour into 1 200 LOC means either
(a) a thin-wrapper-over-v1 (which violates **ADR-0012**: no
shim under v1; v2 must be able to delete v1 in P9.8), or (b)
deferring most of v1's behaviour out of v2's surface (which
breaks **P-RC-9-PLAN section 5.2** parity gate: state graph
+ checkpoint sequence equality demands functional equivalence).

The P9.6 turn-1 report invoked the user-brief escape hatch
"STOP and report -- consider 2-sibling or no-sibling layout
instead". Per the project owner's reply, **option C (revised)**
was chosen: expand v2 src budget so v2 OrgRuntime is a real
rewrite (not a thin wrapper) and P9.8 deletion of v1 stays
feasible.

## Decision

Revise P9.6 budgets in P-RC-9-PLAN.md section 4 P9.6:

| Axis | Old | New | Delta |
|---|---|---|---|
| src LOC | 1 200 | **~3 000** | +1 800 |
| test LOC | 600 | **~900** | +300 (20 parity + ~25 contract) |
| commits | 10-15 | **12-15 across 2-3 turns** | turn-bounded |
| sibling modules | not specified | **7 siblings <= 500 LOC each** | new ceiling |

The v2 OrgRuntime decomposes into ``runtime.py`` + 7
sibling modules under ``src/openakita/runtime/orgs/``:

* ``runtime.py`` (~400) -- class skeleton + 3 new Protocol
  impls + ``CommandRuntimeProtocol`` surface (the contract
  P9.4 OrgCommandService consumes).
* ``_runtime_agent_pipeline.py`` (~500) -- the
  ``_activate_and_run_inner`` rewrite + agent cache.
* ``_runtime_dispatch.py`` (~500) -- send_command /
  cancel_user_command + tracker + chain helpers.
* ``_runtime_node_lifecycle.py`` (~400) -- node state machine
  + message routing.
* ``_runtime_lifecycle.py`` (~300) -- org start / stop /
  restart / health.
* ``_runtime_plugin_assets.py`` (~400) -- plugin asset record.
* ``_runtime_watchdog.py`` (~250) -- command + idle watchdogs.
* ``_runtime_event_bus.py`` (~150) -- emit / subscribe / WS.

**Three new Protocols** (each <= 5 methods per ADR-0011
granularity ceiling): ``RuntimeStateProtocol`` (4 methods) +
``NodeLifecycleProtocol`` (5 methods) + ``EventBusProtocol``
(4 methods). Total new Protocols: 3; total methods added: 13.

**Six reused Protocols** (no redefinition; from prior P9.x
work): ``OrgLookupProtocol`` (P9.4 / P9.5),
``OrgPersistenceProtocol`` + ``OrgLifecycleEmitterProtocol``
+ ``OrgFactoryProtocol`` (P9.5), ``BlackboardBackendProtocol``
(P9.1), ``NodeSchedulerProtocol`` (P9.3). One **implemented**
Protocol: ``CommandRuntimeProtocol`` (P9.4 contract; OrgRuntime
IS its canonical implementation -- closes the P9.4 loop).

Per-commit gate discipline **unchanged**: <= 380 WARN,
target <= 350, REJECT at 400. With 3 000 src LOC across
8-10 substantive commits + 900 test LOC across 3-4 commits +
1 mini-gate doc, the cumulative budget is achievable inside
the per-commit ceiling.

## Consequences

* **+1 800 LOC** of v2 production code beyond the original
  P9.6 plan. Net P-RC-9 budget effectively grows from
  charter ~4 800 LOC v2 src to ~6 600 LOC v2 src.
* **8 sibling modules** under ``runtime/orgs/`` (runtime.py +
  7 underscore-prefixed siblings). Each capped at <= 500
  LOC so single-file complexity stays manageable.
* **2-3 turns** to land P9.6 (alpha turn 1: 4-5 commits;
  beta turn 2: 4-5 commits; gamma turn 3: 3-4 commits +
  mini-gate). Previous estimate of 1 turn was unrealistic.
* **G-RC-9.6 mini-gate doc** grows proportionally
  (~400-500 LOC vs. the ~300-400 originally projected).
* **P9.7 (REST endpoint mint) onset slips** by 1-2 turns
  relative to original calendar but is unaffected in scope.
* **P9.8 (v1 ``orgs/`` deletion) becomes feasible** because
  v2 is now a real rewrite, not a wrapper -- otherwise the
  deletion would be blocked.

## Alternatives rejected

* **Option A (Protocol wrapper + 2-sibling layout + shape
  parity)**: violates ADR-0012 (v2 cannot delete v1 if it
  wraps v1); reduces parity to a self-reflexive tautology.
  Rejected.
* **Option B (multi-turn but stay at 1 200 LOC budget)**:
  re-spreads the same impossible compression ratio over more
  turns. Does not solve the architectural conflict.
  Rejected.
* **Option D (force 1 200 LOC budget in 1 turn)**: produces
  low-quality compressed work that fails the section 5.2
  parity gate. Rejected.

## Refs

* P-RC-9-PLAN.md section 4 P9.6 (now revised by this ADR).
* P9.6 turn-1 escape-hatch report (commit ``e4b59137`` body
  + the chat turn that landed it).
* ADR-0011 (subsystem decomposition; OrgRuntime is the
  6th and largest subsystem).
* ADR-0012 (no shim under v1; v2 must be deletion-eligible).
* ADR-0013 (wall-clock SLA tests; the P9.4e SLAs remain in
  force for the v2 OrgRuntime cancel pipeline).

## Closure (P-RC-9 epic close, P9.9eta-2b)

- **Status**: **CLOSED-EFFECTIVE**. P-RC-9 epic CLOSED at gate
  ``e4d963e6`` (G-RC-9 final roll-up gate; eta-2a) + this commit
  (eta-2b acceptance / ADR / BOM follow-up).
- **P9.6 outcome**: OrgRuntime decomposed across the original
  ADR-0014 layout -- ``runtime.py`` + 7 underscore-prefixed
  sibling shards under ``src/openakita/runtime/orgs/``
  (``_runtime_agent_pipeline.py`` / ``_runtime_dispatch.py`` /
  ``_runtime_node_lifecycle.py`` / ``_runtime_lifecycle.py`` /
  ``_runtime_plugin_assets.py`` / ``_runtime_watchdog.py`` /
  ``_runtime_event_bus.py``). Core OrgRuntime decomposition LOC at
  HEAD ``e4d963e6``: **2 226** -- well within the revised
  **3 000 LOC** cap (cross-ref G-RC-9.6 sec 4 / ``runtime/orgs/_runtime_*.py``).
- **gamma-1b absorption shard**: ``_runtime_templates.py`` (1 572
  LOC) absorbs v1 plugin / template helpers per G-RC-9 sec 1 P9.6
  row. With this absorption shard, the OrgRuntime sub-decomposition
  totals **3 798 LOC** across 9 files; classified as a separate
  absorption per G-RC-9.6 sec 4 and not counted against the
  OrgRuntime LOC cap.
- **Net delivery vs original 1 200 LOC budget**: the revised
  3 000 cap is honored by the core decomposition; +1 800 LOC delta
  vs original budget absorbed as predicted; v1 retirement
  feasibility preserved (P9.9eps-2b retired the 6 355 LOC v1
  ``runtime.py`` together with the rest of ``src/openakita/orgs/``;
  the -20 237 LOC v1 src delete + the broader -35 493 LOC net
  retirement axis were unblocked by the real-rewrite path that
  this ADR ratified).
- **Net OrgRuntime LOC vs original 6 355 LOC v1** (retired in
  eps-2b): the core decomposition is **~35 %** of the v1 size by
  net code (~65 % reduction); with the gamma-1b absorption shard
  included, the OrgRuntime sub-decomposition is **~52-53 %** of
  the v1 size when normalised against an estimated v1 envelope
  that includes the helpers later absorbed elsewhere; further
  reduction comes from the ``runtime/llm/`` (5 shards, 486 LOC)
  and ``agent/`` (42 shards, 10 392 LOC) extractions that
  absorbed v1 OrgRuntime cross-cutting code into their respective
  canonical homes.
- **M-2 (sub-cap rebalance)**: deferred to P-RC-10; closes
  naturally during the ``runtime/orgs/`` -> ``orgs/`` flattening.
- **Reference**: G-RC-9 sec 3 (charter vs delivery diff) +
  G-RC-9 sec 4 (ADR closure pointers); G-RC-9.6 mini-gate
  (``c9007eb5``).
