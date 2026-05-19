# Revamp Progress Ledger -- P-RC-9 (orgs/ integral migration)

<!-- machine-readable phase marker; do NOT remove.
     Parsed by tests/revamp/_ledger.py + tests/parity/test_no_facade.py. -->
current_phase: P-RC-9

> **Sub-phase status (2026-05-19, G-RC-9.4 PASS)**: P9.0 closed, P9.1 closed (Nit-3 of 5 cleared; 4 ride to G-RC-9), P9.2 closed (parity 6/6, contract 36/36), P9.3 NodeScheduler closed (parity 4/4, contract 12/12, all 4 G-RC-9.2 nits folded in, no v1 touch). **P9.4 OrgCommandService closed** (parity 10/10, contract 16/16, 3 ADR-0013 wall-clock SLA tests green with 5x flake-guard, ACCEPTANCE.md #2 upgraded Pass-with-caveat -> Pass). **P9.5 OrgManager is next**. G-RC-9.4 mini-gate signed off at commit ``7fc863b8`` (auditor PASS-WITH-NITS; NIT-E-1 LangGraph attribution + NIT-G-1 Protocol count phrasing folded in via this P9.5.nit pre-flight; 4 other doc-level G-RC-9.4 NITs ride to G-RC-9 final). **P9.5 OrgManager pre-flight: clean.**

> Source of truth for every commit landed on ``revamp/v3-orgs``
> during the P-RC-9 ``src/openakita/orgs/`` integral migration.
> One row per commit, in commit order. Each row is appended *in
> the same commit that produced it* (N3 from G-RC-1).
>
> This ledger is **separate** from ``docs/revamp/PROGRESS_LEDGER.md``
> (which is frozen at P-RC-8 close). Keeping P-RC-9 in its own file
> stops the long P-RC-0..8 history from being mixed with the new
> 30-50 commits the charter projects, and lets future readers diff
> the two phases cleanly.
>
> Rules of the ledger (per continuation plan ?0.3, inherited):
> * append-only -- once a row lands on ``revamp/v3-orgs`` it must
>   not be silently rewritten;
> * ``LOC delta`` and ``tests delta`` are signed integers,
>   positive = grew, negative = shrank, ``0`` = unchanged;
> * ``ADR refs`` lists the ADRs whose sections the commit
>   implements (ADR-0011/0012/0013 are P-RC-9-specific).
>
> Pause points: every 5 commits, re-read
> ``docs/revamp/P-RC-9-PLAN.md`` + this ledger + the relevant
> phase section before opening the next commit.

## P9.0 -- Baseline (branch + recon + plan + ADRs + parity scaffold)

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| ``f1833fe5`` | P-RC-9 P9.0a | chore(p-rc-9): initialise revamp/v3-orgs branch + bump ledger to P-RC-9 | +PLACEHOLDER (new PROGRESS_LEDGER_P9.md + STATUS.md pointer) | 0 | --- (process; charter only) |
| ``e3308eaf`` | P-RC-9 P9.0b | docs(p-rc-9): write recon report part 1 (P-RC-9-RECON.md sections 0/1a/1b) | +246 (new file) | 0 | --- (read-only analysis) |
| ``75aebde2`` | P-RC-9 P9.0b2 | docs(p-rc-9): append recon report part 2 (sections 1c/1d/1e/1f + appendices) | +170 (append) | 0 | --- (read-only analysis) |
| ``205973ce`` | P-RC-9 P9.0c | docs(p-rc-9): write execution plan part 1 (P-RC-9-PLAN.md sections 0/1/2/3) | +361 (new file) | 0 | --- (planning) |
| ``e78ef3dd`` | P-RC-9 P9.0d | docs(p-rc-9): write execution plan part 2 (P-RC-9-PLAN.md sections 4/5) | +325 (append) | 0 | --- (planning) |
| ``f7425326`` | P-RC-9 P9.0e | docs(p-rc-9): write execution plan part 3 (P-RC-9-PLAN.md sections 6/7/8) | +180 (append) | 0 | --- (planning; previews ADR-0011/0012/0013) |
| ``1d5a8938`` | P-RC-9 P9.0f | docs(adr): add ADR-0011 (org subsystem decomposition) | +118 (new ADR) | 0 | ADR-0011 |
| ``46e8c884`` | P-RC-9 P9.0g | docs(adr): add ADR-0012 (orgs/ deletion strategy) | +137 (new ADR) | 0 | ADR-0012 |
| ``2d60189c`` | P-RC-9 P9.0h | docs(adr): add ADR-0013 (wall-clock SLA tests for cancel + checkpoint) | +123 (new ADR) | 0 | ADR-0013 |
| ``066524d4`` | P-RC-9 P9.0i | feat(tests): scaffold tests/parity/orgs/ harness skeleton (6 xfail placeholders) | +~200 (new package: __init__ + conftest + README + 6 test files) | +6 xfailed | ADR-0011 (subsystem list anchors the 6 placeholders) |
| _this commit_ | P-RC-9 P9.0z | docs(revamp): write G-RC-9.0 mini-gate (P9.0 baseline ready) | +183 (new gate file) | 0 | --- (gate review; cites ADR-0011/0012/0013) |

## P-RC-10 charter + Q decisions (post-P9.0z paperwork, pre-P9.1)

> Two paperwork commits that close the G-RC-9.0 review's open
> items (deferred-work charter for ``runtime/`` flattening +
> Q-A/Q-B/Q-C decision lock-in) BEFORE P9.1 OrgBlackboard work
> opens. Both commits are pure docs; no source touched.

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| ``ff8f695a`` | P-RC-10 charter | docs(revamp): write P-RC-10 charter for runtime/ hygiene flattening (v2.1.0 prep) | +309 (new ``P-RC-10-CHARTER.md`` 272 + STATUS.md 27 + ledger 11) | 0 | --- (planning; previews ADR-0014) |
| _this commit_ | P-RC-9 Q-lock | docs(revamp): lock in Q-A/Q-B/Q-C defaults + write Q_DECISIONS.md ledger | +~250 (new ``Q_DECISIONS.md`` + P-RC-9-PLAN.md section 7 ACCEPTED markers + ledger) | 0 | --- (paperwork; cites ADR-0011/0012/0013 indirectly via plan section 7) |

## P9.1 -- OrgBlackboard (charter subsystem #1)

> Implements ADR-0011 subsystem #1 (charter section 1).
> Replaces v1 ``openakita.orgs.blackboard.OrgBlackboard`` (344
> LOC, 19 methods) with a Protocol-typed, backend-pluggable v2
> surface under ``runtime/orgs/`` while preserving v1''s public
> sync API verbatim (parity gate per P-RC-9-PLAN section 0.2).

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| ``040256b2`` | P-RC-9 P9.1a0 | feat(runtime/orgs): add v2 memory models (MemoryScope/Type/OrgMemoryEntry) | +162 (memory_models.py NEW 127 + __init__.py +17/-10 + ledger) | 0 | ADR-0011 (subsystem decomposition; this is the shared model layer) |
| ``bcf43580`` | P-RC-9 P9.1a | feat(runtime/orgs): scaffold BlackboardProtocol + minimal v2 implementation | +315 (blackboard.py NEW 288 + __init__.py +20/-5 + ledger +3) | 0 (smoke import only) | ADR-0011 (Protocol-typed subsystem); ADR-0012 (no shim under v1) |
| ``57977dd0`` | P-RC-9 P9.1b | feat(runtime/orgs): complete v2 OrgBlackboard with concurrency + schema validation (JsonFile half) | +202 (blackboard.py +198/-4 + ledger +3) | 0 | ADR-0011; ADR-0013 |
| ``d1c8f235`` | P-RC-9 P9.1b2 | feat(runtime/orgs): add SqliteBlackboardBackend + get_default_blackboard_backend factory | +237 (blackboard.py +230 + __init__.py +4 + ledger +3) | 0 (sqlite smoke run during commit prep) | ADR-0011; ADR-0012 |
| ``7f3445e3`` | P-RC-9 P9.1c | test(parity/orgs): activate 8 blackboard parity fixtures (xfail -> pass) | +229 (test_blackboard_parity.py REPLACE: 18-line xfail placeholder -> 228-line fixture suite + ledger +3) | +8 / -1 xfail | ADR-0011; ADR-0013 |
| ``272b108e`` | P-RC-9 P9.1d | test(runtime/orgs): add 12 blackboard contract tests covering both backends | +351 (test_blackboard_contract.py NEW 345 + ledger +3) | +24 | ADR-0011; ADR-0012 |
| ``9b8d83a5`` | P-RC-9 G-RC-9.1 | docs(revamp): write G-RC-9.1 mini-gate (P9.1 OrgBlackboard sign-off) | +189 (G-RC-9.1.md NEW 186 + ledger +3) | 0 | ADR-0011; ADR-0012; ADR-0013 |
| ``fea1a5d5`` | P-RC-9 P9.1e | test(parity/orgs): relax bb_concurrent_writes to corruption-parity (v1 has no lock) | +17 (test_blackboard_parity.py rewrite + ledger) | 0 (8 parity still pass; flake eliminated; 3 stress runs 8/8/8) | ADR-0011; ADR-0013 |
| _this commit_ | P-RC-9 P9.1f | docs(revamp): append G-RC-9.1 section 11 addendum (P9.1e flake fix audit trail) | +42 (G-RC-9.1.md +41 + ledger +3) | 0 | ADR-0011; ADR-0013 |

## P9.2 prep -- G-RC-9.1 Nit-3 (date placeholder fix)

> One paperwork commit closing G-RC-9.1 auditor Nit-3 BEFORE
> P9.2 ProjectStore work opens. The other 4 nits ride along to
> the full G-RC-9 gate at P9.10. Pure docs; no source touched.

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| _this commit_ | P-RC-9 P9.2.nit3 | docs(revamp): fix Q_DECISIONS.md date placeholders (G-RC-9.1 Nit-3) | +6 (Q_DECISIONS.md 3-line edit + ledger 3) | 0 | --- (process; G-RC-9.1 audit follow-up) |

## P9.2 -- ProjectStore (charter subsystem #2)

> Implements ADR-0011 subsystem #2 (charter section 1).
> Replaces v1 ``openakita.orgs.project_store.ProjectStore``
> (281 LOC, 15 public + 5 private methods, single JSON-file
> backend) with a Protocol-typed, backend-pluggable v2 surface
> under ``runtime/orgs/`` while preserving v1''s public sync
> API verbatim (parity gate per P-RC-9-PLAN section 0.2 and
> section 5.2 ProjectStore ignore set).

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| _this commit_ | P-RC-9 P9.2a0 | feat(runtime/orgs): add v2 project models (OrgProject/ProjectTask + enums + ULID-style ids) | +PLACEHOLDER (project_models.py NEW + __init__.py +28/-1 + ledger) | 0 | ADR-0011 (subsystem decomposition; shared model layer for ProjectStore) |
| _this commit_ | P-RC-9 P9.2a | feat(runtime/orgs): ProjectStoreProtocol + JsonProjectStore CRUD half (project + task CRUD; tree/query in P9.2b) | +PLACEHOLDER (project_store.py NEW 317 + __init__.py +4 net + ledger) | 0 (smoke import + JsonProjectStore CRUD round-trip during commit prep) | ADR-0011 (Protocol-typed subsystem); ADR-0012 (no shim under v1) |
| _this commit_ | P-RC-9 P9.2b | feat(runtime/orgs): complete JsonProjectStore tree/query half (all_tasks, find_task_by_chain, get_task, get_subtasks, get_task_tree, get_ancestors, recalc_progress) | +PLACEHOLDER (project_store.py +130 net + ledger) | 0 (smoke create_project + add_task + recalc_progress + tree traversal during commit prep) | ADR-0011 (Protocol-typed subsystem); ADR-0013 (cycle guard on get_ancestors) |
| _this commit_ | P-RC-9 P9.2c | feat(runtime/orgs): add SqliteProjectStore backend (WAL + BEGIN IMMEDIATE, relational projects/tasks schema) | +PLACEHOLDER (project_store.py +372/-2 + __init__.py +5 + ledger) | 0 (smoke create/add_task/recalc/tree round-trip + Protocol check during commit prep) | ADR-0011; ADR-0012 |
| _this commit_ | P-RC-9 P9.2c2 | feat(runtime/orgs): ProjectStore factory + per-org cache (get_default_project_store / reset_default_project_stores) | +PLACEHOLDER (project_store.py +70 + __init__.py +6 + ledger) | 0 (smoke factory + cache + reset during commit prep) | ADR-0011 (Protocol-typed subsystem); ADR-0012 |
| _this commit_ | P-RC-9 P9.2d | test(parity/orgs): activate 6 project_store parity fixtures (xfail -> pass) | +PLACEHOLDER (test_project_store_parity.py REPLACE: 20-line xfail placeholder -> ~280-line fixture suite + ledger) | +6 / -1 xfail | ADR-0011 (subsystem decomposition); P-RC-9-PLAN section 5.2 ignore set |
| _this commit_ | P-RC-9 P9.2e | test(runtime/orgs): add 10 project_store contract cases (read-back/IDs/recalc/delete) across both backends -> 20 collected tests | +PLACEHOLDER (test_project_store_contract.py NEW 276 + ledger) | +20 | ADR-0011 (Protocol-typed subsystem); ADR-0012 (no shim under v1) |
| _this commit_ | P-RC-9 P9.2e2 | test(runtime/orgs): add 8 project_store contract cases (malformed/schema/concurrent/perf) -> +16 collected tests, total 36 | +PLACEHOLDER (test_project_store_contract.py +229/-5 + ledger) | +16 | ADR-0011 (Protocol-typed subsystem); ADR-0013 (perf envelope; wall-clock concurrent-write SLA) |

| _this commit_ | P-RC-9 G-RC-9.2 | docs(revamp): write G-RC-9.2 mini-gate (P9.2 ProjectStore sign-off) | +PLACEHOLDER (G-RC-9.2.md NEW 292 + ledger +3) | 0 | ADR-0011; ADR-0012; ADR-0013 |


## P9.3 -- NodeScheduler (charter subsystem #3)

> Implements ADR-0011 subsystem #3 (charter section 1).
> Replaces v1 ``openakita.orgs.node_scheduler.OrgNodeScheduler``
> (215 LOC, 10 methods, OrgRuntime-coupled) with a
> Protocol-typed, dependency-injected v2 surface under
> ``runtime/orgs/`` while preserving v1''s public sync API
> verbatim (parity gate per P-RC-9-PLAN section 0.2 and
> section 5.2 NodeScheduler ignore set). The three injected
> Protocols (CommandDispatcher / ScheduleStore /
> SchedulerRuntimeProbe) make the scheduler testable without
> ``OrgRuntime`` and pre-position the P9.4 OrgCommandService
> boundary.

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| _this commit_ | P-RC-9 P9.3a0 | feat(runtime/orgs): add v2 schedule models (NodeSchedule/ScheduleType + monotonic-counter ULID id mint) | +PLACEHOLDER (scheduler_models.py NEW 145 + __init__.py +7 + ledger) | 0 | ADR-0011 (subsystem decomposition; shared model layer for NodeScheduler); ADR-0012 (no shim under v1); Nit-1 fold-in from G-RC-9.2 (monotonic-counter id mint) |
| _this commit_ | P-RC-9 P9.3a | feat(runtime/orgs): NodeSchedulerProtocol + CommandDispatcher/ScheduleStore/SchedulerRuntimeProbe Protocols + compute_next_fire_time helper + OrgNodeScheduler skeleton (P9.3b lands bodies) | +PLACEHOLDER (node_scheduler.py NEW 263 + __init__.py +33 + ledger) | 0 (smoke imports + Protocol structural check + compute_next_fire_time pure helper sanity during commit prep) | ADR-0011 (Protocol-typed subsystem decomposition; CommandDispatcher is the cross-subsystem boundary for P9.4 OrgCommandService); ADR-0012 (no shim under v1); ADR-0013 (compute_next_fire_time is a pure function so the parity 1-ms safety net asserts deterministically) |
| _this commit_ | P-RC-9 P9.3b | feat(runtime/orgs): OrgNodeScheduler implementation (lifecycle methods + _schedule_loop + _execute_schedule + asyncio.Lock-guarded mutators + parity-faithful prompt builder) | +PLACEHOLDER (node_scheduler.py +236 net + __init__.py +2 + ledger) | 0 (smoke trigger_once + reload + stop_all + prompt structure round-trip during commit prep; pytest tests/runtime/orgs/ -> 92 passed unchanged) | ADR-0011 (Protocol-typed subsystem; CommandDispatcher injected); ADR-0013 (asyncio.Lock cancel-then-replace race-safety; MAX_FREQUENCY_FACTOR back-off ceiling) |
| _this commit_ | P-RC-9 P9.3c | test(parity/orgs): activate 4 node_scheduler parity fixtures (xfail -> pass; next-fire 1-ms safety net + dispatch-prompt v1==v2) | +PLACEHOLDER (test_node_scheduler_parity.py REPLACE: 28-line xfail placeholder -> ~310-line fixture suite + ledger) | +4 / -1 xfail | ADR-0011 (subsystem decomposition); P-RC-9-PLAN section 5.2 next-fire-time 1-ms tolerance |
| _this commit_ | P-RC-9 P9.3d | test(runtime/orgs): add 12 node_scheduler contract cases (compute_next_fire + lifecycle + cancel/reload + 4x25 concurrent + dispatch + missing-id) | +PLACEHOLDER (test_node_scheduler_contract.py NEW 316 + ledger) | +12 | ADR-0011 (Protocol-typed subsystem); ADR-0013 (Nit-2 fold-in concurrency stress: 4 coroutines x 25 reloads = 100 ops) |
| _this commit_ | P-RC-9 G-RC-9.3 | docs(revamp): write G-RC-9.3 mini-gate (P9.3 NodeScheduler sign-off) | +PLACEHOLDER (G-RC-9.3.md NEW 325 + ledger +3) | 0 | ADR-0011; ADR-0012; ADR-0013; G-RC-9.2 Nit-1/2/3/4 fold-in |

## P9.4 -- OrgCommandService (charter subsystem #4)

> Implements ADR-0011 subsystem #4 (charter section 1) -- the
> BIGGEST P-RC-9 subsystem (v1 963 LOC). Replaces v1
> ``openakita.orgs.command_service.OrgCommandService`` with a
> Protocol-typed, dependency-injected v2 surface under
> ``runtime/orgs/`` that implements the
> :class:`CommandDispatcher` boundary already defined by P9.3
> ``runtime.orgs.node_scheduler``. The cancel verb in the v2
> path is the closure of ACCEPTANCE.md #2 (Pass-with-caveat ->
> Pass) via the three wall-clock SLA tests from ADR-0013
> (landed in P9.4e).

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| ``9a085922`` | P-RC-9 P9.4a0 | feat(runtime/orgs): add v2 command models (Request/Response/Source/ForwardTarget + Surface/Scope enums + monotonic-counter id mint) | +367 (command_models.py NEW 324 + __init__.py +24 -0 + ledger +19 -1) | 0 | ADR-0011 (subsystem decomposition; shared model layer for OrgCommandService); ADR-0012 (no shim under v1); Nit-1 fold-in from G-RC-9.2 (monotonic-counter id mint) |
| ``eb4d6478`` | P-RC-9 P9.4a | feat(runtime/orgs): OrgCommandServiceProtocol + 5 DI Protocols (Lookup/Runtime/Session/Gateway/Emitter) + 1 SLA-test-only BrainProtocol + OrgCommandService skeleton implementing CommandDispatcher (dispatch + accessors) | +372 (command_service.py NEW 347 + __init__.py +22 + ledger +3 -2) | 0 | ADR-0011 (Protocol-typed subsystem decomposition; CommandRuntimeProtocol replaces v1 runtime reach-ins); ADR-0012 (no shim under v1); ADR-0013 (BrainProtocol scaffolds the P9.4e wall-clock SLA tests) |
| ``ef9c6d6f`` | P-RC-9 P9.4b | feat(runtime/orgs): OrgCommandService.submit + 5 private helpers (asyncio.Lock; conflict gate; Nit-1 cmd id; ADR-0011 Lookup/Runtime injection; get_status/cancel deferred to P9.4b2) | +360 (command_service.py +357 -7 net + ledger +3 -2) | 0 (smoke submit/get_status/cancel + conflict + empty-content during commit prep) | ADR-0011 (Protocol injection in _require_org_running + _runtime calls); ADR-0013 (cancel pipeline awaits CommandRuntimeProtocol.cancel_user_command which the P9.4e SLA tests stub deterministically) |
| ``71597235`` | P-RC-9 P9.4b2 | feat(runtime/orgs): OrgCommandService get_status + cancel + 5 fan-out methods + _dispatch_forwards + _live_snapshot_view (closes the v2 service surface; ADR-0011 EventEmitter/Gateway injection) | +338 (command_service.py +335 -7 + ledger +3 -2) | 0 (smoke get_status/cancel/subscribe/publish/forward during commit prep) | ADR-0011 (Protocol injection in cancel broadcast + IM forward dispatch); ADR-0013 (cancel + delivered_to + forward_log are the observability surface the wall-clock SLA tests assert against in P9.4e) |
| ``0893a112`` | P-RC-9 P9.4c | test(parity/orgs): activate 10 command_service parity fixtures (xfail -> pass; request to_dict + 4 default_scope + 2 ForwardTarget + submit record shape) | +365 (test_command_service_parity.py +362 -13 + ledger +3 -2) | +10 / -1 xfail | ADR-0011 (subsystem decomposition); P-RC-9-PLAN section 5.2 OrgCommandService ignore set (command_id + timestamps); P-RC-9-PLAN section 5.1 (10 fixtures = max of any P9.x phase) |
| ``55653d11`` | P-RC-9 P9.4d | test(runtime/orgs): add 16 command_service contract cases (dispatch + submit gates + replace conflict + get_status overlay + cancel + fan-out + find) | +335 (test_command_service_contract.py NEW 332 + ledger +3 -2) | +16 | ADR-0011 (Protocol injection asserted at every test double); P-RC-9-PLAN section 4 P9.4 (charter 20 cases; we ship 16 because the section 5.2 parity gate already covers the dataclass surface) |
| ``52d9bbc8`` | P-RC-9 P9.4e | test(runtime/orgs): add 3 wall-clock SLA tests + ACCEPTANCE.md #2 upgrade (Pass-with-caveat -> Pass; ADR-0013 closure) | +322 (test_cancel_wall_clock_budget.py NEW 234 + ACCEPTANCE.md #2 block +30 -22 + ledger +3 -1) | +5 collected (3 parametrize SLA #1 + 1 SLA #2 + 1 SLA #3) | ADR-0013 (this commit IS the closure); ADR-0011 (CommandRuntimeProtocol stub for determinism); ADR-0005 (checkpoint contract assumed; structural pin retained by tests/runtime/test_supervisor.py) |
| ``7fc863b8`` | P-RC-9 G-RC-9.4 | docs(revamp): write G-RC-9.4 mini-gate (P9.4 OrgCommandService sign-off) | +328 (G-RC-9.4.md NEW 328 + ledger header +1 -1 + ledger row +1) | 0 | ADR-0011 (subsystem decomposition; 7 Protocols documented); ADR-0012 (no shim under v1; sentinel 10.3 empty); ADR-0013 (SLA tests landed in P9.4e; ACCEPTANCE.md #2 closure documented in section 12); G-RC-9.3 4-nit fold-in status (section 11) |
| ``57611160`` | P-RC-9 P9.5.nit | docs(revamp): clean up G-RC-9.4 doc/self-representation NITs (E-1 LangGraph attribution removed; G-1 Protocol count corrected to 5 DI + 1 public contract + 1 SLA-test-only) | +52 (G-RC-9.4.md +24 -22 across 6 spots + PROGRESS_LEDGER_P9.md +3 -2 = +27 -24 hand-written) | 0 | G-RC-9.4 NIT-E-1 (LangGraph attribution verified false against both candidate citation sites); G-RC-9.4 NIT-G-1 (5 DI Protocols confirmed by reading runtime/orgs/command_service.py:236-244 OrgCommandService.__init__); pre-flight for P9.5 OrgManager |

## P9.5 -- OrgManager (charter subsystem #5)

> Implements ADR-0011 subsystem #5 (charter section 1) -- the
> v1 ``openakita.orgs.manager.OrgManager`` (683 LOC, 24 public
> methods + ``OrgNameConflictError``) replaced by a
> Protocol-typed v2 surface under ``runtime/orgs/`` per
> P-RC-9-PLAN section 4 P9.5. Implements ``OrgLookupProtocol``
> (REUSE from P9.4 ``command_service.py``) so P9.4
> ``OrgCommandService`` can consume the v2 manager once P9.8
> redirects callers. Storage default = filesystem JSON
> (data/orgs/<id>/org.json + state.json + nodes/<n>/schedules.json
> + org_templates/*.json), parity-faithful to v1. Protocol
> granularity ceiling: <= 5 methods per Protocol (G-RC-9.4
> auditor recommendation #4).

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| _this commit_ | P-RC-9 P9.5a0 | feat(runtime/orgs): add ``_org_layout.py`` -- ``apply_initial_tree_layout`` (template create path) + ``normalize_org_name`` (uniqueness key) + 4 layout constants lifted byte-for-byte from v1 ``manager._apply_initial_tree_layout`` / ``_normalize_org_name`` | +PLACEHOLDER (_org_layout.py NEW 182 + ledger header +24 + ledger row +1) | 0 (smoke import + normalize + apply_initial_tree_layout 2-node tree round-trip during commit prep) | ADR-0011 (sibling helper module for OrgManager subsystem); ADR-0012 (no shim under v1; helper is duplicated by intent so v1 manager.py is untouched) |
