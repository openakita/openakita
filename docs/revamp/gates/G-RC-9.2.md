# G-RC-9.2 mini-gate -- P9.2 ProjectStore rewrite

**Phase**: P-RC-9 / P9.2 (second subsystem rewrite per
P-RC-9-PLAN section 4).
**Status**: AUTO-SIGNOFF (P9.2 only -- full G-RC-9 gate is at
P9.10, after every subsystem ships).
**Branch**: ``revamp/v3-orgs`` (no push, no amend; nine
commits land linearly: 1 Nit-3 docs fix + 8 P9.2 commits).
**Date**: 2026-05-19.

## 1. Scope

P9.2 replaces v1 ``openakita.orgs.project_store.ProjectStore``
(281 LOC, 15 public methods, single JSON-file backend) with a
v2 Protocol-typed surface under
``src/openakita/runtime/orgs/project_store.py`` (~877 LOC
across two backends + factory) plus a ``project_models.py``
sibling (~225 LOC). Public sync API is verbatim-preserved so
callers can migrate at P9.8 by changing one import line.

The single Nit-3 docs fix from the G-RC-9.1 audit closeout
(date placeholders in ``Q_DECISIONS.md``) lands first, BEFORE
any P9.2 code, per the auditor's "≤ 2 min ride-along" call.
The remaining 4 G-RC-9.1 nits ride along to the full G-RC-9
gate at P9.10.

P9.2 explicitly does NOT delete the v1 file. v1 deletion is
P9.9 after every caller has been redirected. v1 is also NOT
touched at all this phase (verified by sentinel three-piece
section 10 below).

## 2. Commit chain (1 docs fix + 8 implementation commits, all ≤ 392 LOC)

| commit | phase | subject | LOC |
|---|---|---|---|
| ``c29ce31d`` | P9.2.nit3 | docs(revamp): fix Q_DECISIONS.md date placeholders (G-RC-9.1 Nit-3) | +18 |
| ``d58fc045`` | P9.2a0 | feat(runtime/orgs): add v2 project models (OrgProject/ProjectTask + enums + ULID-style ids) | +270 |
| ``38b903d7`` | P9.2a | feat(runtime/orgs): ProjectStoreProtocol + JsonProjectStore CRUD half (project + task CRUD; tree/query in P9.2b) | +329 |
| ``ee76c25a`` | P9.2b | feat(runtime/orgs): complete JsonProjectStore tree/query half (all_tasks, find_task_by_chain, get_task, get_subtasks, get_task_tree, get_ancestors, recalc_progress) | +139 |
| ``a05441ff`` | P9.2c | feat(runtime/orgs): add SqliteProjectStore backend (WAL + BEGIN IMMEDIATE, relational projects/tasks schema) | +392 (WARN; under 400 REJECT) |
| ``bd36a56c`` | P9.2c2 | feat(runtime/orgs): ProjectStore factory + per-org cache (get_default_project_store / reset_default_project_stores) | +84 |
| ``36f4d54b`` | P9.2d | test(parity/orgs): activate 6 project_store parity fixtures (xfail -> pass) | +295 |
| ``1f44230d`` | P9.2e | test(runtime/orgs): add 10 project_store contract cases (read-back/IDs/recalc/delete) across both backends -> 20 collected tests | +277 |
| ``06ded007`` | P9.2e2 | test(runtime/orgs): add 8 project_store contract cases (malformed/schema/concurrent/perf) -> +16 collected tests, total 36 | +235 |

The plan suggested 5-6 commits; reality was 8 (a0/a/b/c/c2/d/e/e2).
The LOC budget forced three extra splits:

* **P9.2a vs P9.2b** -- single-commit landing of the full
  ``project_store.py`` (~430 LOC) tripped the 400-LOC REJECT
  guard. Split into "Protocol + CRUD" (a) and "tree / query
  methods" (b); the Protocol declares the full v1 surface
  from day one and the tree methods are stubbed with
  ``NotImplementedError`` in P9.2a so the file shape is set
  immediately.
* **P9.2c vs P9.2c2** -- folding the factory into the
  SqliteProjectStore commit tripped REJECT at 441 LOC; the
  factory is split off into a 84-LOC follow-up.
* **P9.2e vs P9.2e2** -- single-commit landing of all 18
  contract cases tripped REJECT at 490 LOC. Split into "10
  cases" (e) + "8 cases" (e2) along the case-number axis so
  both halves are independently bisectable.

Per ``docs/revamp/PROGRESS_LEDGER_P9.md``, the splits are
preferable to fat commits: each step is independently
reviewable and the guard discipline holds.

## 3. Test counts (before / after) -- per G-RC-9.1 §3 format

