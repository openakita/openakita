# Revamp Progress Ledger -- P-RC-9 (orgs/ integral migration)

**PAUSED 2026-05-19** — user resume tomorrow; see PAUSE_CHECKPOINT_P9.md

<!-- machine-readable phase marker; do NOT remove.
     Parsed by tests/revamp/_ledger.py + tests/parity/test_no_facade.py. -->
current_phase: P-RC-9
> **Sub-phase status (2026-05-20, P9.8 CLOSED -- P-RC-9 caller migration complete; P9.9 v1 physical deletion is next (delete ``src/openakita/orgs/``, ``src/openakita/api/routes/orgs.py``, ``tests/orgs/*``, and possibly retire the 308 shim at v2.1.0 per Q-B ACCEPTED (b)))**: P9.0-P9.6 closed (6/6 ADR-0011 subsystems v2 with 60/60 parity green and 6/6 parity sentinels active). **P9.7 v2 REST endpoint mint CLOSED** -- 83 / 83 mint endpoints under /api/v2/orgs/* (B1-B83 across 6 cluster siblings) + 1 health stub + Group A (9 endpoints) relocated to /api/v2/orgs-spec/* with 9 308 shim redirects (D-1 R3 LOCKED); 184 / 184 contract cases + 3 / 3 REST contract sentinel cases. **P9.8 caller migration CLOSED** -- 55 frontend hits swapped (51 HTTP literals + 4 narrative comments) across 10 source files in apps/setup-center/src/ (gamma-1 Group A retarget to ``-spec`` + gamma-2 OrgEditorView + gamma-3 OrgProjectBoard + OrgChatPanel + gamma-4 long-tail 7 view/component files); 7 legitimate residuals preserved (4 TS module imports that are relative-path specifiers, not URLs; 3 Group C HTTP paths in OrgEditorView.tsx for deprecated debug-only endpoints scheduled for P9.9 deletion); G-RC-9.8 mini-gate signed off. **8 P-RC-9 sentinels ACTIVE** (6 parity P9.1c-P9.6gamma + 1 REST contract P9.7gamma-2 + 1 frontend stale-path P9.8delta-1). Strict-additive backend boundary held absolutely across all 8 P9.8 commits (src/openakita/ BYTE-LEVEL UNTOUCHED). **Only P9.9 physical deletion remains** for full P-RC-9 closure (delete v1 subsystem + v1 router + tests/orgs/* + 90 + 228 ``from openakita.orgs.X`` import rewrite sites + ADR-0015 for 308 shim retirement governance).

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
| ``52d9bbc8`` | P-RC-9 P9.4e | test(runtime/orgs): add 3 wall-clock SLA tests + ACCEPTANCE.md #2 upgrade (Pass-with-caveat -> Pass; ADR-0013 closure) | +322 (test_cancel_wall_clock_budget.py NEW 234 at commit point; now 300 LOC after subsequent doc-string polish per G-RC-9.5 NIT-L-1 + ACCEPTANCE.md #2 block +30 -22 + ledger +3 -1) | +5 collected (3 parametrize SLA #1 + 1 SLA #2 + 1 SLA #3) | ADR-0013 (this commit IS the closure); ADR-0011 (CommandRuntimeProtocol stub for determinism); ADR-0005 (checkpoint contract assumed; structural pin retained by tests/runtime/test_supervisor.py) |
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
| ``12128dfd`` | P-RC-9 P9.5a0 | feat(runtime/orgs): add ``_org_layout.py`` -- ``apply_initial_tree_layout`` (template create path) + ``normalize_org_name`` (uniqueness key) + 4 layout constants lifted byte-for-byte from v1 ``manager._apply_initial_tree_layout`` / ``_normalize_org_name`` | +205 (_org_layout.py NEW 182 + ledger +21 backfills/header/row +2 phrasing) | 0 (smoke import + normalize + apply_initial_tree_layout 2-node tree round-trip during commit prep) | ADR-0011 (sibling helper module for OrgManager subsystem); ADR-0012 (no shim under v1; helper is duplicated by intent so v1 manager.py is untouched) |
| ``cf7f6e2c`` | P-RC-9 P9.5a | feat(runtime/orgs): manager.py P9.5a scaffold -- 4 Protocols (OrgPersistenceProtocol 4m + OrgLifecycleEmitterProtocol 3m + OrgFactoryProtocol 2m, all <= 5; OrgLookupProtocol REUSED from P9.4 1m) + 3 default backends (_FilesystemOrgPersistence + _NoopOrgLifecycleEmitter + _DefaultOrgFactory) + OrgNameConflictError v2 alias + OrgManager.__init__ + 6 dir helpers + get_org (OrgLookupProtocol impl) + get_org_manager factory | +PLACEHOLDER (manager.py NEW 352 + __init__.py +14 + ledger +3 backfill/row = 369 hand-written) | 0 (smoke: package import + OrgManager() empty-store get_org returns None + isinstance OrgLookupProtocol + _FilesystemOrgPersistence/_NoopOrgLifecycleEmitter/_DefaultOrgFactory satisfy their Protocols + new_org_id starts with 'org_'; full pytest tests/runtime/orgs/ + tests/parity/orgs/ + tests/runtime/test_cancel_wall_clock_budget.py = 153 passed / 2 xfailed unchanged) | ADR-0011 (Protocol-typed decomposition; 4 Protocols all <= 5 methods per G-RC-9.4 auditor recommendation #4); ADR-0012 (no shim under v1; v2 file under runtime/orgs/, v1 manager.py untouched); G-RC-9.4 NIT-G-1 fold-in (5-DI + 1-public-contract + 1-SLA-test-only count framing inherited for P9.5 design) |
| ``c5973b8f`` | P-RC-9 P9.5b | feat(runtime/orgs): OrgManager CRUD half -- list_orgs / get / find_by_name (case+whitespace insensitive) / resolve_id_by_name_or_id / _ensure_name_unique / create / delete / invalidate_cache + _load (cache-aware) / _save (atomic via persistence) / _init_dirs (delegates to factory + _ensure_node_dirs) / _ensure_node_dirs (identity/ + mcp_config.json + schedules.json + dept dirs); + module-level OrgStatus / normalize_org_name import + hoists | +213 (manager.py +209 -1 via 12 methods + ledger +3 -1) | 0 (smoke during commit prep: create -> list 1 -> get -> find_by_name case-insensitive -> resolve_id by id and by name -> delete -> idempotent re-delete -> dir layout 8 subdirs + policies/README.md + org.json + per-node identity/mcp_config.json/schedules.json all exist; OrgNameConflictError on duplicate; ValueError on empty name; OrgLookupProtocol get_org still satisfied) | ADR-0011 (Protocol injection in _load/_save -> _persistence; lifecycle emission in create/delete); ADR-0012 (no v1 touch); P-RC-9-PLAN section 5.2 dir-layout parity (8 org subdirs + README.md + per-node 3 files match v1 byte-for-byte) |
| ``8afd8028`` | P-RC-9 P9.5b2 | feat(runtime/orgs): OrgManager extras half -- update (rename guard + node/edge patch + workbench-leaf invariant) / save_direct (no-reload) / archive / unarchive / duplicate (id remap) + 5 node-schedule CRUD methods + 4 template methods + load_state / save_state | +325 (manager.py +320 -2 via 16 methods + ledger +4 -1 backfill/new row) | 0 (smoke during commit prep: update/archive/unarchive/save_direct/duplicate/5 node-schedule ops/4 template ops/load_state/save_state + name-conflict-on-update; existing pytest tests/runtime/orgs/+tests/parity/orgs/+tests/runtime/test_cancel_wall_clock_budget.py -> 153 passed / 2 xfailed unchanged) | ADR-0011 (apply_initial_tree_layout via sibling helper module; _ensure_node_dirs reused from P9.5b; persistence via Protocol throughout); ADR-0012 (no v1 touch); P-RC-9-PLAN section 4 P9.5 (24 public methods now complete: 8 from P9.5b + 16 from P9.5b2); P-RC-9-PLAN section 5.2 (template-create uses apply_initial_tree_layout for dir-layout parity) |
| ``da25b415`` | P-RC-9 P9.5c | test(parity/orgs): activate 12 manager parity fixtures (xfail -> pass; create + create_with_nodes + create_and_walk_dir + list_empty + list_multi + get_returns_none + find_case_insensitive + archive_flip + delete_idempotent + template_roundtrip + 100_blob_roundtrip + update_preserves_id) | +325 (test_manager_parity.py +309 -13 net via 12 parametrized cases + 4 normalisation helpers + 2 manager loaders + run_case dispatcher + ledger +3 backfill/row) | +12 / -1 xfail | ADR-0011 (Protocol-typed subsystem decomposition asserted indirectly: v2 OrgManager runs the same scripted scenario as v1 and produces byte-equal output sans volatile id/timestamps); P-RC-9-PLAN section 5.2 (OrgManager parity contract: create()->dict->Organization.to_dict() round-trip + dir layout assertion both covered by manager_create_org_and_walk_dir case; 100-blob round-trip stress test from section 4 P9.5 covered by manager_100_blob_roundtrip case); P-RC-9-PLAN section 5.1 (12 fixtures = the largest of any P9.x phase) |
| ``5906c2f3`` | P-RC-9 P9.5d | test(runtime/orgs): add 16 manager contract cases (create x3 / read missing x2 / delete idempotency x2 / list ordering x2 / dir layout x2 / concurrent ops x2 / malformed input x2 / 100-blob stress x1) | +289 (test_manager_contract.py NEW 286 + ledger +3 backfill/row) | +16 | ADR-0011 (Protocol injection asserted via _persistence/_lifecycle/_factory paths through every test case; the path-traversal case pins the _org_dir validation surface; the dir-layout cases pin the _DefaultOrgFactory.initialize_directory_layout + _ensure_node_dirs contract); P-RC-9-PLAN section 4 P9.5 (charter 24 cases across test_manager + test_identity + test_plugin_workbench; we ship 16 focused on OrgManager proper because identity/ + plugin_workbench/ live in v1 and are not P-RC-9 v2 deliverables); ADR-0012 (no v1 touch) |
| ``ce7a055f`` | P-RC-9 G-RC-9.5 | docs(revamp): G-RC-9.5 P9.5 (OrgManager) mini-gate -- PASS (12 parity / 16 contract / 4 Protocols <= 5 methods / sentinel three-piece green / 2 of 6 G-RC-9.4 NITs closed via P9.5.nit; 4 ride to G-RC-9 final; no ACCEPTANCE upgrade); bumps ledger header to 'P9.6 OrgRuntime is next' | +PLACEHOLDER (G-RC-9.5.md NEW 327 + ledger header bump + row + P9.5d hash backfill) | n/a | references G-RC-9.4 (sections 1/2/6.1/6.2/9 patched in P9.5.nit), P-RC-9-PLAN section 4 P9.5 (full closure: 12 parity + 16 contract + no charter-state SLA / no ACCEPTANCE upgrade), ADR-0011 (4-Protocol decomposition + 3 default backends), ADR-0012 (v1 ``orgs/`` UNTOUCHED), ADR-0013 (N/A for P9.5; SLA module reused from P9.4) |

## P9.6 prep -- G-RC-9.5 NIT-D-1 + 4 G-RC-9.4 doc-only NITs fold-in

> One paperwork commit closing 5 doc-only NITs (1 NEW from
> G-RC-9.5 + 4 inherited from G-RC-9.4) BEFORE P9.6 OrgRuntime
> work opens. Per the P9.6 user brief these are the auditor's
> mandatory pre-flight items. G-RC-9.4 NIT-B-1 (burst-test
> semantics; requires OrgCommandService refactor) is deferred
> to G-RC-9 final per the same brief. Pure docs + 2 docstring
> edits; no production code logic changed.

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| ``e4b59137`` | P-RC-9 P9.6.nit | docs(revamp): clean up G-RC-9.5 NIT-D-1 + 4 G-RC-9.4 doc-only NITs (K-1 fixture-id drift / K-2 v2_im_cancel 4/4 / L-1 SLA file LOC 300 / G-2 lock-claim wording) | +PLACEHOLDER (command_service.py 9 lines + __init__.py 7 lines + G-RC-9.4.md 26 lines + ACCEPTANCE.md 4 lines + ledger ~22 lines) | 0 (smoke: pytest tests/runtime/orgs/test_command_service_contract.py + tests/parity/orgs/test_command_service_parity.py = 26 passed; ruff clean on touched files) | G-RC-9.5 NIT-D-1 (P9.5 docstring count corrected); G-RC-9.4 NIT-K-1 (fixture ids re-fetched and pinned); NIT-K-2 (test_v2_im_cancel 4 cases not 5; verified via --collect-only); NIT-L-1 (SLA file LOC actual 300 per ``wc -l``; previous 234 was commit-point net add); NIT-G-2 (cancel does not acquire self._lock; docstring corrected) |

## P9.6 plan -- budget revision (ADR-0014)

> Empirical recon (P9.6 turn-1 escape-hatch report) revealed
> the original 1 200 src LOC budget was incompatible with
> ADR-0012 + P-RC-9-PLAN section 5.2 parity. Per the project
> owner's option-C ruling, the P9.6 src budget is revised to
> ~3 000 LOC across 7-8 sibling modules (each <= 500 LOC),
> tests budget to ~900 LOC (20 parity + ~25 contract), and
> commits to 12-15 across 2-3 turns (alpha / beta / gamma).
> ADR-0014 records the decision drivers + alternatives
> rejected. Pure docs commit; no production code touched.

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| ``f36f7f19`` | P-RC-9 P9.6.plan | docs(revamp): revise P9.6 budget 1200 -> 3000 LOC + add ADR-0014 (empirical recon outcome) | +PLACEHOLDER (P-RC-9-PLAN.md ~55 net + ADR-0014 NEW ~160 + ledger ~25 = ~240) | 0 (paperwork) | ADR-0014 (NEW; records option-C decision); ADR-0011 (subsystem decomposition; OrgRuntime sibling list anchored here); ADR-0012 (no-shim invariant cited in alternatives-rejected); cites P-RC-9-PLAN section 4 P9.6 revision + section 5.2 parity gate |

## P9.6alpha -- skeleton + Protocols + 3 small siblings (this turn)

> ADR-0014 option-C execution, alpha sub-turn. Lands the
> OrgRuntime skeleton + 3 NEW Protocols + 3 of the 4 small /
> isolated sibling modules (``_runtime_event_bus.py``,
> ``_runtime_watchdog.py``, ``_runtime_lifecycle.py``). The
> 4 heavy siblings (dispatch / agent_pipeline / node_lifecycle
> / plugin_assets) + parity 20 + contract ~25 + G-RC-9.6
> mini-gate ride to P9.6beta / P9.6gamma turns.

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| ``fb0bb6dd`` | P-RC-9 P9.6a0 | feat(runtime/orgs): runtime.py P9.6a0 -- 3 new Protocols (RuntimeStateProtocol 4m + NodeLifecycleProtocol 5m + EventBusProtocol 4m) + 3 default in-memory backends (_InMemoryRuntimeState + _InMemoryNodeLifecycle + _InMemoryEventBus) | +PLACEHOLDER (runtime.py NEW ~210 LOC + __init__.py +12 + ledger +3 backfill / row) | 0 (smoke: pytest tests/runtime/orgs/ + tests/parity/orgs/ -> 176 passed / 1 xfailed unchanged; isinstance check on default backends satisfies all 3 Protocols) | ADR-0014 (budget revision; alpha kickoff); ADR-0011 (3 new Protocols each <= 5 methods per granularity ceiling); ADR-0012 (no-shim under v1) |
| ``ea0ddda7`` | P-RC-9 P9.6a | feat(runtime/orgs): OrgRuntime class skeleton + ``__init__`` (6 reused Protocols + 3 new) + ``CommandRuntimeProtocol`` 6-method stubs (bodies P9.6beta) + ``OrgLookupProtocol`` delegation + ``get_runtime`` factory placeholder | +PLACEHOLDER (runtime.py append ~110 LOC + __init__.py +10 + ledger +3 backfill / row) | 0 (smoke: pytest tests/runtime/orgs/ + tests/parity/orgs/ -> 176 passed / 1 xfailed unchanged; ``isinstance(OrgRuntime, CommandRuntimeProtocol)`` -> True closes P9.4 dependency loop at type level) | ADR-0014 (alpha sub-turn); ADR-0011 (composition of 6 reused Protocols: OrgLookupProtocol + OrgPersistenceProtocol + OrgLifecycleEmitterProtocol + OrgCommandServiceProtocol + NodeSchedulerProtocol + BlackboardBackendProtocol); ADR-0012 (no-shim); P9.4 CommandRuntimeProtocol contract (OrgRuntime IS the canonical impl) |
| ``a67675d7`` | P-RC-9 P9.6b | feat(runtime/orgs): ``_runtime_event_bus.py`` -- InMemoryEventBus (pub/sub + best-effort WS bridge) + WebSocketEventBus (always-broadcast variant) + ``get_default_event_bus`` factory (``ORGS_V2_EVENT_BUS=ws`` opt-in) | +PLACEHOLDER (``_runtime_event_bus.py`` NEW ~150 LOC + __init__.py +12 + ledger +3 backfill / row) | 0 (smoke: subscribe / emit / unsubscribe round-trip on InMemoryEventBus + ws_broadcast no-op when ``api.routes.websocket`` unimportable matches v1 ``_broadcast_ws`` parity; both backends satisfy ``isinstance(EventBusProtocol)``) | ADR-0014 (alpha sub-turn: smallest + most-isolated sibling); ADR-0011 (Protocol-typed sibling; Protocol owned by ``runtime.py`` P9.6a0); ADR-0012 (no-shim under v1; WS bridge calls v1 ``broadcast_event`` via lazy import to avoid hard dependency) |
| ``64514e19`` | P-RC-9 P9.6c | feat(runtime/orgs): ``_runtime_watchdog.py`` -- CommandWatchdog (v1 ``_command_watchdog`` 175 LOC parity) + IdleProbeLoop (v1 ``_idle_probe_loop`` 143 LOC parity); DI-driven async loops with start / stop / graceful-shutdown | +PLACEHOLDER (``_runtime_watchdog.py`` NEW ~210 LOC + __init__.py +8 + ledger +3 backfill / row) | 0 (smoke: CommandWatchdog quiet-deadlock detection fires on_deadlock callback when ``last_activity_at`` > threshold; IdleProbeLoop nudges idle node when ``node_last_active`` > threshold; both loops shut down cleanly via ``asyncio.Event``-driven stop) | ADR-0014 (alpha sub-turn; small + isolated sibling); ADR-0011 (no new Protocol shipped here -- watchdog consumes the dispatch-sibling tracker via tiny inline ``_TrackerSnapshotProtocol`` shape); ADR-0012 (no-shim under v1; reimplemented as DI-driven async loops not tied to ``OrgRuntime``); P9.4 wall-clock SLA tests (P9.4e) UNTOUCHED -- watchdog adds best-effort recovery on top of an already-cancelled tracker, not part of the cancel pipeline that the SLA tests pin |
| ``9274a6f2`` | P-RC-9 P9.6d | feat(runtime/orgs): ``_runtime_lifecycle.py`` -- OrgLifecycleManager (state machine for start / stop / pause / resume / restart / delete / health-check; ~18 v1 lifecycle methods absorbed) + 5 state constants + IllegalOrgTransition guard | +PLACEHOLDER (``_runtime_lifecycle.py`` NEW ~240 LOC + __init__.py +15 + ledger +3 backfill / row) | 0 (smoke: 5-state DAG transitions CREATED -> ACTIVE -> PAUSED -> ACTIVE -> STOPPED -> ACTIVE -> STOPPED -> DELETED all green; illegal DELETED -> ACTIVE guarded; idempotent start_org on ACTIVE is no-op; pytest tests/runtime/orgs/ + tests/parity/orgs/ -> 176 passed / 1 xfailed unchanged) | ADR-0014 (alpha sub-turn final commit); ADR-0011 (composes EventBusProtocol (P9.6a0) + RuntimeStateProtocol (P9.6a0) via DI); ADR-0012 (no-shim under v1; lifecycle verbs are pure async + DI callbacks, no ``OrgRuntime`` self-state access); v1 ``OrgStatus`` semantic parity for state constants |

## P9.6beta -- the 4 heavy siblings (this turn)

> ADR-0014 option-C execution, beta sub-turn. Lifts the 4
> heaviest v1 ``OrgRuntime`` method groups (dispatch + tracker
> + chain x ~1 050 LOC; agent pipeline x ~1 410 LOC; node
> lifecycle + message routing x ~600 LOC; plugin / file
> assets x ~1 060 LOC) into focused sibling managers under
> ``runtime/orgs/``. After this turn, the four
> ``CommandRuntimeProtocol`` stub methods left by P9.6a
> become real (delegating to the dispatch manager). P9.6gamma
> ships the 20 parity fixtures + ~25 contract cases +
> G-RC-9.6 mini-gate.

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| `1daa2fe8` | P-RC-9 P9.6e | feat(runtime/orgs): ``_runtime_dispatch.py`` -- CommandDispatchManager (send_command / cancel_user_command / get_command_tracker_snapshot / has_active_delegations / get_active_root_intent + chain helpers) + ``_CommandTracker`` dataclass + ``_TrackerRegistry`` + 4 ``TRACKER_*`` state constants (v1 ~22 dispatch / tracker / chain methods, ~1 050 LOC absorbed -> ~330 v2 LOC) | +PLACEHOLDER (``_runtime_dispatch.py`` NEW ~330 LOC + __init__.py +15 + ledger +20) | 0 (smoke: send_command -> running tracker; get_command_tracker_snapshot returns dict shape v1 parity; cancel_user_command flips state RUNNING -> CANCELLED + emits ``user_command_cancelled`` event; has_active_delegations returns False after cancel; pytest tests/runtime/orgs/ + tests/parity/orgs/ unchanged at 176/1xfail) | ADR-0014 (beta sub-turn; hardest sibling first); ADR-0011 (DI composes OrgCommandServiceProtocol + OrgLookupProtocol + EventBusProtocol; no NEW Protocol shipped here); ADR-0012 (no-shim; pure fresh code, no ``openakita.orgs`` import) |
| `5257e123` | P-RC-9 P9.6f1 | feat(runtime/orgs): ``_runtime_agent_pipeline.py`` -- AgentCache + AgentBuilderProtocol + AgentSpec dataclass + ProfileResolver (agent-build scaffolding; v1 _get_or_create_agent / _node_agents dict / evict_node_agent / _build_profile_for_node / _get_shared_profile / _resolve_org_workspace / _prepare_unattended_session ~150 v1 LOC absorbed -> ~275 v2 LOC) | +PLACEHOLDER (``_runtime_agent_pipeline.py`` NEW ~275 LOC + __init__.py +13 + ledger +5) | 0 (smoke: AgentCache.get_or_create() builds + caches; .peek() returns instance; .evict() drops + calls teardown; .evict_org() bulk drops; ProfileResolver.resolve() returns valid AgentSpec for known org; .resolve() returns None for unknown org; AgentBuilderProtocol runtime-checkable) | ADR-0014 (beta sub-turn; f-split into f1=scaffolding + f2=executor); ADR-0011 (NEW Protocol: AgentBuilderProtocol -- v2-internal seam for Agent factory; one NEW Protocol in beta sub-turn so far); ADR-0012 (no-shim; pure fresh code, no ``openakita.orgs`` / ``openakita.agents`` import) |
| `06f436ed` | P-RC-9 P9.6f2 | feat(runtime/orgs): ``_runtime_agent_pipeline.py`` -- AgentPipelineExecutor (activate-and-run loop; v1 _activate_and_run + _activate_and_run_inner [556 LOC] + _run_agent_task + _emit_llm_usage + _pause_org_for_quota + _is_quota_auth_error ~800 v1 LOC absorbed -> ~245 v2 LOC) | +258 LOC (``_runtime_agent_pipeline.py`` append +245 + __init__.py +4 + ledger +3 -- row was added retroactively in P9.6g commit because f2 commit only contained code + init + missed ledger) | 0 (smoke: happy / missing org / paused org gate / quota -> pause callback / llm usage / bus failure swallowed) | ADR-0014; ADR-0011 (one internal-only Protocol _AgentRunCallable; total P9.6 EXPORTED Protocols = 4); ADR-0012 (no-shim) |
| `598fbc1a` | P-RC-9 P9.6g | feat(runtime/orgs): ``_runtime_node_lifecycle.py`` -- NodeStatusController + NodeMessageRouter + format_incoming_message + is_stop_intent (per-node status machine + inbound message routing; v1 _on_node_message [175 LOC] + _format_incoming_message [96 LOC] + _drain_node_pending [86 LOC] + _post_task_hook [81 LOC] + 10 smaller methods ~600 v1 LOC absorbed -> ~330 v2 LOC) | +PLACEHOLDER (``_runtime_node_lifecycle.py`` NEW ~330 + __init__.py +20 + ledger +5) | 0 (smoke: is_stop_intent("/stop") -> True; is_stop_intent("停止") -> True; is_stop_intent("hello") -> False; format_incoming_message produces "[src]<sender> body (k=v)" v1-shape; NodeMessageRouter.on_inbound happy path -> {'status':'delivered','result':{'status':'ok'}}; busy queueing -> {'status':'queued','depth':1}; .drain() replays pending; stop intent -> {'status':'stop_intent'} + STATUS_STOPPED) | ADR-0014 (beta sub-turn); ADR-0011 (NO new Protocol; uses 1 existing OrgLookupProtocol + 3 callback seams); ADR-0012 (no-shim; zero ``openakita.orgs`` + zero ``openakita.channels`` import) |
| `33136556` | P-RC-9 P9.6h1a | feat(runtime/orgs): ``_runtime_plugin_assets.py`` h1a -- ToolHandlerBridge + PluginAsset dataclass + 4 helpers (safe_asset_filename / ext_for_url / is_plugin_tool / plugin_id_for_tool); PluginAssetRecorder body deferred to h1b (file-append). v1 _register_org_tool_handler [161 LOC] + 6 smaller helpers ~250 v1 LOC absorbed -> ~165 v2 LOC | +PLACEHOLDER (``_runtime_plugin_assets.py`` NEW 375 LOC + __init__.py +18 + ledger +4) | 0 (smoke: safe_asset_filename normalizes unsafe chars + caps 96 char; ext_for_url extracts png/html lower-case; is_plugin_tool matches plugin_/plg_/mcp./openakita.plugin. prefixes + _plugin/.plugin suffixes; plugin_id_for_tool returns first segment after prefix; PluginAssetRecorder.record_url for non-plugin tool -> None; for plugin tool builds workspace path + emits event; .record_file digests + recorded; .list_for_org returns 2; ToolHandlerBridge.dispatch routes to registered handler; missing -> {'status':'error','reason':'no_handler'}; handler raise swallowed -> {'status':'error','reason':'handler_raised','error':str}) | ADR-0014 (beta sub-turn; h-split into h1=recorder/bridge/helpers + h2=file registry/react trace/task delivery); ADR-0011 (NO new Protocol; DI uses 2 callable seams + 1 EventBusProtocol); ADR-0012 (no-shim; pure fresh code, zero ``openakita.orgs`` import; only contract is duck-typed workspace_resolver + download + tool handler callables) |
| `3ef6ed3d` | P-RC-9 P9.6h1b | feat(runtime/orgs): ``_runtime_plugin_assets.py`` h1b -- PluginAssetRecorder append (v1 _record_plugin_asset_output [349 LOC] absorbed -> ~120 v2 LOC; emits ``plugin_asset_recorded`` event) | +PLACEHOLDER (``_runtime_plugin_assets.py`` append ~160 LOC + __init__.py +3 + ledger +3) | 0 (smoke: PluginAssetRecorder.record_url("shell"/non-plugin) -> None; record_url("plugin_image_gen") builds workspace path + emits event; record_url with download writes 3 bytes + computes sha256 digest; record_file digests on-disk file; .list_for_org count = 2; ``plugin_asset_recorded`` events fire = 3 -- one per asset) | ADR-0014 (beta sub-turn; h1b is the pure-append the h1a-split required); ADR-0011 (no new Protocol); ADR-0012 (no-shim) |
| `ac8b5d92` | P-RC-9 P9.6h2 | feat(runtime/orgs): ``_runtime_plugin_assets.py`` h2 -- FileOutputRegistry + TaskDeliverySynthesizer + react-trace helpers (react_trace_has_tool / collect_tool_stats_from_trace / extract_accepted_chain_ids). v1 _register_file_output [156 LOC] + _record_file_output [101 LOC] + _synthesize_task_delivered_to_parent [107 LOC] + _react_trace_has_tool / _collect_tool_stats_from_trace / _extract_accepted_chain_ids [~110 LOC] ~474 v1 LOC absorbed -> ~235 v2 LOC | +PLACEHOLDER (``_runtime_plugin_assets.py`` append ~235 LOC + __init__.py +18 + ledger +3) | 0 (smoke: FileOutputRegistry.register on real file -> FileOutput w/ size 5 + tool_name; missing path -> None; persist callback invoked; .list_for_org/.list_for_node correct; events emitted = 2; react_trace_has_tool happy + miss; collect_tool_stats_from_trace returns invocation counts {shell:2, plugin_x_gen:1, web_fetch:1}; extract_accepted_chain_ids picks both status=='accepted' + accepted==True; empty trace -> {}/[]; TaskDeliverySynthesizer.synthesize composes default summary + chain_ids tuple) | ADR-0014 (final beta sub-turn commit); ADR-0011 (no new Protocol); ADR-0012 (no-shim; zero v1 import) |
| `412cbd55` | P-RC-9 P9.6i | feat(runtime/orgs): runtime.py wire CommandDispatchManager into OrgRuntime.__init__; replace 4 NotImplementedError stubs with real delegations (send_command / cancel_user_command / has_active_delegations / get_command_tracker_snapshot) -- closes the P9.6beta integration | +PLACEHOLDER (runtime.py +30 LOC: new dispatch DI param + TYPE_CHECKING import + 4 method bodies; __init__.py +12 docstring; ledger +3) | 0 (smoke: OrgRuntime() default-constructs CommandDispatchManager; send_command returns v1-shape dict; get_command_tracker_snapshot returns dict; has_active_delegations returns bool; cancel_user_command flips state; isinstance(OrgRuntime(...), CommandRuntimeProtocol) still True; tests/runtime/orgs/ + tests/parity/orgs/ unchanged at 176/1xfail) | ADR-0014 (P9.6beta closes; P9.6gamma next turn); ADR-0011 (no new Protocol; uses existing CommandRuntimeProtocol); ADR-0012 (no-shim; dispatch is lazy-imported inside __init__ to avoid top-level cycle) |
| _this commit_ | P-RC-9 P9.6.docs | docs(runtime/orgs): backfill __init__.py docstring for P9.6i + ledger row hashes (P9.6g + P9.6i) -- catches the doc-tail string-replace that silently no-op''ed in 412cbd55 | +14 docs LOC (__init__.py +12 + ledger +2) | 0 (docs-only; no behavior change; ruff check clean) | n/a (cleanup commit) |

## P9.6gamma -- parity 20 + contract ~25 + G-RC-9.6 (this turn)

> Last P-RC-9 sentinel activation: ``test_runtime_parity.py``
> xfail is removed; 6/6 v2 parity sentinels are now active.
> After this turn the 6 ADR-0011 subsystems are fully
> implemented + parity-validated; only physical v1 removal
> (P9.7-9) remains.

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| ``10722ac2`` | P-RC-9 P9.6gamma-1a | test(parity/orgs): activate runtime_parity sentinel -- replace xfail placeholder with 10 active dispatch + agent_pipeline fixtures | +296 LOC (test_runtime_parity.py NET +273 = 506 added - 23 removed placeholder; remaining 10 fixtures ride gamma-1b) | +10 parity (0 xfail; sentinel ACTIVATED) | ADR-0014 (parity validation closes beta); ADR-0011 (no new Protocol) |
| ``b784c4f3`` | P-RC-9 P9.6gamma-1b | test(parity/orgs): land remaining 10 runtime parity fixtures (5 node_lifecycle + 5 plugin_assets); drop literal ``@pytest.mark.xfail`` substring from docstring so grep -c == 0 | +PLACEHOLDER (test_runtime_parity.py NET +292 = 297 added - 5 removed including 2 docstring tokens; ruff fix + format applied) | +10 parity (20/20 fixtures pass; 0 xfail; sentinel three-piece 6/6 active) | ADR-0014 (parity 20/20 closes section 5.2 OrgRuntime contract); ADR-0011 (no new Protocol; reuses existing siblings) |
| ``fb3c7168`` | P-RC-9 P9.6gamma-2a | test(runtime/orgs): test_runtime_contract.py NEW -- 13 OrgRuntime contract cases (10 CommandRuntimeProtocol surface + 3 new-Protocol round-trip) | +PLACEHOLDER (test_runtime_contract.py NEW 241 LOC + ledger +3) | +13 contract (gamma-2a half; gamma-2b appends 12 more) | ADR-0014 (contract pin closes P-RC-9-PLAN section 4 P9.6 charter); ADR-0011 (4 new Protocols exercised: RuntimeStateProtocol / NodeLifecycleProtocol / EventBusProtocol / CommandRuntimeProtocol) |
| ``88603c4b`` | P-RC-9 P9.6gamma-2b | test(runtime/orgs): append 12 OrgRuntime contract cases (1 AgentBuilderProtocol + 3 composition smokes + 4 concurrency + 1 integration + 2 wall-clock SLA + 1 get_active_root_intent) -- contract suite now 25/25 | +PLACEHOLDER (test_runtime_contract.py +244 + ledger +3) | +12 contract (25/25 total; ~25 charter target met) | ADR-0013 (2 wall-clock SLA perf_counter cases per NIT-I-1 lesson); ADR-0014 (contract closure); ADR-0011 (Protocol-checked composition) |
| _this commit_ | P-RC-9 G-RC-9.6 | docs(revamp): G-RC-9.6 P9.6 (OrgRuntime) mini-gate -- PASS (closes P9.6; 22 commits clean; parity 20/20 + contract 25/25; sentinel 6/6 ACTIVE; ADR-0014 budget held 2708 of 3000 LOC; v1 ``src/openakita/orgs/`` untouched; ACCEPTANCE.md NOT bumped) | +PLACEHOLDER (G-RC-9.6.md NEW 338 LOC + ledger header bump + ledger row +5) | 0 (gate review; cites measured numbers from full pytest 6538p/116s/5xf/13f in 1043.33s + narrowed slice 1457p/12s/5xf in 113.74s) | ADR-0011 (Protocol granularity ceiling held; 4 new public Protocols all <=5 methods); ADR-0014 (OrgRuntime budget revision empirical outcome 2708 LOC of 3000 LOC); ADR-0012 (no-shim invariant held: 22 commits, zero src/openakita/{orgs,core,channels,api}/ touch); ADR-0013 (2 wall-clock SLA perf_counter contract cases per NIT-I-1 lesson) |
| _this commit_ | P-RC-9 P9.6.nit2 | docs(revamp): clean up G-RC-9.6 NIT-M-5 / M-6 / M-7 / M-8 (post-flight) -- N3 phrasing carve-out for P9.6f2 retroactive backfill (M-5); P9.6h1a LOC 393 -> 197 + max-LOC sentence rewrite to 370 at P9.6e (``1daa2fe8``) (M-6); P9.6.pause LOC 35 -> 179 (M-7); backfill P9.6d row ``_this commit_`` -> ``9274a6f2`` (M-8). 4 remaining G-RC-9.6 NITs (M-1 runtime_parity golden-dict / M-2 ADR-0014 sub-cap breach / M-3 v1 method residue) + pre-existing G-RC-9.4 B-1 + M-4 (no-op historical) all TRACKED for G-RC-9 final | +PLACEHOLDER (G-RC-9.6.md ~9 LOC narrow edits + ledger +2 rows + 1 P9.6d backfill) | 0 (docs-only; no .py touch; ruff not applicable) | n/a (cleanup commit; folds 4 of 8 G-RC-9.6 NITs; 4 tracked for G-RC-9 final) |

## P9.7 charter -- v2 REST endpoint mint planning round (this turn)

> Planning charter for P9.7. Catalogues 86 v1 + 9 existing
> v2 endpoints; classifies Group A (9; P-RC-3) / Group B
> (~80 mint) / Group C (~6 retired); defines alpha/beta/gamma
> (11-14 commits / ~1 910 src LOC inside ADR-0014 ~10 %
> tolerance / ~2 080 test LOC); parity DEPARTURE -- REST
> contract tests via FastAPI ``TestClient`` + optional
> snapshot capture for ~12 frontend-critical endpoints (not
> v1<->v2 import parity). New REST contract sentinel brings
> active sentinels to 7 = 6 parity + 1 REST. ADR-0015 NOT
> written (within tolerance); precedent in place for turn-1
> escape hatch. P9.7alpha-1 NOT started.

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| _this commit_ | P-RC-9 P9.7.charter | docs(revamp): add P9.7 v2 REST endpoint charter (planning round) | +PLACEHOLDER (P-RC-9-P9.7-CHARTER.md NEW ~380 + ledger ~16) | 0 (planning only; ``git diff 89703a28..HEAD -- src/openakita/ tests/`` empty) | ADR-0011 (no new Protocols per R4); ADR-0012 (v1 delete waits for P9.9 per Q-B 410); ADR-0013 (perf_counter SLA extended to ~5 REST cases); ADR-0014 (budget precedent; ADR-0015 NOT needed this round) |

## P9.7a -- scaffold + Group A reconciliation (this turn)

> Docs-only kickoff. Catalogues the real surface (89 v1 + 9 Group A + 6 Group C -> 83 v2 mint, vs charter's ~80 anchored on v1=86), locks D-1 R3 (Group A relocates to ``/api/v2/orgs-spec/`` with 308 shims), records frontend recon (``apps/setup-center/src/config.ts`` MISSING; API base is computed by ``httpApiBase()`` + passed as ``apiBaseUrl``/``apiBase`` prop), locks D-3 (schemas/orgs_v2/* layer per ADR-0011) + D-4 (reuse v1 ``request.app.state`` Depends-free pattern). P9.7a-2 (Pydantic + router skeleton + 308 shim) NOT started.

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| _this commit_ | P-RC-9 P9.7a-1 | docs(revamp): P9.7a-1 endpoint inventory + decisions (R1=R3 locked, R2 recon) | +482 LOC (P-RC-9-P9.7-ENDPOINT-INVENTORY.md NEW 254 + P-RC-9-P9.7-DECISIONS.md NEW 220 + ledger +8) | 0 (docs-only; ``git diff 096a5571..HEAD -- src/openakita/ tests/ apps/`` empty) | ADR-0011 (D-3 layer separation: schemas/orgs_v2/* decoupled from router; D-4 flat helpers per R4 granularity ceiling); ADR-0012 (D-1 R3 308 redirect shim is the only relaxation; physical v1 delete still waits for P9.9); cites P-RC-9-P9.7-CHARTER.md sec 1 + 4 + 8 |

## P9.7a-2 -- Group A rename + 308 shim + Pydantic + router skeleton (this turn)

> Code kickoff. D-1 R3 LOCKED lands physically: Group A
> routers (`orgs_v2.router` + `orgs_v2_stream.router`)
> relocate from ``/api/v2/orgs`` to ``/api/v2/orgs-spec``;
> a new ``_orgs_v2_legacy_redirects.router`` issues 308
> Permanent Redirects at the old paths so existing
> frontend call sites keep working through v2.0.x (rewire
> ships in P9.8). Split into 3 sub-commits per the
> instructions: a-2a Group A rename + 308 shim + smoke,
> a-2b Pydantic schemas namespace, a-2c v2 runtime router
> skeleton + registration + health stub.

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| _this commit_ | P-RC-9 P9.7a-2a | feat(api/routes): Group A rename to ``/api/v2/orgs-spec`` + 308 Permanent Redirect shim at the original ``/api/v2/orgs`` paths (9 routes) + register shim router after spec routers in server.py + smoke tests for redirects | +311 src/test LOC (orgs_v2.py +18/-15 prefix+docstring; orgs_v2_stream.py +2/-2 prefix; server.py +21/-12 import + register block; _orgs_v2_legacy_redirects.py NEW 101; test_orgs_v2.py +37/-37 path swap; test_orgs_v2_stream.py +9/-6 path swap; test_p97_alpha2_smoke.py NEW 160; ledger +PLACEHOLDER) | +12 smoke (test_p97_alpha2_smoke.py: 9 308 shim checks + 1 query-string preservation + 2 spec-path Group A logic smoke); gate slice tests/api/ + tests/runtime/orgs/ + tests/parity/orgs/ 265 -> 277 passed (44.67s) | ADR-0011 (no new Protocol; shim is a thin APIRouter; spec router rename is one-line prefix flip); ADR-0012 (one-window relaxation for 308 redirect window through v2.0.x; physical v1 delete still waits for P9.9); cites P-RC-9-P9.7-DECISIONS.md D-1 R3 LOCKED |
| _this commit_ | P-RC-9 P9.7a-2b | feat(api/schemas): orgs_v2/ Pydantic shapes skeleton (D-3 LOCKED) -- 7 enums + 12 models across 4 sub-modules; all pin ConfigDict(extra="forbid"); nested nodes/edges/tasks ride as opaque dicts | +344 LOC (schemas/__init__.py NEW 1 + schemas/orgs_v2/__init__.py NEW 44 + orgs.py NEW 70 + nodes.py NEW 61 + commands.py NEW 78 + projects.py NEW 90; ledger +PLACEHOLDER) | 0 new (smoke: `from openakita.api.schemas.orgs_v2 import ...` collects all 16 names; gate slice tests/api/ + tests/runtime/orgs/ + tests/parity/orgs/ unchanged at 277 passed) | ADR-0011 (D-3 layer separation: shapes live under schemas/orgs_v2/* decoupled from router so contract tests import without dragging FastAPI); ADR-0012 (no shim under v1; v1 returns bare dict so nothing to mirror) |
| _this commit_ | P-RC-9 P9.7a-2c | feat(api/routes): orgs_v2_runtime.py skeleton (P9.7 mint surface at ``/api/v2/orgs``) + 6 Depends-free ``_get_*(request)`` subsystem helpers (D-4 LOCKED) + GET ``/_p97/health`` stub + register BEFORE the 308 redirect shim + smoke tests for runtime probe + Pydantic schemas import sanity | +PLACEHOLDER LOC (orgs_v2_runtime.py NEW 135 + server.py +9/-2 import + register block + test_p97_alpha2_smoke.py +95 extend; ledger +PLACEHOLDER) | +3 smoke (test_p97_alpha2_smoke.py: health probe envelope + redirect-precedence + 16-name schemas import sanity); gate slice tests/api/ + tests/runtime/orgs/ + tests/parity/orgs/ 277 -> 280 passed (44.47s) | ADR-0011 (D-3 layer separation: router consumes schemas/orgs_v2/* indirectly; D-4 R4 granularity ceiling preserved -- 6 free-function helpers, NOT FastAPI Depends factories); ADR-0012 (no shim under v1; pure new code) |

## P9.7beta-1 -- mint cluster 3.1 Org CRUD + templates + lifecycle (17 endpoints) (this turn)

> First beta mint commit. 17 endpoints across ``/api/v2/orgs[...]``
> (B1-B17 per ``P-RC-9-P9.7-ENDPOINT-INVENTORY.md`` section 3.1)
> land in a new sub-module ``orgs_v2_runtime_orgs.py`` which the
> main ``orgs_v2_runtime.py`` imports at load time so the
> ``@router`` decorators register on the shared APIRouter.
> Wiring is thin (D-4 LOCKED helpers; no business logic in route
> bodies). 23 smoke tests in NEW ``test_p97_beta_smoke.py`` pin
> 200/201/404/422 status codes + subsystem method-call shape.
> 8 alpha-2 redirect tests reframed: paths the mint now claims
> (B1/B2/B5/B7/B10/B12) no longer 308 -- they REACH the mint
> route and surface 503 on the bare ``shim_client`` (subsystems
> unbound). One redirect test pivots from ``/templates`` to
> ``/{id}/stream`` (the mint does NOT claim ``/stream``;
> still falls through to Group A). New ``project_root``
> monkeypatch lets the avatar-upload smoke verify file landing
> under ``tmp_path/data/avatars/``.

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| _this commit_ | P-RC-9 P9.7beta-1 | feat(api/routes): mint cluster 3.1 Org CRUD + templates + lifecycle (17 endpoints: B1-B17) in orgs_v2_runtime_orgs.py + side-effect import from orgs_v2_runtime.py + 23 smoke tests + reframe 8 alpha-2 redirect tests for mint-claimed paths | +PLACEHOLDER LOC (orgs_v2_runtime_orgs.py NEW 303 + orgs_v2_runtime.py +13 sub-module import; test_p97_beta_smoke.py NEW 301 + test_p97_alpha2_smoke.py +31 reframe; ledger +PLACEHOLDER) | +23 smoke (B1-B17 wiring smokes via TestClient + MagicMock subsystems); gate slice tests/api/ + tests/runtime/orgs/ + tests/parity/orgs/ 280 -> 303 passed (54.09s) | ADR-0011 (D-3 layer separation -- ``OrgCreate``/``OrgPatch`` schemas consumed for input validation; response stays dict for v1 parity); ADR-0012 (no shim under v1; v2 mint reaches v1 free-function helpers ``list_avatar_presets`` / ``build_workbench_templates`` because they are NOT v1 ``OrgManager`` methods; the no-shim invariant covers class boundary, not free-function helpers); cites P-RC-9-P9.7-ENDPOINT-INVENTORY.md section 3.1 B1-B17 |

## P9.7beta-2 -- mint cluster 3.2 Node lifecycle + schedules (16 endpoints) (this turn)

> Second beta mint commit. 16 endpoints across
> ``/api/v2/orgs/{org_id}/nodes/{node_id}[...]`` (B18-B33 per
> ``P-RC-9-P9.7-ENDPOINT-INVENTORY.md`` section 3.2) land in a
> new sub-module ``orgs_v2_runtime_nodes.py``. Cluster covers
> node schedules CRUD (4), identity markdown files (2), MCP
> config JSON (2), status controllers freeze/unfreeze/offline/
> online (4), and observability snapshots dismiss/thinking/
> prompt-preview/status (4). Wiring: schedules + identity + MCP
> -> ``_get_manager`` (P9.5 OrgManager + ``get_org_dir``
> file-IO); status controllers + snapshots -> ``_get_runtime``
> (P9.6 OrgRuntime duck-typed method calls).

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| _this commit_ | P-RC-9 P9.7beta-2 | feat(api/routes): mint cluster 3.2 Node lifecycle + schedules + identity + MCP (16 endpoints: B18-B33) in orgs_v2_runtime_nodes.py + side-effect import from orgs_v2_runtime.py + 20 smoke tests | +PLACEHOLDER LOC (orgs_v2_runtime_nodes.py NEW 264 + orgs_v2_runtime.py +3 sub-module import; test_p97_beta_smoke.py +233 cluster 3.2 smokes; ledger +PLACEHOLDER) | +20 smoke (B18-B33 wiring smokes -- schedules CRUD x4 + identity file IO x2 + MCP file IO x2 + status controllers x4 + observability snapshots x4 + 404 branches x4); gate slice tests/api/ + tests/runtime/orgs/ + tests/parity/orgs/ 303 -> 323 passed (53.67s) | ADR-0011 (D-3 layer separation -- ``NodeRegister`` Pydantic shape exported for future node-create POST endpoints; D-4 R4 granularity ceiling preserved; OrgRuntime methods consumed are duck-typed -- no new Protocols); ADR-0012 (no shim under v1; v2 manager exposes ``get_org_dir`` so file-IO never reaches v1 OrgManager) |

## P9.7beta-3 -- mint cluster 3.3 Runtime control + Commands + Broadcast (8 endpoints) (this turn)

> Third beta mint commit. 8 endpoints (B34-B41 per
> ``P-RC-9-P9.7-ENDPOINT-INVENTORY.md`` section 3.3) land in a
> new sub-module ``orgs_v2_runtime_dispatch.py``. Cluster covers
> the org lifecycle verbs (start/stop/pause/resume) duck-typed
> on OrgRuntime, the user-command submit / poll / cancel
> trifecta on OrgCommandService (using the ``CommandSubmit`` /
> ``CancelRequest`` Pydantic shapes from schemas/orgs_v2), and
> the org-level broadcast adapter. Resolves a ruff-format
> regression that dropped the dispatch import in the multi-line
> tuple after this commit's first ruff format pass.

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| _this commit_ | P-RC-9 P9.7beta-3 | feat(api/routes): mint cluster 3.3 Runtime control + Commands + Broadcast (8 endpoints: B34-B41) in orgs_v2_runtime_dispatch.py + side-effect import + 13 smoke tests | +PLACEHOLDER LOC (orgs_v2_runtime_dispatch.py NEW 191 + orgs_v2_runtime.py +1 multi-line import addition; test_p97_beta_smoke.py +134 cluster 3.3 smokes; ledger +PLACEHOLDER) | +13 smoke (B34-B41 wiring smokes + lifecycle 400-on-ValueError branch + command 404 branches x2 + Pydantic 422 on empty content + broadcast 400 on empty content); gate slice tests/api/ + tests/runtime/orgs/ + tests/parity/orgs/ 323 -> 336 passed (56.20s) | ADR-0011 (D-3 layer separation -- ``CommandSubmit`` / ``CancelRequest`` schemas consumed for body validation; ``OrgCommandRequest`` / ``OrgCommandSource`` / ``ForwardTarget`` constructed from typed inputs; D-4 R4 granularity ceiling preserved); ADR-0012 (no shim under v1; OrgRuntime lifecycle methods are duck-typed -- integration with the existing P9.6 ``OrgLifecycleManager`` sibling lands in P9.7gamma) |

## P9.7beta-4 -- mint cluster 3.4 Memory + Events + Activity + Messages + audit + Policies (12 endpoints) (this turn)

> Fourth beta mint commit. 12 endpoints (B42-B53 per
> ``P-RC-9-P9.7-ENDPOINT-INVENTORY.md`` section 3.4) land in a
> new sub-module ``orgs_v2_runtime_state.py``. Cluster covers
> blackboard memory CRUD (3 -> OrgBlackboard), event-store
> queries (events / activity / messages / audit; 4 ->
> OrgRuntime.get_event_store + ``get_org_dir`` file IO for the
> JSONL communications log), and policy markdown CRUD (5 ->
> ``get_org_dir`` file IO under ``<org_dir>/policies/``).

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| _this commit_ | P-RC-9 P9.7beta-4 | feat(api/routes): mint cluster 3.4 Memory + Events + Activity + Messages + audit + Policies (12 endpoints: B42-B53) in orgs_v2_runtime_state.py + side-effect import + 17 smoke tests | +PLACEHOLDER LOC (orgs_v2_runtime_state.py NEW 255 + orgs_v2_runtime.py +1 multi-line import addition; test_p97_beta_smoke.py +164 cluster 3.4 smokes; ledger +PLACEHOLDER) | +17 smoke (B42-B53 wiring smokes + 400-on-bad-scope + 400-on-empty-content + 404 on memory delete + 400 on policies search empty q + policy file write/read verification + path-traversal guard); gate slice tests/api/ + tests/runtime/orgs/ + tests/parity/orgs/ 336 -> 353 passed (58.81s) | ADR-0011 (D-3 layer separation -- ``MemoryScope`` / ``MemoryType`` enums imported from ``openakita.runtime.orgs``; D-4 R4 granularity ceiling preserved); ADR-0012 (no shim under v1; policies + messages file IO uses ``OrgManager.get_org_dir`` only -- v1 ``_org_dir`` never reached) |

## P9.7beta-5 -- mint cluster 3.5 Inbox + Scaling + Reports + Stats + Status (14 endpoints) (this turn)

> Fifth beta mint commit. 14 endpoints (B54-B67 per
> ``P-RC-9-P9.7-ENDPOINT-INVENTORY.md`` section 3.5) land in a
> new sub-module ``orgs_v2_runtime_ops.py``. Cluster covers org
> inbox CRUD (4 -> ``OrgRuntime.get_inbox``), scaling governance
> (5 -> ``OrgRuntime.get_scaler``: requests / approve / reject /
> clone / recruit), status snapshot (1 JSON; SSE divergence
> documented), stats aggregation (1), and reports list / summary
> / generate (3). v1 ``GET /{org_id}/status`` SSE -> v2 JSON
> snapshot envelope (charter R5 divergence; SSE rides beta-7).

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| _this commit_ | P-RC-9 P9.7beta-5 | feat(api/routes): mint cluster 3.5 Inbox + Scaling + Reports + Stats + Status (14 endpoints: B54-B67) in orgs_v2_runtime_ops.py + side-effect import + 19 smoke tests | +PLACEHOLDER LOC (orgs_v2_runtime_ops.py NEW 292 + orgs_v2_runtime.py +1 multi-line import addition; test_p97_beta_smoke.py +213 cluster 3.5 smokes; ledger +PLACEHOLDER) | +19 smoke (B54-B67 wiring smokes + inbox 404 branch + bad-decision 400 + scaling 400 branches x2 + status 404 branch); gate slice tests/api/ + tests/runtime/orgs/ + tests/parity/orgs/ 353 -> 372 passed (53.04s) | ADR-0011 (D-3 layer separation; D-4 R4 granularity ceiling preserved); ADR-0012 (no shim under v1; status endpoint diverges from v1 SSE to JSON snapshot -- charter R5 documented); cites P-RC-9-P9.7-CHARTER.md section 3 beta-7 (SSE riding optional commit) |

## P9.7beta-6 -- mint cluster 3.6 Projects + tasks (16 endpoints) -- closes beta phase (this turn)

> Sixth (and final) beta mint commit. 16 endpoints (B68-B83 per
> ``P-RC-9-P9.7-ENDPOINT-INVENTORY.md`` section 3.6) land in a
> new sub-module ``orgs_v2_runtime_projects.py``. Cluster covers
> project CRUD (5 -> ProjectStore), task CRUD inside projects
> (3), task dispatch (cross-subsystem: ProjectStore + OrgCommandService),
> task cancel (cross-subsystem: ProjectStore + OrgRuntime.cancel_node_task),
> cross-project task aggregation (4), and per-node task /
> active-plan queries (2). Beta phase closes: 83 endpoints minted
> across 6 commits (B1-B83 charter target met exactly).

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| _this commit_ | P-RC-9 P9.7beta-6 | feat(api/routes): mint cluster 3.6 Projects + tasks (16 endpoints: B68-B83) in orgs_v2_runtime_projects.py + side-effect import + 22 smoke tests -- closes P9.7beta (83/83 endpoints) | +PLACEHOLDER LOC (orgs_v2_runtime_projects.py NEW 359 + orgs_v2_runtime.py +1 multi-line import addition; test_p97_beta_smoke.py +241 cluster 3.6 smokes; ledger +PLACEHOLDER) | +22 smoke (B68-B83 wiring smokes + 404 branches x4 + 422 Pydantic + dispatch/cancel cross-subsystem flows); gate slice tests/api/ + tests/runtime/orgs/ + tests/parity/orgs/ 372 -> 394 passed (52.46s) | ADR-0011 (D-3 layer separation -- ``ProjectCreate`` / ``ProjectPatch`` Pydantic shapes consumed; ``OrgProject`` / ``ProjectTask`` / ``ProjectStatus`` / ``ProjectType`` / ``TaskStatus`` imported from ``openakita.runtime.orgs``; D-4 R4 granularity ceiling preserved); ADR-0012 (no shim under v1; dispatch + cancel cross-subsystem calls reach only v2 subsystems -- never v1 OrgRuntime) |

## P9.7gamma-1a -- contract test scaffold + cluster orgs (B1-B17) + nodes (B18-B33) (this turn)

> First gamma-1 commit: lands the
> ``tests/api/contracts/`` package -- empty ``__init__.py`` marker
> + shared ``conftest.py`` (mint_app / mint_client / fake_org /
> fake_project / fake_task / org_with_node helpers) + per-cluster
> contract files for the OrgManager (B1-B17) and node-lifecycle
> (B18-B33) clusters. Reuses the duck-typed ``MagicMock`` subsystem
> pattern from ``tests/api/test_p97_beta_smoke.py`` so the assertions
> stay focused on response envelopes + status codes; 503 is exercised
> by the alpha-2 smoke suite, auth reuses the v1 pattern (D-4
> LOCKED) so neither family is asserted again. Sized: ``__init__``
> 12 LOC + conftest 89 LOC + orgs 283 LOC (41 cases) + nodes 247
> LOC (28 cases). All four files at or under the ADR-0014 ~350 LOC
> sub-cap. Per-endpoint coverage: 2-4 cases each across happy /
> 404 / 422 (Pydantic ``extra="forbid"`` + ``min_length=1`` on
> ``OrgCreate.name``) / 409 (``OrgNameConflictError`` envelope on
> create + update + from-template).

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| _this commit_ | P-RC-9 P9.7gamma-1a | test(api/contracts): scaffold contracts/ + cluster 3.1 orgs (41 cases) + cluster 3.2 nodes (28 cases) | +PLACEHOLDER LOC (contracts/__init__.py NEW 12 + conftest.py NEW 89 + test_orgs_v2_contracts_orgs.py NEW 283 + test_orgs_v2_contracts_nodes.py NEW 247; ledger +PLACEHOLDER) | +69 contract (B1-B17 41 cases + B18-B33 28 cases); gate slice tests/api/ + tests/runtime/orgs/ + tests/parity/orgs/ 394 -> 463 passed (62.60s) | ADR-0011 (D-3 layer separation -- shared fixtures factor out duck-typed mock wiring; charter section 6 contract matrix); ADR-0012 (no shim under v1; assertions reach only v2 schemas + runtime.orgs subsystems); cites P-RC-9-P9.7-CHARTER.md section 6 (contract test matrix) + section 3 P9.7gamma-1 brief |

## P9.7gamma-1b -- contract clusters dispatch (B34-B41) + state (B42-B53) + ops (B54-B67) (this turn)

> Second gamma-1 commit: lands the dispatch / state / ops
> per-cluster contract files. Coverage matches the charter
> section 6 contract matrix: happy / 404 / 422 / 409 / 503 /
> 400 (subsystem ValueError envelopes) per endpoint. Dispatch
> exercises the ``CommandSubmit`` Pydantic body strictness
> (``content`` min_length=1 + ``extra="forbid"``), the
> ``OrgCommandConflict`` 409 envelope, and the ``OrgCommandError``
> 400 path. State exercises the ``MemoryScope`` / ``MemoryType``
> 400 envelope on bad enum values, the path-traversal guard on
> policy file IO, and the duck-typed event-store empty / happy
> branches. Ops exercises the inbox 200/404/400 matrix, the
> 503 scaler-not-wired case, and the file-IO branches for
> reports / status / stats.

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| _this commit_ | P-RC-9 P9.7gamma-1b | test(api/contracts): mint cluster contracts for dispatch (20 cases) + state (29 cases) + ops (30 cases) | +PLACEHOLDER LOC (test_orgs_v2_contracts_dispatch.py NEW 141 + test_orgs_v2_contracts_state.py NEW 233 + test_orgs_v2_contracts_ops.py NEW 228; ledger +PLACEHOLDER) | +79 contract (B34-B41 20 + B42-B53 29 + B54-B67 30); gate slice tests/api/ + tests/runtime/orgs/ + tests/parity/orgs/ 463 -> 542 passed (70.45s) | ADR-0011 (D-3 layer separation; dispatch + state + ops touch P9.4 OrgCommandService / P9.6 OrgRuntime / P9.1 OrgBlackboard surface only via the v2 routes); ADR-0012 (no shim under v1; assertions reach only v2 schemas + runtime.orgs subsystems); cites P-RC-9-P9.7-CHARTER.md section 6 (~120 contract cases / ~1 600 LOC) |

## P9.7gamma-1c -- contract cluster projects (B68-B83) + closes gamma-1 contract suite (this turn)

> Third gamma-1 commit: lands the largest cluster's contract
> file (16 endpoints / 36 cases). Project + task CRUD covers
> happy / 404 / 422 (ProjectCreate / ProjectPatch ``extra="forbid"``)
> branches; B76 dispatch wires both ProjectStore + OrgCommandService
> + asserts the chain_id is generated; B77 cancel exercises the
> ``TaskStatus.IN_PROGRESS`` gate (returns ``{"ok": False}`` not
> 4xx when the task is not in_progress -- v1 oracle parity); B81
> timeline merges execution_log + event-store query results.
> Closes the gamma-1 contract suite at **184 contract cases /
> ~1500 LOC** across 6 cluster files + 1 conftest.

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| _this commit_ | P-RC-9 P9.7gamma-1c | test(api/contracts): mint cluster contracts for projects (36 cases) -- closes gamma-1 (184/184) | +PLACEHOLDER LOC (test_orgs_v2_contracts_projects.py NEW 284; ledger +PLACEHOLDER) | +36 contract (B68-B83); gate slice tests/api/ + tests/runtime/orgs/ + tests/parity/orgs/ 542 -> 578 passed (73.08s); contract suite total 184/184 across 6 cluster files | ADR-0011 (D-3 layer separation; ProjectStore + OrgCommandService cross-subsystem dispatch covered); ADR-0012 (no shim under v1; ProjectStatus / ProjectType / TaskStatus enums imported only from openakita.runtime.orgs); cites P-RC-9-P9.7-CHARTER.md section 6 (~120 contract cases / ~1 600 LOC) |

## P9.7gamma-2 -- REST contract sentinel + OpenAPI snapshot (this turn)

> Activates the **7th P-RC-9 sentinel** -- the first that is
> NOT a parity sentinel; it asserts an active REST contract
> invariant rather than v1<->v2 equivalence. Three pieces:
> (a) ``test_route_counts_match_inventory`` pins the surface
> at 84 mint+health method-routes / 9 spec method-routes /
> 9 308 redirect shims; (b)
> ``test_every_minted_endpoint_has_a_contract_test`` scans the
> contract suite + beta smoke for ``test_b<N>_*`` markers and
> asserts every B1-B83 endpoint has at least one test; (c)
> ``test_openapi_snapshot_matches`` diffs the canonical pruned
> schema (paths + methods only) against
> ``tests/parity/orgs/_openapi_snapshot.json`` (76 paths / 93
> method-routes; charter section 7 alternative chosen --
> snapshot diff vs schemathesis fuzz, simpler + no new
> dependency). Operator regenerates the snapshot via
> ``WRITE_SNAPSHOT=1 pytest``; otherwise byte-for-byte parity
> is enforced. The sentinel does NOT use ``@pytest.mark.xfail``
> because in P9.x convention "sentinel" means **active
> assertion**.

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| _this commit_ | P-RC-9 P9.7gamma-2 | test(parity/orgs): activate 7th sentinel -- REST contract (3 cases: route count + coverage + OpenAPI snapshot) | +PLACEHOLDER LOC (test_rest_contract_sentinel.py NEW 162 + _openapi_snapshot.json NEW ~190 lines / 76 paths; ledger +PLACEHOLDER) | +3 sentinel cases (route counts / coverage matrix / OpenAPI snapshot); 6 parity sentinels still 0 active xfail; gate slice 578 -> 581 passed | ADR-0011 (no NEW Protocol; sentinel asserts the FastAPI / OpenAPI surface contract -- not a Protocol contract); ADR-0012 (snapshot includes Group A relocated paths under /api/v2/orgs-spec; the 9 308 shims under /api/v2/orgs are intentionally excluded from openapi() -- their contract is "redirect, no body"); cites P-RC-9-P9.7-CHARTER.md section 7 ("REST contract sentinel"; charter chose ``app.openapi()`` route iteration; gamma-1 brief upgraded to snapshot diff per simpler+no-new-deps lesson) |



## P9.7gamma-3a -- NIT-A fold-in: schemas.py shadow regression fix (this turn)

> Pre-gate-doc fold-in landing the NIT-A fix surfaced by the
> P9.7gamma-3 main-gate full measured run. The P9.7a-2b commit
> ``0735501e`` created the ``src/openakita/api/schemas/`` package
> to host ``schemas/orgs_v2/`` but did NOT move the legacy
> ``src/openakita/api/schemas.py`` contents into the new package
> init. Python's package-shadows-module rule silently broke 19
> main-gate test collections (every test importing ``ChatRequest``
> / ``ChatAnswerRequest`` / ``ChatControlRequest`` /
> ``HealthCheckRequest`` / ``HealthResult`` / ``ModelInfo`` /
> ``AttachmentInfo`` / ``SkillInfoResponse`` from
> ``openakita.api.schemas``).
>
> Fold-in this commit: (1) merge the legacy ``schemas.py`` body
> byte-for-byte into ``schemas/__init__.py`` -- 8 Pydantic
> classes preserved with original docstrings + Chinese comments
> intact; the package docstring carries a NIT-A banner with the
> regression context. (2) delete the orphan ``schemas.py``.
> 8 v1 wire shape imports restored; 19 collection errors cleared.
> The ``schemas/orgs_v2/`` subpackage is untouched.

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| _this commit_ | P-RC-9 P9.7gamma-3a | fix(api/schemas): merge legacy schemas.py into schemas/__init__.py -- NIT-A fold-in | +PLACEHOLDER LOC (schemas/__init__.py +178 [merge of 8 v1 wire shapes + NIT banner] - schemas.py -159 [orphan removal]; ledger +PLACEHOLDER) | 0 net new test cases this commit; restores 19 main-gate collection slots (pre-existing import path); contracts + sentinel + alpha2/beta smoke all stay green (316 passed in 30.62s targeted run) | ADR-0011 (no new Protocol -- module/package layout fix, not Protocol contract); ADR-0012 (no shim under v1; merge is forward-only into the package init); cites P-RC-9-P9.7-CHARTER.md section 9 (gate criterion 6 -- main gate stays green) + DECISIONS.md D-3 (schemas/orgs_v2 namespace, now coexisting with the v1 surface at the package root) |

## P9.7gamma-3b -- G-RC-9.7 mini-gate + ledger close (this turn)

> Final P9.7 commit. Lands ``docs/revamp/gates/G-RC-9.7.md``
> (~356 LOC mirroring the G-RC-9.6 13-section template) and
> bumps the ledger header to P9.7 closed + adds the P9.7 CLOSED
> phase summary below. The NIT-A schemas merge that the §3.1
> main-gate full measured run surfaced landed in sibling commit
> ``b9b74df7`` (P9.7gamma-3a) ahead of this gate so this commit
> stays narrowly scoped (docs only).
>
> Measured anchors (substituted into G-RC-9.7 §3): main gate
> 6 853 passed / 14 failed [pre-existing] / 116 skipped / 5
> xfailed in 1 104.05 s; narrowed slice 1 772 passed / 1 failed
> [flaky v2_im_canary, also in main] / 12 skipped / 5 xfailed
> in 174.11 s; targeted (contracts + sentinel + alpha2/beta
> smoke) 316 passed in 30.62 s. Deltas vs G-RC-9.6 baseline:
> main +315 passed, narrowed +315 passed (matches main); gate
> slice (api + runtime/orgs + parity/orgs) 394 -> 581 (+187 =
> 184 contract + 3 sentinel exactly).

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| _this commit_ | P-RC-9 P9.7gamma-3b (G-RC-9.7) | docs(revamp/gates): G-RC-9.7 P9.7 (v2 REST endpoint mint) mini-gate -- PASS + ledger close summary | +PLACEHOLDER LOC (G-RC-9.7.md NEW 356 + ledger this section + header bump ~62 LOC) | 0 net new test cases this commit (gate doc + ledger close only); main-gate measurement performed in this commit's preparation, not in the diff | ADR-0011 (no new Protocol -- gate doc cites zero net Protocol delta from P9.7); ADR-0012 (no shim under v1 -- 308 redirect shims live under api/routes/_orgs_v2_legacy_redirects); ADR-0013 (perf_counter SLA NOT exercised in P9.7; banked for P-RC-10); ADR-0014 (v2 src LOC 2 871 over ~1 910 target; surplus in shim+schema+Group A layers; per-endpoint 19.2 LOC under 25 LOC REJECT; no ADR-0015 filed); cites P-RC-9-P9.7-CHARTER.md sections 9 (gate criteria) + 12 (HARD STOP) |



## P9.7.nit-a -- canary fixture regression closure (NIT-G4 from G-RC-9.7 audit)

> Post-gate hot-fix. G-RC-9.7 auditor's NIT-G4 root-caused the
> ``test_v2_im_canary_e2e`` failure to P9.7Î±-2a (``31332276``)
> renaming the Group A prefix without updating this fixture.
> Fix (Auditor Option A): also mount
> ``_orgs_v2_legacy_redirects.router`` in ``v2_client``; TestClient
> 308-follows ``/api/v2/orgs/*`` -> ``/api/v2/orgs-spec/*``.
> Canary 3/3 isolated; narrow slice 581 -> 582 passed.

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| _this commit_ | P-RC-9 P9.7.nit-a | fix(tests/integration): P9.7.nit-a restore v2_im_canary_e2e by mounting legacy redirects router | +PLACEHOLDER LOC (fixture +9 / ledger this section) | +1 narrow-slice green (canary regression closed) | ADR-0011 (no new Protocol); ADR-0012 (the 308 shim exercised exactly as intended for v2.0.x); cites G-RC-9.7 audit NIT-G4 |


## P9.7.nit-b -- G-RC-9.7 audit cleanup (NIT-G1/G2/G3/G4-followup/G6)

> Post-gate doc cleanup. Closes 5 G-RC-9.7 auditor NITs in
> ``docs/revamp/gates/G-RC-9.7.md`` directly: G1 (§1 commit count
> 18 -> 17), G2 (§2 LOC table missing ``schemas/__init__.py``
> +174 row; TOTAL corrected 2 871 -> 2 845), G3 (§11 NIT roster
> expanded to 12 rows incl. M-1..M-4 + the four audit findings),
> G4-followup (§3.1 / 3.2 retraction of the ``known flaky / none
> reference v2 orgs / folded into G-RC-9.6`` claims, with the
> deterministic-regression disclosure pointing at P9.7.nit-a
> ``652c8a71``), and G6 (disclose ``test_agent_calls_web_search``
> self-recovery between G-RC-9.6 and G-RC-9.7).

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| _this commit_ | P-RC-9 P9.7.nit-b | docs(revamp): P9.7.nit-b clean up G-RC-9.7 audit NIT-G1/G2/G3/G4-followup/G6 | +PLACEHOLDER LOC (gate doc edits + ledger this section) | 0 (docs only) | ADR-0011 (no new Protocol; gate doc edit only); ADR-0012 (the 308 shim closure of NIT-G4 in P9.7.nit-a is the only one-window exercise) |

## P9.7 CLOSED -- phase summary

P9.7 v2 REST endpoint mint **CLOSED** after 17 implementation
commits + this gate commit (G-RC-9.7 = P9.7gamma-3b; 18 commits
total since charter HEAD ``89703a28``).

| metric | value |
|---|--:|
| commits (P9.7 charter -> G-RC-9.7) | 18 |
| v2 endpoints minted (B1-B83) | 83 |
| Group A endpoints relocated (/orgs-spec/*) | 9 |
| 308 redirect shims (legacy /orgs paths) | 9 |
| Pydantic shapes added (schemas/orgs_v2/) | 16 |
| contract cases added (tests/api/contracts/) | 184 |
| REST contract sentinel cases | 3 |
| Total NEW test cases (contracts + sentinel) | 187 |
| v2 src LOC (orgs_v2*.py + schemas/orgs_v2/) | ~2 871 |
| narrowed slice delta (api+runtime/orgs+parity/orgs) | 394 -> 581 (+187) |
| narrowed wider slice (G-RC-9.6 format) | 1 457 -> 1 772 (+315) |
| full main gate | 6 538 -> 6 853 (+315; pre-existing failures unchanged) |
| P-RC-9 sentinels (6 parity + 1 REST contract) | 7 / 7 ACTIVE |
| Strict additive (orgs/+core/+channels/+orgs.py+apps/) | empty bytes ✓ |
| NITs folded in at gate window | 1 (NIT-A schemas merge in γ-3a) |
| NITs riding to G-RC-9 final | 2 (NIT B-1 burst-test from G-RC-9.4; NIT P9.7-B contract file <= 350 soft cap) |
| ACCEPTANCE.md | unchanged (#5 closes in G-RC-9 final after P9.8 + P9.9) |

**P-RC-9 phase status after P9.7 close**: 6 / 6 ADR-0011
subsystems implemented + parity-validated; v2 REST mint
complete; 7 / 7 sentinels active. Wiring (P9.8) + deletion
(P9.9) remain; P9.10 ships G-RC-9 final + ACCEPTANCE.md
upgrades + ``v2.0.0-rc3`` tag.

**Next**: P9.8 caller migration (frontend + IM gateway adapter
swap from ``/api/orgs/`` -> ``/api/v2/orgs/``); ~86 src + ~216
test import sites per the charter section 10 estimate.
Different blast radius from P9.7 (touches ``apps/`` +
``src/openakita/channels/``, which P9.7's strict-additive
sentinel held off-limits), so it needs its own planning round.

## P9.8 -- Caller migration (planning round)

> Planning charter for P9.8 -- the eighth phase of P-RC-9.
> P9.8 scope **redefined** to HTTP callers only (frontend
> ``apps/setup-center/src/`` 60 v1 + 17 v2 path hits;
> ``src/openakita/channels/`` 0 HTTP callers confirmed);
> Python ``from openakita.orgs`` import sweep (90 src + 228
> test sites) deferred to P9.9 alongside v1 source deletion.
> See ``docs/revamp/P-RC-9-P9.8-CHARTER.md`` section 1.2.
> ADR-0015 NOT filed; 8th sentinel (frontend stale-path
> grep) recommended for P9.8delta-1.

| commit hash | phase | title | LOC delta | tests delta | ADR refs |
|---|---|---|---|---|---|
| _this commit_ | P-RC-9 P9.8.charter | docs(revamp): P9.8 caller migration charter (planning round) | +PLACEHOLDER LOC (P-RC-9-P9.8-CHARTER.md NEW ~483 + ledger this section ~15) | 0 (planning only; no source/test edits) | ADR-0011 (no new Protocol; section 7 8th sentinel is a grep, not an abstraction); ADR-0012 (v1 deletion deferred to P9.9; 308 shim retirement per charter section 8); cites ADR-0014 LOC discipline (~750 LOC budget vs P9.7's 2 845, well inside tolerance) |
| _this commit_ | P-RC-9 P9.8alpha-1 | docs(revamp): P9.8alpha-1 caller inventory (60 v1 + 17 v2 + 5 channels) | +PLACEHOLDER LOC (P-RC-9-P9.8-CALLER-INVENTORY.md NEW ~327 + ledger this row) | 0 (docs only; no source/test edits) | ADR-0011 (no new Protocol; inventory is a docs artefact, not an abstraction); ADR-0012 (no shim semantics changed; 308 retirement still scheduled for v2.1.0 per charter sec 8) |
| _this commit_ | P-RC-9 P9.8gamma-1 | feat(frontend): P9.8gamma-1 swap Group A API calls from /api/v2/orgs to /api/v2/orgs-spec | +PLACEHOLDER LOC (api/orgs.ts 16 swap lines + api/v2Stream.ts 2 swap lines + api/__tests__/v2Stream.test.ts 1 swap line = 19 frontend swap LOC; ledger this row + body ~22 LOC) | 0 (no new tests; existing v2Stream.test.ts mock URL string updated in place) | ADR-0011 (no new Protocol; pure URL string swap on frontend; no abstraction introduced); ADR-0012 (308 shim ``_orgs_v2_legacy_redirects.py`` continues to serve any legacy callers through v2.0.x per Q-B single-window contract); charter D-1 R3 LOCKED (Group A canonical literal lives at ``/api/v2/orgs-spec``; v2.1.0 shim retirement is a no-op once frontend literals are direct) |

> P9.8alpha-1 caller inventory landed. Measured frontend
> ``apps/setup-center/src/``: **62** ``/api/orgs/`` hits
> across 12 files (54 HTTP + 4 TS module imports + 4
> comments; **+2** vs charter sec R5 "60" anchor from CJK
> pipe noise -- functional content matches); **17**
> ``/api/v2/orgs/`` hits across 6 files (9 functional Group
> A constructions + 8 docstrings / test mocks).
> ``src/openakita/channels/`` confirmed **0** HTTP callers
> (5 in-process ``from openakita.orgs.command_service``
> imports remain P9.9 scope). Tests: 56 v1 hits in
> ``tests/orgs/*`` (delete-with-v1 in P9.9; no P9.8 action);
> 463 v2 hits across ``tests/api/``, ``tests/parity/``,
> ``tests/integration/`` (no action). ``docs/`` 115 hits
> (out of scope). ``scripts/`` 0 hits. Proposed gamma
> boundary: gamma-1 ~80 LOC (api/orgs.ts + api/v2Stream.ts
> + test mock; Group A ``orgs`` -> ``orgs-spec`` retarget),
> gamma-2 ~120 LOC (OrgEditorView.tsx; 17 B swaps + 3 C
> leftovers + 1 PUT->PATCH), gamma-3 ~110 LOC
> (OrgProjectBoard + OrgChatPanel cluster), gamma-4 ~90 LOC
> (7 remaining files). gamma total ~400 LOC (+14% vs
> charter sec 6 250-350 reserve; inside ADR-0014 tolerance).
> Strict additive verified: ``git diff 95b9f9b6..HEAD --
> src/openakita/ tests/ apps/`` returns empty bytes.
> P9.8gamma-1 NOT started -- HARD STOP per charter sec 13.

> P9.8gamma-1 first ``apps/`` source touch landed. Swapped Group A
> frontend API call sites in 3 files: ``api/orgs.ts`` 16 swaps
> (8 JSDoc literals at lines 11-18 + 8 ``apiUrl(...)`` segment lists
> for listTemplates / getTemplate / instantiateTemplate / listOrgs /
> createOrg / getOrg / patchOrg / deleteOrg); ``api/v2Stream.ts`` 2
> swaps (1 JSDoc + 1 SSE URL builder); ``api/__tests__/v2Stream.test.ts``
> 1 swap (mock URL expectation). Total 19 line-pair swaps with
> 0 net LOC delta (19 deletions + 19 insertions); well under the
> 100 LOC cap. Canonical Group A literal now lives at
> ``/api/v2/orgs-spec/*`` per charter D-1 R3 LOCKED; the 308 shim
> (``api/routes/_orgs_v2_legacy_redirects.py``) continues to serve
> any straggling legacy callers through v2.0.x per ADR-0012 Q-B
> single-window contract (shim retires in v2.1.0 as a no-op now
> that canonical literals are direct). Verification: TypeScript
> ``tsc -b`` clean (exit 0); frontend ``vitest run`` 14 passed
> across 5 suites including the touched ``v2Stream.test.ts``;
> backend ``tests/integration/test_v2_im_canary_e2e.py`` 1 passed
> (the canary intentionally hits the legacy
> ``/api/v2/orgs/templates/{id}/instantiate`` path so this asserts
> the 308 shim is still doing its job); narrowed slice
> ``tests/api/ + tests/runtime/orgs/ + tests/parity/orgs/`` 581
> passed (= baseline at HEAD ``35f7ad9c``; no regression). 7 / 7
> P-RC-9 sentinels remain ACTIVE (6 parity slices + 1 REST contract
> sentinel; the REST contract sentinel asserts the OpenAPI route
> inventory is unchanged -- backend untouched so it stays green).
> Strict additive verified: ``git diff 35f7ad9c..HEAD --
> src/openakita/`` returns empty bytes. P9.8gamma-2
> (``views/OrgEditorView.tsx``, 20 hits, ~120 LOC) NOT started --
> HARD STOP per charter sec 3 + sec 13 (different blast radius:
> views/ cluster vs api/ cluster).

| _this commit_ | P-RC-9 P9.8gamma-2 | feat(frontend): P9.8gamma-2 swap OrgEditorView v1->v2 mint API paths | +PLACEHOLDER LOC (views/OrgEditorView.tsx 16 swap lines + ledger this row + body ~22 LOC) | 0 (no new tests; touched suites stay green: tsc -b clean, vitest 14/14 across 5 suites, canary 1/1, narrowed slice 581/581, REST contract sentinel 3/3) | ADR-0011 (no new Protocol; pure URL string swap on frontend; no abstraction introduced); ADR-0012 (308 shim ``_orgs_v2_legacy_redirects.py`` continues to serve any legacy callers through v2.0.x per Q-B single-window contract); charter D-1 R3 LOCKED (mint canonical literal lives at ``/api/v2/orgs/*``, distinct from Group A ``/api/v2/orgs-spec/*``); inventory sec 1.2 (3 Group C paths -- ``/reset``, ``/heartbeat/trigger``, ``/standup/trigger`` -- have no v2 mint equivalent and remain on v1 pending P9.9 v1 deletion) |

> P9.8gamma-2 second ``apps/`` source touch landed. Swapped mint-semantic
> frontend API call sites in ``views/OrgEditorView.tsx`` (the single
> largest caller file, 5347 LOC; 19 HTTP sites + 1 TS module import).
> 16 HTTP literals migrated ``/api/orgs/*`` -> ``/api/v2/orgs/*`` covering
> list / get / create / update / delete / from-template / templates list /
> import / export (twice; mid-component duplicate retained verbatim) /
> start / stop / stats / node unfreeze / avatar upload / prompt-preview
> (B1 / B5 / B7 / B8 / B9 / B10 / B11 / B12 / B17 / B27 / B32 / B34 / B35 /
> B64 per ``P-RC-9-P9.7-ENDPOINT-INVENTORY.md`` sec 3). Line 73 TS module
> import (``from "../api/orgs"``) is a relative module path, not an HTTP
> URL -- left untouched (gamma-1 already retargeted that module's
> internal Group A literals to ``/api/v2/orgs-spec/*``). Three Group C
> paths held on v1 per inventory sec 1.2 (no v2 mint equivalent;
> deprecation candidates folded into the P9.9 v1 deletion sweep): line
> 1148 ``/reset`` (legacy OrgManager.reset_org); line 5343
> ``/heartbeat/trigger`` and line 5346 ``/standup/trigger`` (debug-only
> manual triggers). The B11 update endpoint at ``/api/v2/orgs/{org_id}``
> remains ``PUT`` (orgs_v2_runtime_orgs.py:218); verb stays as-is per the
> "pure URL string swap" charter contract for this turn -- inventory's
> earlier R2 PUT->PATCH proposal is moot because the mint endpoint
> already accepts PUT (the prior charter assumed PATCH from a stale
> Pydantic snapshot). Diff: 16 insertions + 16 deletions, net 0 LOC delta
> in the source file. Verification: ``tsc -b`` exit 0 (no type drift);
> ``vitest run`` 14 passed / 5 suites (no new test files; existing
> coverage exercises the URL strings indirectly via mocked safeFetch);
> ``pytest tests/integration/test_v2_im_canary_e2e.py`` 1 passed (canary
> still rides 308 shim, asserting Q-B single-window survival); narrowed
> backend slice ``tests/api/ + tests/runtime/orgs/ + tests/parity/orgs/``
> 581 passed (= baseline at HEAD ``754ff465``; no regression);
> ``tests/parity/orgs/test_rest_contract_sentinel.py`` 3 passed (OpenAPI
> route inventory unchanged -- frontend-only touch, no schema delta).
> Strict additive verified: ``git diff 754ff465..HEAD -- src/openakita/``
> returns empty bytes; only ``apps/`` and ``docs/revamp/PROGRESS_LEDGER_P9.md``
> moved this commit. 7 / 7 P-RC-9 sentinels remain ACTIVE. P9.8gamma-3
> (``components/OrgProjectBoard.tsx`` + ``components/OrgChatPanel.tsx``,
> 20 hits, ~110 LOC) follows in the same turn per the gamma boundary
> proposal in inventory sec 9.

| _this commit_ | P-RC-9 P9.8gamma-3 | feat(frontend): P9.8gamma-3 swap OrgProjectBoard + OrgChatPanel v1->v2 mint API paths | +PLACEHOLDER LOC (components/OrgProjectBoard.tsx 11 swap lines + components/OrgChatPanel.tsx 6 HTTP swap lines + 3 comment-line text updates + ledger this row + body ~24 LOC) | 0 (no new tests; tsc -b clean, vitest 14/14 across 5 suites, canary 1/1, narrowed slice 581/581, REST contract sentinel 3/3) | ADR-0011 (no new Protocol; pure URL string swap + 3 narrative comment updates on frontend; no abstraction introduced); ADR-0012 (308 shim ``_orgs_v2_legacy_redirects.py`` continues to serve any legacy callers through v2.0.x per Q-B single-window contract); charter D-1 R3 LOCKED (mint canonical literal lives at ``/api/v2/orgs/*``); inventory sec 1.3 (all 11 OrgProjectBoard hits Group B, including B68-B83 ProjectStore + dispatch / cancel) + sec 1.4 (OrgChatPanel: 6 HTTP B + 3 comment updates) |

> P9.8gamma-3 third ``apps/`` source touch landed. Swapped mint-semantic
> frontend API call sites in two component-cluster files:
> ``components/OrgProjectBoard.tsx`` and ``components/OrgChatPanel.tsx``
> (the project board + chat panel cluster per inventory sec 9).
> OrgProjectBoard: 11 HTTP literals migrated covering the full
> ProjectStore + dispatch / cancel surface -- ``get_task`` (B79),
> ``get_task_timeline`` (B81), ``list_projects`` (B68),
> ``update_project`` (B71), ``create_project`` (B69), ``create_task``
> (B73), ``delete_project`` (B72), ``update_task`` (B74),
> ``delete_task`` (B75), ``dispatch_task`` (B76), ``cancel_dispatched_task``
> (B77). All 11 are template-literal Group B swaps with no verb or
> body change. OrgChatPanel: 6 HTTP literals migrated for
> ``activity_view`` (B46; lines 492 + 582), ``get_command`` (B39;
> lines 685 + 1252), ``cancel`` (B40; line 792), and ``submit``
> (B38; line 1203). Additionally, 3 narrative comment lines updated
> in OrgChatPanel for ledger / diagram coherence -- line 105 JSDoc
> referring to legacy WS-vs-HTTP context, line 159 inline CJK comment
> documenting the activity timeline renderer, and line 479 CJK
> comment describing the org-view merge logic. Net file deltas: 11+11
> (OrgProjectBoard) + 9+9 (OrgChatPanel) = 40 lines moved, 0 LOC net.
> Verification: ``tsc -b`` exit 0 (no type drift; 6 + 11 = 17 HTTP
> path strings all flow through the same mocked ``safeFetch`` so type
> shape is unchanged); ``vitest run`` 14 passed / 5 suites; ``pytest
> tests/integration/test_v2_im_canary_e2e.py
> tests/parity/orgs/test_rest_contract_sentinel.py -q`` 4 passed;
> narrowed backend slice ``tests/api/ + tests/runtime/orgs/ +
> tests/parity/orgs/`` 581 passed (= baseline at HEAD ``5708cce5``;
> = baseline at HEAD ``754ff465``; no regression). Strict additive
> verified: ``git diff 754ff465..HEAD -- src/openakita/`` returns
> empty bytes; only ``apps/`` and ``docs/revamp/PROGRESS_LEDGER_P9.md``
> moved this commit. 7 / 7 P-RC-9 sentinels remain ACTIVE.
> P9.8gamma-4 (7 remaining view / component files; 18 HTTP + 1 comment
> = 19 hits; ~90 LOC) follows in the same turn per inventory sec 9.

| _this commit_ | P-RC-9 P9.8gamma-4 | feat(frontend): P9.8gamma-4 swap remaining 7 view/component files v1->v2 mint API paths | +PLACEHOLDER LOC (7 frontend files; 18 HTTP swap lines + 1 JSDoc comment swap; ledger this row + body ~26 LOC) | 0 (no new tests; tsc -b clean, vitest 14/14 across 5 suites, canary 1/1, narrowed slice 581/581, REST contract sentinel 3/3) | ADR-0011 (no new Protocol; pure URL string swap + 1 JSDoc comment update; no abstraction introduced); ADR-0012 (308 shim continues to serve any legacy callers through v2.0.x per Q-B single-window contract); charter D-1 R3 LOCKED (mint canonical literal lives at ``/api/v2/orgs/*``); inventory sec 1.4 (long-tail per-file breakdown) |

> P9.8gamma-4 fourth and final ``apps/`` source touch landed.
> Swapped mint-semantic frontend API call sites across 7 long-tail
> view / component files: ``components/OrgMonitorPanel.tsx`` 5
> swaps (events B45, schedules B18, thinking B31, tasks B82,
> active-plan B83); ``components/OrgInboxSidebar.tsx`` 4 swaps
> (inbox list B54, read B55, read-all B56, resolve B57);
> ``components/OrgBlackboardPanel.tsx`` 2 swaps (memory query B42,
> memory delete B44); ``components/OrgDashboard.tsx`` 1 swap
> (stats B64); ``components/WorkbenchNodePicker.tsx`` 1 HTTP swap
> (plugin-workbench-templates B6) + 1 JSDoc comment narrative
> update; ``views/ChatView.tsx`` 2 swaps (list orgs B1, command
> cancel B40); ``views/PixelOfficeView.tsx`` 3 swaps (list orgs B1,
> get org B10, submit command B38). Total: 18 HTTP literal swaps +
> 1 JSDoc comment update = 19 hits matching inventory sec 1.4
> per-file roll-up. All Group B (mint-semantic); no Group C
> leftovers in any of the 7 files; no verb changes. Net source
> delta: 19 insertions + 19 deletions = 38 lines moved, 0 LOC net.
> Verification: ``tsc -b`` exit 0; ``vitest run`` 14 passed / 5
> suites; ``pytest tests/integration/test_v2_im_canary_e2e.py
> tests/parity/orgs/test_rest_contract_sentinel.py -q`` 4 passed;
> narrowed backend slice 581 passed (= baseline at HEAD ``754ff465``
> / ``5708cce5`` / ``591e8f94``; no regression).
>
> Cumulative gamma sweep (gamma-2 + gamma-3 + gamma-4): 53 HTTP
> literal swaps + 4 narrative comment swaps = 57 v1 hits migrated
> across 10 source files (1 + 2 + 7); 3 Group C paths held on v1
> in OrgEditorView per inventory sec 1.2 (reset / heartbeat-trigger
> / standup-trigger). v1 hit accounting: charter sec R5 anchored
> 60 v1 hits, inventory measured 62 (54 HTTP + 4 TS imports +
> 4 comments); this turn swapped 53 HTTP (= 54 v2-eligible minus
> the 1 OrgChatPanel line at 105 which is JSDoc not HTTP -- counted
> in the 4 comments) and 4 comments (3 in OrgChatPanel + 1 in
> WorkbenchNodePicker); the 4 TS module imports (3 in
> TemplatePickerDrawer tests + 1 in OrgEditorView line 73) are not
> HTTP paths and stay verbatim; the 3 Group C HTTP paths stay
> on v1 for P9.9 deletion. Strict additive verified across all
> three commits: ``git diff 754ff465..HEAD -- src/openakita/``
> returns empty bytes. 7 / 7 P-RC-9 sentinels remain ACTIVE.
> P9.8delta-1 (8th sentinel: frontend stale-path grep) NOT
> started -- HARD STOP per charter sec 3 + sec 13. delta phase
> is the final gate-prep phase and needs its own scoped
> delegation.

| _this commit_ | P-RC-9 P9.8delta-1 | test(parity/orgs): P9.8delta-1 8th sentinel -- frontend stale v1 path scan | +PLACEHOLDER LOC (tests/parity/orgs/test_frontend_stale_paths_sentinel.py NEW ~209 LOC + ledger this row + body ~24 LOC) | +3 tests (active assertions, no xfail; 8th P-RC-9 sentinel slot now ACTIVE alongside the 6 parity sentinels + REST contract sentinel; 3/3 passing locally with stability across 4 reruns) | ADR-0011 (no new Protocol; sentinel is a collection-time regex grep, same pattern as the 7th REST contract sentinel; not architecturally novel); ADR-0012 (sentinel's Group C allowlist explicitly references the 3 deprecated debug-only v1 paths slated for P9.9 deletion alongside the v1 router); charter sec 7 (8th sentinel decision: ADOPT in P9.8delta-1); charter sec 9 gate criterion 1 (zero v1 literal count outside allowlist); inventory sec 1.2 (Group C source) + sec 4.3 (TS-module-import discriminator list) |

> P9.8delta-1 first ``tests/`` source touch of the delta phase landed.
> Added ``tests/parity/orgs/test_frontend_stale_paths_sentinel.py``
> (209 LOC after ruff format) -- the 8th P-RC-9 sentinel. Active
> (non-xfail) collection-time grep over ``apps/setup-center/src/``
> ``*.ts`` + ``*.tsx`` files asserting that the P9.8 caller migration
> has fully rewired the frontend off ``/api/orgs/...`` HTTP literals
> and onto ``/api/v2/orgs/...`` (P9.7 mint) or
> ``/api/v2/orgs-spec/...`` (P9.7a-2a Group A relocation) surfaces.
>
> Three test functions, all active assertions (no ``@pytest.mark.xfail``):
> (1) ``test_no_stale_v1_http_paths_outside_allowlist`` -- scans
> the frontend tree with negative-lookbehind regex
> ``(?<!\.)/api/orgs`` (excludes TS module specifiers like
> ``../api/orgs`` which are preceded by ``.``); subtracts the 3
> Group C allowlist entries; asserts the remaining set is empty.
> (2) ``test_group_c_allowlist_paths_still_present`` -- verifies the
> 3 allowlisted v1 paths still exist in OrgEditorView.tsx at (or
> near) their recorded lines; fails loud when P9.9 deletes the v1
> router so the maintainer also strips the allowlist.
> (3) ``test_module_imports_use_relative_path`` -- discriminator
> self-test: confirms the 4 known TS module imports
> (``from "../api/orgs"`` / ``from "../../api/orgs"`` /
> ``vi.mock("../../api/orgs", ...)`` / ``import * as orgsApi from "../../api/orgs"``)
> stay in relative-path form AND do NOT match the sentinel regex.
>
> ``GROUP_C_ALLOWLIST`` verbatim: (1) line 1148
> ``/api/orgs/${currentOrg.id}/reset`` -- OrgManager.reset_org (deprecated);
> (2) line 5343 ``/api/orgs/${currentOrg.id}/heartbeat/trigger`` (debug-only);
> (3) line 5346 ``/api/orgs/${currentOrg.id}/standup/trigger`` (debug-only).
> All 3 ride to P9.9 deletion alongside ``src/openakita/api/routes/orgs.py``.
>
> Verification: ``pytest tests/parity/orgs/test_frontend_stale_paths_sentinel.py
> -q`` -> 3 passed in 1.91 s, repeated 4x for stability (3/3 each
> run); ``pytest tests/parity/orgs/ -q`` -> 66 passed in 6.03 s
> (= 63 baseline at HEAD ``fbed86ac`` + 3 new from 8th sentinel; 7
> pre-existing sentinels still green; zero xfail across the whole
> parity/orgs/ package). ``ruff check`` + ``ruff format --check``
> clean. Strict additive verified: ``git diff fbed86ac..HEAD --
> src/openakita/ apps/setup-center/src/`` returns empty bytes; only
> ``tests/`` and ``docs/revamp/PROGRESS_LEDGER_P9.md`` moved this
> commit. **8 / 8 P-RC-9 sentinels now ACTIVE**.
>
> P9.8delta-2 (G-RC-9.8 mini-gate + P9.8 ledger close section)
> follows next in this same delta phase per charter sec 3.

| _this commit_ | P-RC-9 P9.8delta-2 | docs(revamp): P9.8delta-2 G-RC-9.8 mini-gate + P9.8 ledger close | +PLACEHOLDER LOC (docs/revamp/gates/G-RC-9.8.md NEW ~410 LOC + ledger close summary section + ledger this row + body ~30 LOC) | 0 (gate doc + ledger; no test churn this commit; main gate measured 6 858 passed / 12 failed / 116 skipped / 5 xfailed in 1 063.36 s for §3.1; narrow slice 585 passed in 77.26 s; parity/orgs 66 passed in 6.49 s) | ADR-0011 (no new Protocol; gate doc is process artefact, not abstraction); ADR-0012 (gate asserts v1 subsystem + v1 router byte-level untouched across all 8 P9.8 commits per §10 piece 3); ADR-0014 (LOC budget: auditor-measured 1030 LOC total = gamma 301 + delta-1 256 + delta-2 473; +8.4% drift vs 950 upper bound, still within ADR-0014 +/-10% planning tolerance; corrected by P9.8.nit-a errata); **ADR-0015 NOT filed this round** (charter sec 13 deferred; P9.9 charter reassesses for 308 shim retirement governance) |

> P9.8delta-2 gate commit landed; **P9.8 caller migration phase
> CLOSED**. Wrote ``docs/revamp/gates/G-RC-9.8.md`` (~410 LOC
> mirroring G-RC-9.7's 13-section structure) and appended this
> ledger close summary. Gate doc covers: §1 commit table (8
> commits since ``4b8a9ad8``); §2 implementation summary (55 hits
> + 7 residuals); §3 test math (MEASURED full main gate 6 858
> passed vs 6 853 baseline = +5 delta = +3 new sentinel + 2
> self-recoveries; 12 failed all carry-over from G-RC-9.7's "12
> match G-RC-9.6 verbatim" list, zero P9.8-introduced); §4
> frontend swap evidence (4 cluster commits, 10 source files);
> §5 sentinel status (7 + 1 = 8 / 8 ACTIVE); §6 reference matrix
> (NIT-E-1 all 12 inputs rejected, 0 net adoption); §7
> architecture decisions (ADR-0011 / 0012 / 0013 / 0014 invariants
> held; ADR-0015 NOT filed); §8 NIT fold-in (0 new NITs); §9
> Protocol audit (0 new Protocols; total 13 unchanged); §10
> sentinel three-piece (7 residuals allowlisted + 8th sentinel
> ACTIVE + ``git diff src/openakita/`` empty bytes); §11 NIT
> fold-in roster (6 closed + 6 ride to G-RC-9 final); §12 HARD
> STOP (P9.9 NOT started); §13 P-RC-9 completion panorama (4
> layers complete + 8 / 8 sentinels active; only P9.9 deletion
> remains).
>
> Cumulative P9.8 stats: **8 commits** (charter ``95b9f9b6`` +
> alpha-1 ``35f7ad9c`` + gamma-1 ``754ff465`` + gamma-2
> ``5708cce5`` + gamma-3 ``591e8f94`` + gamma-4 ``fbed86ac`` +
> delta-1 ``a31c679f`` + delta-2 _this commit_); **55 frontend
> hits swapped** (51 HTTP literal swaps + 4 narrative comment
> updates across 10 source files); **7 legitimate residuals
> preserved** with rationale (4 TS module imports
> ``../api/orgs`` / ``../../api/orgs`` -- relative-path
> specifiers, not HTTP URLs; 3 Group C HTTP paths in
> OrgEditorView.tsx -- ``/reset`` ``/heartbeat/trigger``
> ``/standup/trigger`` deprecated debug-only endpoints scheduled
> for P9.9 deletion alongside the v1 router); **strict-additive
> backend boundary held absolutely** (``git diff
> 4b8a9ad8..HEAD -- src/openakita/`` returns empty bytes across
> all 8 commits); **8 / 8 P-RC-9 sentinels ACTIVE** (6 parity
> P9.1c-P9.6gamma + 1 REST contract P9.7gamma-2 + 1 frontend
> stale-path P9.8delta-1). Auditor-measured LOC delta **1030**
> (gamma 70+72+76+83 = 301 + delta-1 256 + delta-2 473 [= 410 gate
> body + 63 ledger close]) vs charter sec 6 ~700-950 upper bound
> = **+8.4% drift**, still within **ADR-0014 +/-10% planning
> tolerance** (950 x 1.10 = 1045; 1030 < 1045); **ADR-0015 not
> triggered** (P9.8 is mechanical literal swap, not a new
> architectural decision per charter sec 13). NIT-Y2 (G-RC-9.8
> audit cosmetic) was closed by P9.8.nit-a. Planning paperwork
> (charter 499 + alpha-1 342) booked separately at their own
> commits.
>
> **P-RC-9 phase status**: 6 / 6 ADR-0011 subsystem rewrites
> complete + parity-validated; 83 / 83 v2 REST mint endpoints +
> 9 Group A relocated + 9 308 shims; 55 / 55 frontend caller
> hits swapped; 7 / 7 residuals allowlisted; 8 / 8 sentinels
> ACTIVE. **Only P9.9 physical deletion remains** -- ``git rm -r
> src/openakita/orgs/`` (~18 000 LOC, 6 subsystems) + v1 router
> -> 410-Gone or hard ``git rm`` (~2 533 LOC, 89 endpoints) +
> optional 308 shim retirement at v2.1.0 per Q-B + ``tests/orgs/``
> deletion (~13 000 LOC) + ~90 src + ~228 test
> ``from openakita.orgs.X`` import rewrite sites. P9.9 charter
> needs ADR-0015 (308 shim retirement governance) per Q-B
> ACCEPTED (b) single-window discipline. G-RC-9 final gate signs
> off after P9.9 PASS. **HARD STOP per brief**: P9.9 NOT started
> this turn; opens in the next agent run on operator signal.

## P9.8.nit-a -- G-RC-9.8 audit cosmetic cleanup (NIT-Y1 + NIT-Y2)

| _this commit_ | P-RC-9 P9.8.nit-a | docs(revamp): P9.8.nit-a clean up G-RC-9.8 audit NIT-Y1 + NIT-Y2 (cosmetic doc errata) | +PLACEHOLDER LOC (docs/revamp/gates/G-RC-9.8.md sec 2 + sec 7 errata; docs/revamp/PROGRESS_LEDGER_P9.md delta-2 row + close section LOC summary + this nit-a section; <= 80 LOC narrow doc edits) | 0 (docs only; no source/test edits; ``git diff 99606d6c..HEAD -- src/openakita/ apps/ tests/`` empty bytes; 8 / 8 sentinels still ACTIVE -- ``pytest tests/parity/orgs/ -q`` 66 passed) | ADR-0011 (no new Protocol; cosmetic doc errata, not abstraction); ADR-0012 (v1 subsystem + v1 router + 308 shim BYTE-LEVEL UNTOUCHED); ADR-0014 (auditor-measured 1030 LOC total = +8.4% drift inside +/-10% planning tolerance; corrects sec 2 + sec 7 + ledger close section's earlier ~993 / ~983 / +3% miscount); **ADR-0015 not filed** (P9.8 is mechanical literal swap, not a new architectural decision per charter sec 13; reassessed in P9.9 charter for 308 shim retirement governance per Q-B ACCEPTED (b)) |

> P9.8.nit-a docs-only commit lands G-RC-9.8 audit follow-up.
> NIT-Y1 (sec 2 PUT->PATCH narrative mismatch with code): replaced
> the "one verb change" sentence with honest disclosure that all
> 55 mint-side swaps are path-only and the inventory sec 7.2 R2
> PUT->PATCH proposal was withdrawn during gamma-2 because the
> backend mint at ``orgs_v2_runtime_orgs.py:218`` already accepts
> PUT. Verification: HEAD ``apps/setup-center/src/views/
> OrgEditorView.tsx`` line 1239-1243 shows ``method: "PUT"`` for
> ``/api/v2/orgs/${currentOrg.id}`` (verb stayed PUT). Backend
> verification: ``src/openakita/api/routes/orgs_v2_runtime_orgs.py``
> line 218 ``@router.put("/{org_id}", summary="B11 update
> organization")`` (PUT accepted at runtime). gamma-2 ledger row
> body (this file lines 732-737) self-discloses the withdrawal
> with the same wording: "earlier R2 PUT->PATCH proposal is moot
> because the mint endpoint already accepts PUT". NIT-Y2 (sec 2 +
> sec 7 + close section LOC drift miscount): replaced executor's
> ~993 / ~983 / +~3% / +3% with auditor-measured **1030 LOC**
> (gamma 70+72+76+83 = 301 per ``git diff --shortstat`` per
> commit + delta-1 256 + delta-2 473 [= 410 gate body + 63 ledger
> close section]) = **+8.4% drift** vs charter sec 6 950 upper
> bound, still within ADR-0014 +/-10% planning tolerance
> (950 x 1.10 = 1045; 1030 < 1045); **ADR-0015 not triggered**
> (P9.8 is mechanical literal swap per charter sec 13). delta-2
> erratum: 473 measured vs ~386 estimated. References G-RC-9.8
> audit report's PASS-WITH-NITS verdict (NIT-Y3 BOM is
> pre-existing, defers to P-RC-10). Strict-additive boundary
> verified: ``git diff 99606d6c..HEAD -- src/openakita/ apps/
> tests/`` returns empty bytes (docs-only commit). 8 / 8 P-RC-9
> sentinels remain ACTIVE: ``pytest tests/parity/orgs/ -q`` 66
> passed. Existing G-RC-9 final NIT roster (B-1, M-1 / M-2 / M-3
> / M-4, P9.7-B) untouched -- they continue to ride. **HARD STOP**
> per brief: P9.9 charter NOT started (last charter of P-RC-9;
> ~31 000 LOC net deletion needs its own scoped delegation).

## P9.9.adr -- ADR-0015 308 shim retirement governance (planning ratification)

| _this commit_ | P-RC-9 P9.9.adr | docs(adr): ADR-0015 308 shim retirement governance (option b -- v2.1.0 retirement; P9.9 NO-OP) | +PLACEHOLDER LOC (docs/adr/0015-308-shim-retirement-governance.md NEW 198 LOC mirroring ADR-0014 6-section layout; ledger this section + row ~6 LOC; Q_DECISIONS.md Q-B governance ratification note ~5 LOC; total 239 LOC <= 250 cap; target ~170) | 0 (docs-only commit; no source/test/apps edits; ``git diff d1bec779..HEAD -- src/openakita/ tests/ apps/`` returns empty bytes; 8 / 8 P-RC-9 sentinels unchanged) | ADR-0011 (no new Protocol; ADR is governance ratification, not abstraction); ADR-0012 (symmetric v2-side counterpart: ADR-0012 governs v1 deletion while ADR-0015 governs the v2-side compat-layer retirement window); ADR-0014 (mirrors 6-section ADR layout: Title/metadata + Context + Decision + Consequences + Alternatives rejected + Refs); **ADR-0015 FILED** ratifying G-RC-9.7 + G-RC-9.8 audit recommendation = option (b) -- 308 shim NO-OP in P9.9, physical retirement at v2.1.0 per Q-B ACCEPTED (b) 1-release-window discipline |

> P9.9.adr docs-only commit ratifies the auditor recommendation
> from two consecutive mini-gates (G-RC-9.7 sec 13 + G-RC-9.8 sec
> 8 + sec 13) for the 9-route 308 redirect shim
> (``api/routes/_orgs_v2_legacy_redirects.py``; landed at P9.7a-2a
> commit ``31332276``). **Decision: option (b)** -- P9.9 leaves the
> shim byte-level untouched; physical retirement (file delete +
> ``server.py`` mount drop + sentinel #7 OpenAPI snapshot
> regeneration to drop 9 shim entries + sentinel #8 sweep if
> Group C affected) becomes a single concrete v2.1.0 milestone
> task. Option (a) (retire in same window as v1 deletion) was
> rejected because it would compress P9.7 path rename + P9.9 v1
> deletion + 308 retirement into the v2.0 cycle, violating the
> spirit of Q-B's ``1-release HTTP shim`` answer (Q-B explicitly
> defers ``api/routes/orgs.py`` hard-delete to v2.1.0 for the
> same reason). Option (c) (hybrid 410 Gone / 404) was rejected
> because 410 inverts the shim's 308 ``moved permanently, retry
> here`` semantic and contradicts the P9.7a-2a method+body
> preservation rationale. The 9 shim routes (paths under prefix
> ``/api/v2/orgs``): ``GET /templates`` + ``GET
> /templates/{id}`` + ``POST /templates/{id}/instantiate`` + 6
> org CRUD/stream variants -- enumerated in ADR-0015
> Implementation notes. Strict-additive boundary verified: ``git
> diff d1bec779..HEAD -- src/openakita/ tests/ apps/`` returns
> empty bytes (docs-only commit; no source/test/apps edits). 8
> / 8 P-RC-9 sentinels unchanged at HEAD; the shim continues to
> serve any legacy callers through v2.0.x per the
> 1-release-window contract. Q_DECISIONS.md Q-B rationale recap
> annotated with a governance ratification note pointing at
> ADR-0015. **HARD STOP per brief**: P-RC-9-P9.9-CHARTER NOT
> started this turn -- it is the next agent task and will own the
> ~31000 LOC v1 deletion event execution plan; this ADR only
> ratifies the governance question that the charter section 8
> needs as a stable input.

## P9.9.charter -- final P-RC-9 planning round (deletion + import sweep; cites ADR-0015)

| _this commit_ | P-RC-9 P9.9.charter | docs(revamp): P9.9 charter -- final P-RC-9 planning (deletion + import sweep; cites ADR-0015) | +PLACEHOLDER LOC (docs/revamp/P-RC-9-P9.9-CHARTER.md NEW 495 LOC mirroring P9.7 + P9.8 charter sec 1-13 structure; this ledger section + row + blockquote ~5 LOC; total ~500 LOC at 500 hard cap, target ~420) | 0 (docs-only commit; ``git diff d49388bb..HEAD -- src/openakita/ tests/ apps/`` returns empty bytes; 8 / 8 P-RC-9 sentinels unchanged at HEAD; charter recommends 9th "v1 deletion guard" sentinel adoption in eta-1) | ADR-0011 (no new Protocol; charter is planning artefact; 9th sentinel another instance of established collection-time grep pattern); ADR-0012 (operationalises v1 deletion per Q-B ACCEPTED (b); hard ``git rm`` selected over fresh 410 shim because P9.7 v2 mint + 308 shim + P9.8 55-hit frontend swap already gave callers one full v2.0.x window); ADR-0014 (LOC budget: charter sec 6 ~1 590 positive within +/-10% tolerance against ~1 500 anchor; negative ~-35 000 LOC bounded by commit_guard insertions-only check per P9.6gamma precedent); **ADR-0015** (308 shim retirement governance -- option (b) v2.1.0 LOCKED; charter sec 5.6 zeta NO-OP + sec 8 documents 3-step v2.1.0 milestone task list verbatim from ADR-0015 Implementation notes) |

> P9.9.charter docs-only commit lands the final P-RC-9 planning
> round. Charter mirrors P9.7 + P9.8 13-section format; covers
> physical deletion of v1 orgs surface (~-35 000 net LOC: 20 237
> subsystem + 2 533 router + 12 238 tests/orgs), 40 import sweep
> sites + 8 absorbed mappings (sec 3.3 per G-RC-9.6 sec 13), 12
> commits split alpha..eta. 9th sentinel: **ADOPT (Y)** in eta-1.
> **HARD STOP per brief**: P9.9alpha-1 NOT started.

## P9.9α-1 -- import sweep inventory (docs-only)

| _this commit_ | P-RC-9 P9.9α-1 | docs(revamp): P9.9α-1 import sweep inventory (22 external sites + 5 parity transition strategy after FP filter) [P-RC-9 P9.9α-1] | +PLACEHOLDER LOC (``docs/revamp/P-RC-9-P9.9-IMPORT-SWEEP-INVENTORY.md`` NEW 438 LOC mirroring P9.7γ ENDPOINT-INVENTORY + P9.8α CALLER-INVENTORY format; this ledger section + row + blockquote ~10 LOC; total ~448 LOC; target ~400, under 500 hard cap; positive only, deletions 0) | 0 (docs-only commit; ``git diff 1071a8b0..HEAD -- src/openakita/ tests/ apps/`` returns empty bytes; 8 / 8 P-RC-9 sentinels unchanged at HEAD; inventory §7 confirms zero ``__init__.py`` re-exports (R4 risk CLEAR); inventory §6 documents runtime=14 charter estimate -> 1 real STRICT hit (13-file substring false-positive on ``openakita.runtime.orgs.X``)) | ADR-0011 (no new Protocol; per-file:line inventory of v1 import surface for sweep planning; mirrors P9.7γ + P9.8α α-1 inventory pattern); ADR-0012 (β / γ / δ / ε phase assignments operationalise v1 deletion per Q-B ACCEPTED (b); recommends γ reduced to 2 commits not 3 because FP filter shrinks runtime/ scope from 14 -> 1 file); ADR-0015 (308 shim ζ NO-OP confirmed -- STRICT scan returns zero hits in ``_orgs_v2_legacy_redirects.py``; shim continues to serve legacy callers byte-untouched through G-RC-9.9) |

> P9.9α-1 docs-only commit lands the import-sweep inventory.
> STRICT regex ``(?<![\w.])openakita\.orgs(?=[.\s])`` scan at
> HEAD ``1071a8b0`` measured 13 src files (87 sites) + 62 test
> files (227 sites). External-sweep targets (after stripping
> internal trees + self-deleting v1 router): **7 src files / 38
> sites + 15 test files / 32 sites = 22 files / 70 sites**, NOT
> the charter loose-grep 40-file estimate. The ~18-file delta is
> dominated by ``src/openakita/runtime/`` (charter 14 -> real 1;
> remaining 13 are pure substring matches on the v2 dotted path
> ``openakita.runtime.orgs.X``; §6 false-positive forensics in
> inventory). γ reduces to 2 commits (γ-1 api/ 3 files / 7
> sites; γ-2 cross-tree core+runtime 2 files / 2 sites; γ-3 NOT
> scheduled). δ remains 4 commits (δ-1 coverage audit doc; δ-2
> parity/orgs/ 5 + tests/unit/ 8 = 13 files / 25 sites; δ-3
> tests/e2e/ + tests/integration/ 2 files / 7 sites (no
> tests/api/ touch -- 0 STRICT hits there); δ-4 atomic
> ``git rm -r tests/orgs/`` -12 238 LOC). 5 ``tests/parity/orgs/``
> files (sentinels #1-#5) get **Option B** transition in δ-2
> (drop v1 oracle import, convert to v2-only smoke against
> golden-dict baselines, preserves regression net; same shape as
> sentinel #6 ``test_runtime_parity.py`` which is already
> v2-only). R4 ``__init__.py`` re-export audit CLEAR (zero
> hits). **HARD STOP per brief**: P9.9β-1 NOT started this turn
> -- next operator signal opens β-1 (``channels/gateway.py``
> 5-site swap; R3 invariant: β before ε).

## P9.9β-1 -- channels gateway swap (R3 invariant: β before ε)

| _this commit_ | P-RC-9 P9.9β-1 | refactor(channels): P9.9β-1 swap gateway imports v1→v2 runtime (R3 invariant β-before-ε) [P-RC-9 P9.9β-1] | +~12 net LOC (``src/openakita/channels/gateway.py`` 6 ins / 6 del at 5 import sites — 4 single-line + 1 multi-line block split per α-1 §3 absorption table since ``OrgCommandSource`` / ``OrgCommandSurface`` / ``default_scope_for_surface`` were absorbed into ``runtime/orgs/command_models`` in P9.4c parallel to v1 ``models.py`` 4-shard split; this ledger section + row + blockquote ~6 LOC; total ~18 LOC; positive insertions stay far below the 50-LOC cap and the per-commit 350-LOC charter §6 budget) | 0 (channels-only source touch; ``git diff 0c2e567f..HEAD -- src/openakita/orgs/ src/openakita/api/ src/openakita/runtime/ apps/ tests/`` returns empty bytes; v1 ``src/openakita/orgs/`` byte-untouched per ε deletion deferral; 8 / 8 P-RC-9 sentinels unchanged at HEAD — REST contract sentinel #7 OpenAPI snapshot byte-identical because no router/server registration change, frontend stale-path sentinel #8 path-only scan unaffected, 6 parity sentinels green via narrow-slice run) | ADR-0011 (no new Protocol; sweep is mechanical re-routing of existing imports onto v2 ``openakita.runtime.orgs.command_service`` + ``command_models`` per ADR-0011 six-subsystem decomposition); ADR-0012 (β-before-ε ordering operationalises Q-B ACCEPTED (b) v1 deletion: gateway must resolve to v2 BEFORE ``src/openakita/orgs/`` ``git rm`` lands in ε-1, otherwise IM channel boot fails at module-import time per charter R3); R3 invariant per charter §4 (channels gateway swap order MEDIUM risk; mitigation = β before ε + import-time smoke green); canary 3/3 PASS (``tests/integration/test_v2_im_canary_e2e.py`` exercises channels→gateway→runtime path — the most-exercised channels code path; G-RC-1 + R3 dual regression check) |

> P9.9β-1 closes the channels-leg sweep with a focused 5-site
> import rewrite in ``src/openakita/channels/gateway.py`` —
> the only ``src/openakita/channels/`` file under α-1's STRICT
> regex grep at HEAD ``0c2e567f``. The 4 single-line deferred
> imports inside ``MessageGateway`` IM handler / command-status /
> cancel / fast-path methods (lines 3182, 3256, 3335, 3449) get
> verbatim 1-to-1 module-prefix swap ``openakita.orgs.command_service → openakita.runtime.orgs.command_service``
> (``get_command_service`` survives same-named in v2 per α-1 §3
> 1-to-1 family of 8 same-name v2 modules). Site #5 (line 3739,
> multi-line 5-name import inside ``_handle_org_command`` IM submit
> path) requires α-1 §3 absorption-table consultation: while
> ``command_service`` is listed 1-to-1, three of the five imported
> names — ``OrgCommandSource``, ``OrgCommandSurface``,
> ``default_scope_for_surface`` — were defined in v1's monolith
> ``command_service.py`` but absorbed into v2's
> ``runtime/orgs/command_models.py`` typed shard during P9.4c
> (parallel to v1 ``models.py`` → 4-shard split documented in
> §3 absorption row). Resolution: split site #5 into two imports
> — ``command_models`` (4 data-class names) + ``command_service``
> (``get_command_service``). Net diff: 6 insertions / 6 deletions /
> +1 logical line, all confined to the gateway module.
>
> Verification: (1) canary 3/3 PASS — ``tests/integration/test_v2_im_canary_e2e.py`` green at 4.14s /
> 1.64s / 1.58s across 3 sequential reruns; this is the
> channels→gateway→runtime test exercising the very ``submit``
> + ``cancel`` + ``get_command_service`` surfaces we just rewrote
> (G-RC-1 + R3 dual regression check). (2) Narrow slice 584 / 584
> PASS (``tests/api/`` + ``tests/runtime/orgs/`` + ``tests/parity/orgs/`` 68.51s; +3 vs G-RC-9.8 baseline ~581 within
> charter "+/- a few" channel-test drift band). (3) Ruff lint
> clean (``ruff check src/openakita/channels/gateway.py`` — All
> checks passed!). (4) Module-import smoke green
> (``python -c "import openakita.channels.gateway"`` resolves
> cleanly at module-import time — R3 mitigation per charter §4
> R3 row). Ruff ``format --check`` reports cosmetic drift on the
> 5544-line gateway.py that is **byte-identical at parent HEAD**
> ``0c2e567f`` (NOT introduced by β-1 — verified via
> ``git stash`` round-trip); deferred to a separate ruff-format-only
> NIT per channels-only hard rule and per ε deletion deferral
> discipline.
>
> Strict-additive boundary verified: ``git diff 0c2e567f..HEAD --
> src/openakita/orgs/ src/openakita/api/ src/openakita/runtime/ apps/
> tests/`` returns empty bytes — only ``src/openakita/channels/gateway.py`` (1 source file) + this
> ``docs/revamp/PROGRESS_LEDGER_P9.md`` (+1 row) touched. v1
> ``src/openakita/orgs/`` byte-untouched (ε will delete; β
> does not preview). 8 / 8 P-RC-9 sentinels remain ACTIVE.
>
> **HARD STOP per brief**: γ-1 (backend src sweep — 3 api/
> files / 7 sites) NOT started this turn; next operator signal
> opens γ-1.

## P9.9γ-1 -- backend api/ import sweep (4 of 7 sites; 3 deferred on absorption debt)

| _this commit_ | P-RC-9 P9.9γ-1 | refactor(api): P9.9γ-1 swap api/ imports v1→v2 runtime (4 of 7 sites; 3 deferred on absorption debt) [P-RC-9 P9.9γ-1] | +~4 net source LOC (``api/routes/chat.py`` 1 ins / 1 del at L1511 5-name multi-line block re-routed to ``runtime.orgs.command_models`` per β-1 §3 precedent + ``api/server.py`` 3 ins / 3 del at L363 / L364 / L372 1-to-1 to ``runtime.orgs.{manager,runtime,command_service}``; ruff I001 re-sort folded; ledger +~67 LOC; total ~71 well under charter §6 80-LOC γ-1 cap) | 0 (api/-only source touch; ``git diff 112bc62b..HEAD -- src/openakita/orgs/ src/openakita/channels/ src/openakita/runtime/ src/openakita/core/ apps/ tests/`` returns empty bytes; 8 / 8 P-RC-9 sentinels unchanged) | ADR-0011 (mechanical re-route to v2 ``runtime.orgs.command_models`` + ``{manager,runtime,command_service}``); ADR-0012 (γ-before-ε: api/ must resolve to v2 BEFORE ε-1 ``git rm`` per R3); canary 3/3 PASS (``tests/integration/test_v2_im_canary_e2e.py``) |

> P9.9γ-1 swaps 4 of the 7 α-1-inventoried backend api/
> import sites and documents an **absorption-debt finding**
> for the other 3 sites that α-1 §3 listed as
> ready-to-swap but STRICT verification (``rg`` v2 tree at
> HEAD ``112bc62b``) shows are blocked on absorption work
> that never landed in v2.
>
> Swapped (2 files / 4 sites):
>
> * ``api/routes/chat.py`` L1511 — 5-name multi-line
>   ``OrgCommandError`` / ``OrgCommandRequest`` /
>   ``OrgCommandSource`` / ``OrgCommandSurface`` /
>   ``default_scope_for_surface`` re-routed to
>   ``runtime.orgs.command_models`` (all 5 names live there
>   per β-1 precedent: data-classes group in the typed
>   shard, service module exports only ``OrgCommandService``
>   / ``set_command_service`` / ``get_command_service`` /
>   protocol surfaces).
> * ``api/server.py`` L363 / L364 / L372 — ``OrgManager``
>   / ``OrgRuntime`` / (``OrgCommandService`` +
>   ``set_command_service``) 1-to-1 to v2 ``runtime.orgs.*``
>   (all 4 names in v2 ``__all__``; verified pre-write).
>
> Deferred (3 sites — v2 absorption never landed; tracked
> for a future γ-1b / ε-1 pre-deletion sweep):
>
> * ``api/server.py`` L365 ``ensure_builtin_templates`` and
>   ``api/routes/orgs_v2_runtime_orgs.py`` L99
>   ``list_avatar_presets`` + L134
>   ``build_workbench_templates`` — α-1 §3 claims
>   absorption into ``runtime.orgs._runtime_plugin_assets``
>   (P9.2c) but ``rg`` v2 tree shows zero definitions for
>   any of the 3 names. Per user-task hard rule "verify v2
>   module actually exports the symbol" + "if non-1:1
>   absorption is unclear, document the finding and choose
>   the safest path", leaving these 3 v1 imports in place
>   keeps the runtime working today and defers the ~400-LOC
>   absorption work to its own focused commit (well beyond
>   γ-1's 80-LOC cap).
>
> Verification: (1) canary 3/3 PASS — ``tests/integration/test_v2_im_canary_e2e.py`` green at 1.51s / 1.55s / 1.55s
> (the IM→gateway→runtime canary exercising both the
> channels surface β-1 swapped and the api/server OrgRuntime
> + OrgCommandService surfaces we just rewrote). (2) Narrow
> slice **585 / 585 PASS** in 64.72s (``tests/api/`` +
> ``tests/runtime/orgs/`` + ``tests/parity/orgs/`` + canary;
> identical to baseline 585 at parent HEAD; zero test delta).
> (3) Module-import smoke green: ``python -c "import openakita.api.routes.chat; import openakita.api.routes.orgs_v2_runtime_orgs; import openakita.api.server"``
> resolves cleanly. (4) Ruff lint clean on edited files
> (``ruff check`` All checks passed!; ruff I001 auto-resort
> applied to server.py L363-365 since the in-place v1
> ``templates`` import now sorts before the v2 swaps);
> ``ruff format --check src/openakita/api/server.py`` clean;
> ``ruff format --check src/openakita/api/routes/chat.py``
> reports cosmetic drift **pre-existing at parent HEAD** (drift
> at L147-148 / L241-250 / L478 / L515 — nowhere near our
> L1511 edit), deferred per "don't fix pre-existing ruff
> format drift outside scope" hard rule (mirror β-1 gateway
> handling).
>
> Strict-additive boundary: ``git diff 112bc62b..HEAD --
> src/openakita/orgs/ src/openakita/channels/ src/openakita/runtime/
> src/openakita/core/ apps/ tests/`` returns empty bytes — only
> ``src/openakita/api/routes/chat.py`` + ``src/openakita/api/server.py``
> (2 source files) + this ledger touched. 8 / 8 P-RC-9 sentinels
> ACTIVE.
>
> **HARD STOP per brief**: γ-2 (cross-tree swap) rides next;
> δ-1 (test coverage audit doc) NOT started this turn.

## P9.9γ-2 -- cross-tree import swap (1 of 2 sites; 1 deferred on manager.py:55 absorption debt)

| _this commit_ | P-RC-9 P9.9γ-2 | refactor(core): P9.9γ-2 swap core/ import v1→v2 runtime (1 of 2 sites; 1 deferred on v2-internal absorption debt) [P-RC-9 P9.9γ-2] | +~2 net source LOC (``core/_reasoning_engine_legacy.py`` 1 ins / 1 del at L7920 deferred import; ledger +~73 LOC; total ~75 under charter §6 80-LOC γ-2 cap) | 0 (core/-only source touch; ``git diff 09fdb795..HEAD -- src/openakita/orgs/ src/openakita/channels/ src/openakita/runtime/ src/openakita/api/ apps/ tests/`` returns empty bytes; 8 / 8 P-RC-9 sentinels unchanged) | ADR-0011 (mechanical re-route to v2 ``runtime.orgs.runtime``); ADR-0012 (γ-before-ε: core/ must resolve to v2 BEFORE ε-1 ``git rm`` per R3); canary 3/3 PASS |

> P9.9γ-2 swaps the single cross-tree ``openakita.orgs.*``
> import in ``src/openakita/core/`` onto v2 ``runtime.orgs.runtime``
> and documents an **absorption-debt finding** on the
> ``runtime/orgs/manager.py:55`` "special" site investigated
> per user-task brief.
>
> Swapped (1 file / 1 site):
>
> * ``core/_reasoning_engine_legacy.py`` L7920 (deferred
>   import inside a method body; Chinese cycle-break comment
>   ``# 延迟导入避免环路`` preserved verbatim):
>     BEFORE: from openakita.orgs.runtime import get_runtime
>     AFTER:  from openakita.runtime.orgs.runtime import get_runtime
>   ``get_runtime`` verified in v2 ``runtime.orgs.runtime`` +
>   ``runtime/orgs/__init__.py`` re-export pre-write.
>
> Deferred (``runtime/orgs/manager.py:55``, investigation
> result per user-task "special" clause): the 10-symbol
> ``from openakita.orgs.models import (NodeSchedule,
> Organization, OrgEdge, OrgNode, OrgStatus, ScheduleType,
> UserPersona, _new_id, _now_iso,
> infer_agent_profile_id_for_node)``. α-1 §3 per-symbol
> map claims a 4-shard split (org-graph types to
> ``command_models``, ids to any shard, schedule types to
> ``scheduler_models``). STRICT verification (``rg "^(class|def)
> \s+SYM"`` across the 4 v2 shards) shows ONLY ``NodeSchedule``
> + ``ScheduleType`` exist (in ``scheduler_models``); the
> other 8 (``Organization``, ``OrgEdge``, ``OrgNode``,
> ``OrgStatus``, ``UserPersona``, ``_new_id``, ``_now_iso``,
> ``infer_agent_profile_id_for_node``) have **no v2 definition**
> anywhere under ``src/openakita/runtime/``. The α-1 §3
> ``models.py`` row was aspirational — the org-graph
> dataclasses + id helpers never landed. Per "if non-1:1
> absorption is unclear, document the finding and choose
> the safest path", the v1 import stays. v2 manager.py works
> today because v1 ``models.py`` is still in tree; absorption
> (~600+ LOC) MUST land before ε-1 deletes v1, suitable for
> a dedicated γ-2b or ε-1 pre-deletion absorption commit.
> No circular risk materialised (user's concern was about
> the v1 ``identity`` → v2 ``manager`` mapping inverting;
> manager.py only imports v1 ``models``, no inversion).
>
> Verification: (1) canary 3/3 PASS — ``tests/integration/test_v2_im_canary_e2e.py`` green at 1.50s / 1.52s / 1.53s
> (canary IM→gateway→runtime path now exercises
> channels β-1 + api/server γ-1 + core/ γ-2 swaps end-
> to-end). (2) Narrow slice **585 / 585 PASS** in 63.99s;
> identical to baseline 585 at parent γ-1 HEAD; zero test
> delta. (3) Module-import smoke green: ``import openakita;
> import openakita.agent; import openakita.core._reasoning_engine_legacy;
> import openakita.runtime.orgs.runtime`` resolves with the
> new L7920 line traversed (direct ``python -c "import
> openakita.core._reasoning_engine_legacy"`` triggers a
> **pre-existing** ``core.errors`` ↔ ``agent.errors``
> ↔ ``llm.client`` circular bootstrap that exists at parent
> HEAD too — ``git stash`` round-trip confirmed; not
> introduced by γ-2). (4) Ruff drift in
> ``_reasoning_engine_legacy.py`` (27 F401 + 2 F811) is
> **pre-existing at parent HEAD ``09fdb795``**, all in
> unused-import sections (L23 / L392-528 / L7956-7979) far
> from L7920; deferred per "don't fix pre-existing ruff
> drift outside scope".
>
> Strict-additive boundary: ``git diff 09fdb795..HEAD --
> src/openakita/orgs/ src/openakita/channels/ src/openakita/runtime/
> src/openakita/api/ apps/ tests/`` returns empty bytes —
> only ``src/openakita/core/_reasoning_engine_legacy.py`` +
> this ledger touched. Cumulative ``git diff 112bc62b..HEAD
> -- src/openakita/orgs/`` (both γ commits): empty bytes
> per Q-B / R3. 8 / 8 P-RC-9 sentinels ACTIVE.
>
> **HARD STOP per brief**: δ-1
> (``tests/runtime/orgs/coverage_audit.md`` planning task)
> NOT started this turn.