| suite | baseline (G-RC-9.1 close) | post-P9.2e2 | delta |
|---|---|---|---|
| main gate (runtime + agent + api + parity + plugins) | 1155 / 1 skipped / 10 xfailed | **1197 / 1 / 9** | **+42 passed / -1 xfailed** |
| project_store parity (was 1 xfail) | (xfail placeholder, 1 row) | **6 passed** | +6 / -1 xfail |
| project_store contract (NEW) | (did not exist) | **36 passed (18 x 2 backends)** | +36 |
| integration trio (canary + cancel + entrypoints) | 8 passed | 8 passed | no change |

Total +42 (6 parity + 36 contract). Zero regression elsewhere.
The xfail-count drop is exactly 1 (the placeholder that P9.2d
replaced); the other 4 placeholder files
(``test_node_scheduler_parity.py`` /
``test_command_service_parity.py`` /
``test_manager_parity.py`` / ``test_runtime_parity.py``)
keep their xfails intact (verified section 10).

Computation: 1155 + 36 + 6 = 1197. Matches.

## 4. Parity activation evidence

``tests/parity/orgs/test_project_store_parity.py`` was a single
``xfail(strict=True)`` placeholder shipped in P9.0i. P9.2d
replaced it with 6 real fixtures, all green:

```
pytest tests/parity/orgs/test_project_store_parity.py -v
  project_create_empty                 PASSED
  project_create_single_task           PASSED
  project_create_nested_tree           PASSED
  project_recalc_progress_partial      PASSED
  project_recalc_progress_complete     PASSED
  project_delete_subtree               PASSED
  -> 6 passed in 3.36s
```

Sentinel: ``rg -nE "@pytest\.mark\.xfail"
tests/parity/orgs/test_project_store_parity.py`` returns 0
hits (the file's "xfail" string only survives in the module
docstring, describing what was replaced).

Ignore set per P-RC-9-PLAN section 5.2: ``id`` /
``project_id`` / ``parent_task_id`` / ``chain_id`` /
``delegated_by`` (v1 uses uuid4 hex 12, v2 uses ULID-style
``<13-digit ms>_<10 hex>``); plus five timestamp fields
(``created_at`` / ``updated_at`` / ``started_at`` /
``delivered_at`` / ``completed_at``). Parent links are
reconstructed as positional integer indices into the task
list so the tree structure is asserted byte-for-byte without
depending on the volatile ID strings.

## 5. Contract coverage matrix

18 contract cases x 2 backends = 36 rows, all green.

| # | case | json | sqlite | property |
|---|---|---|---|---|
| 1 | empty_store_lists_empty | PASS | PASS | new store -> [] |
| 2 | create_project_round_trip | PASS | PASS | single project survives reload |
| 3 | create_nested_tree_persists | PASS | PASS | 3 children visible via get_task_tree |
| 4 | project_ids_unique | PASS | PASS | 20 fresh project ids distinct |
| 5 | task_ids_unique_within_project | PASS | PASS | 30 fresh task ids distinct |
| 6 | recalc_partial | PASS | PASS | 1 of 4 leaves accepted -> 25 |
| 7 | recalc_complete | PASS | PASS | all 3 leaves accepted -> 100 |
| 8 | recalc_after_demote | PASS | PASS | demoted leaf -> root drops to 65 |
| 9 | delete_leaf | PASS | PASS | True for present, False for missing |
| 10 | delete_subtree_via_recursion | PASS | PASS | mid + 2 leaves gone, root remains |
| 11 | cycle_in_parents_is_walked_safely | PASS | PASS | get_ancestors terminates |
| 12 | orphan_task_remains_orphan | PASS | PASS | listed but parent_task_id="missing" |
| 13 | to_dict_shape_matches_canonical | PASS | PASS | both backends emit same 11-key payload |
| 14 | payload_round_trip_via_from_dict | PASS | PASS | rebuilt project preserves task order |
| 15 | concurrent_add_task_no_loss | PASS | PASS | 2 x 5 add_task -> exactly 10 tasks |
| 16 | perf_add_1000_tasks | PASS | PASS | within 20 s envelope (JSON quadratic; SQLite linear) |
| 17 | perf_all_tasks_under_500ms | PASS | PASS | 1000-task query under 1 s (2x CI slack) |
| 18 | perf_deep_tree_walk | PASS | PASS | 100-deep get_task_tree under 1 s, depth = 100 |

Full run: ``pytest tests/runtime/orgs/test_project_store_contract.py
-v`` -> 36 passed in 34 s.

## 6. Reference codebase usage (per-brief, per-repo -- Nit-4 improvement)

Per G-RC-9.1 auditor Nit-4 recommendation, this section
records each brief and each repo explicitly with a per-item
considered/rejected verdict, rather than the lumped "none
adopted" sentence used in G-RC-9.1 §6.

### 6.1 ``d:\claw-research\briefs`` (6 files)

| brief | one-line topic | considered for P9.2 ProjectStore? | verdict + reason |
|---|---|---|---|
| ``01-cortex.md`` | Cortex (Elixir/OTP) -- NDJSON log parsing + SWIM detector for distributed agent observability | NO | brief is about agent **process** monitoring (telemetry / liveness / SWIM); P9.2 is about project **storage**. No shared design space. |
| ``02-sint-protocol.md`` | SINT Protocol -- CapabilityToken + EvidenceLedger + RequestLifecycleState FSM | YES | rejected. The brief proposes adding a per-task lifecycle FSM (RECEIVED -> POLICY_EVALUATING -> APPROVED -> EXECUTING -> COMPLETED). v1 ProjectTask already has a 7-value status enum (TODO / IN_PROGRESS / DELIVERED / ACCEPTED / REJECTED / BLOCKED / CANCELLED); extending it would BREAK the parity gate (P-RC-9-PLAN section 0.2). Defer to a future P-RC-10+ semantic upgrade. |
| ``03-langgraph.md`` | LangGraph -- Pregel BSP + Checkpoint/Resume + multi-stream observability | YES | rejected. Checkpoint/Resume on every recalc_progress would be powerful but BREAKS parity (v1 has no such feature). The multi-stream observability concept (values / updates / tasks / checkpoints) is interesting but belongs to the OrgRuntime layer (P9.6), not ProjectStore. |
| ``04-metagpt.md`` | MetaGPT -- SOP-based Role + Environment + message history | NO | MetaGPT has no per-task persistence layer (everything is in-memory message history). No design pattern to lift. |
| ``05-crewai.md`` | CrewAI -- Role + Task + Crew + Process + Event Bus | YES | rejected. CrewAI Task has a callback hook on completion (`task.callback`); v1 has no equivalent and adding it BREAKS parity. The Event Bus pattern is interesting for OrgRuntime / OrgCommandService (P9.4 / P9.6) but not for the ProjectStore which is a passive store. |
| ``06-autogen.md`` | AutoGen -- async event queue + Magentic-One progress ledger | YES | rejected. The Magentic-One "progress ledger" is a sibling concept to ProjectStore but operates on natural-language milestones, not typed tasks. Out of scope for the v1-equivalent rewrite; revisit at P-RC-10+ if needed. |

### 6.2 ``d:\claw-research\repos`` (6 subdirs)

| repo | one-line topic | considered for P9.2 ProjectStore? | verdict + reason |
|---|---|---|---|
| ``autogen/`` | Microsoft AutoGen monorepo | NO | already covered by brief 06; same verdict. |
| ``cortex/`` | Elixir Cortex multi-agent CLI orchestrator | NO | already covered by brief 01; same verdict. |
| ``crewAI/`` | CrewAI framework source | NO | already covered by brief 05; same verdict. |
| ``langgraph/`` | LangGraph + checkpointers (sqlite / postgres) | YES | partial. The LangGraph ``BaseCheckpointSaver`` Protocol with sqlite-backed default backend is conceptually parallel to our ``ProjectStoreProtocol`` + ``Sqlite`` / ``Json`` backends. We did NOT lift the API directly (LangGraph's checkpoint surface is async + state-graph specific; ProjectStore is sync + per-org). Instead the in-house P-RC-3 pattern (JsonOrgStore / SqliteOrgStore in ``runtime/orgs/store.py`` + ``sqlite_store.py``) was reused -- same lock + WAL + BEGIN IMMEDIATE recipe lifted from ``SqliteBlackboardBackend`` (P9.1b2) and ``SqliteOrgStore`` (P-RC-3 P3.5). |
| ``MetaGPT/`` | MetaGPT source tree | NO | already covered by brief 04; same verdict. |
| ``sint-protocol/`` | SINT capability/ledger reference | NO | already covered by brief 02; same verdict. |

### 6.3 Summary

**Considered but rejected: 4 briefs (sint-protocol /
langgraph / crewai / autogen).** All four were rejected
because they extend semantics beyond v1; adding any of them
would break the P-RC-9-PLAN section 0.2 parity gate.

**Indirectly adopted: 1 (langgraph)** -- only the *pattern*
("Protocol + multi-backend factory + sqlite WAL"); the API
is in-house, lifted from existing OpenAkita modules
(P-RC-3 ``store.py``, P9.1b2 ``SqliteBlackboardBackend``),
not from LangGraph.

**Not relevant: 2 briefs (cortex / metagpt).** Both are
about agent process / message-history concerns rather than
persistent project storage.

Future subsystems (P9.3 NodeScheduler / P9.4
OrgCommandService / P9.6 OrgRuntime) will revisit
crewai / autogen / langgraph patterns where the design
space actually overlaps.

## 7. Gate evidence per commit

Every P9.2 commit ran:

* ``python scripts/revamp_commit_guard.py --staged --repo .``
  -> all ≤ 392 LOC (largest: P9.2c at 392 WARN under the
  400 REJECT cap; smallest: P9.2.nit3 at 18, P9.2c2 at 84).
* ``python scripts/revamp_loc_audit.py`` -> exit 0 (no v1
  growth, no untracked legacy paths).
* ``ruff check --fix`` over changed paths -> clean.
* ``ruff format`` over changed paths -> clean.
* Targeted ``pytest`` per commit -> green
  (``tests/runtime/orgs/`` 56 passed after each Json/Sqlite
  commit; full contract + parity suite green after P9.2e2).

## 8. Out of scope (deferred)

* v1 ProjectStore deletion -- waits until P9.9 after every
  caller has been redirected.
* Caller redirection from ``openakita.orgs.project_store`` to
  ``openakita.runtime.orgs.project_store`` -- P9.8.
* True ``delete_subtree`` cascade method (the contract case
  drives it through recursive ``get_subtasks`` + ``delete_task``
  to stay parity-clean with v1). New surface lands at P-RC-10+.
* Property-based contract (hypothesis-style) -- the 18-case
  fixture coverage is sufficient per P-RC-9-PLAN section 5.2.
* Cross-process safety stress (multiprocessing.Process pair)
  -- the SqliteProjectStore inherits the same WAL + BEGIN
  IMMEDIATE shape proven by ``SqliteOrgStore`` /
  ``SqliteBlackboardBackend``; P9.6 NodeScheduler will
  validate it end-to-end.

## 9. ADR refs

* **ADR-0011** (subsystem decomposition) -- every P9.2 code
  commit references it. The Protocol-typed surface IS the
  decomposition; cross-backend parity gates the contract.
* **ADR-0012** (no shim under v1) -- P9.2 lands the v2 surface
  fresh under ``runtime/orgs/`` with zero touch to
  ``src/openakita/orgs/`` (sentinel section 10). v1 deletion
  is P9.9.
* **ADR-0013** (wall-clock SLA) -- the concurrent-write
  contract case (15) and the three perf-smoke cases (16-18)
  are the wall-clock SLA tests for ProjectStore. Cycle guard
  on ``get_ancestors`` is the defensive sibling of ADR-0013's
  liveness intent.

## 10. Sign-off + next step (sentinel three-piece per G-RC-9.1 §10 recommendation)

P9.2 is GREEN.

### 10.1 Activation sentinel

```
rg -nE "@pytest\.mark\.xfail" tests/parity/orgs/test_project_store_parity.py
# 0 hits
```

The placeholder is gone; 6 active fixtures replace it.

### 10.2 Other-placeholder sentinel (NOT touched -- still 1 each)

```
rg -nE "@pytest\.mark\.xfail" tests/parity/orgs/test_{node_scheduler,command_service,manager,runtime}_parity.py
# 4 hits total -- one per file (P9.3 / P9.4 / P9.5 / P9.6 will flip these one at a time)
```

### 10.3 Boundary discipline sentinel (v1 NOT touched)

```
git diff 4d2f3078..HEAD -- src/openakita/orgs/ src/openakita/core/ src/openakita/channels/ src/openakita/api/
# (empty -- v1 orgs/, core/, channels/, api/ are untouched since the G-RC-9.1 close)
```

### 10.4 Numbers recap

* Main gate: 1197 / 1 / 9 (vs 1155 / 1 / 10 baseline; +42
  passed, -1 xfailed; total = 1197 + 1 + 9 = 1207 unchanged
  in shape).
* Integration trio: 8 passed (no change vs baseline).
* LOC audit + commit guard + ruff: all green every commit.

### 10.5 Next step

**Next**: P9.3 NodeScheduler. **NOT STARTED in this run** --
the operator has a HARD STOP at G-RC-9.2 to review the
ProjectStore pattern (Protocol + Json/Sqlite split + factory
+ parity ignore-set) before authorising P9.3 onwards. The
``PROGRESS_LEDGER_P9.md`` header is bumped accordingly.

The 4 other G-RC-9.1 nits (compact §3 table cross-link,
sentinel three-piece for blackboard, mini-gate template
extract, optional auditor checklist) are NOT addressed here;
they ride along to the full G-RC-9 gate at P9.10 as
originally scoped.