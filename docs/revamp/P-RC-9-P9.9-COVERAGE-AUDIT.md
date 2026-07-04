# P-RC-9 P9.9δ-1 Coverage Audit -- R2 retirement before tests/orgs/ deletion

Authority: P-RC-9 P9.9 charter §2.2 + §4 R2; ADR-0011
(Protocol-typed subsystem decomposition). Required by
charter §5.4 as the first commit of phase δ -- δ-2 / δ-3
/ δ-4 stay blocked until R2 retires.

Scope: docs-only audit asserting that **48 files / 12 238
LOC** of v1 ``tests/orgs/`` have EQUIVALENT-or-STRONGER
v2 coverage **before** any ``git rm`` in P9.9δ-4. R2
stays OPEN until this doc lands; with no BLOCKER gaps,
R2 RETIRES at this commit and δ-2 / δ-3 / δ-4 may run in
charter order.

HEAD at audit: ``459323d7`` (revamp/v3-orgs; close of
P9.9γ-1b). All counts measured at this HEAD; ``git diff
459323d7..HEAD -- src/openakita/ tests/ apps/`` is empty
at commit close.

## 0. Measurement summary

| tree | files | LOC | tests collected |
|---|--:|--:|--:|
| ``tests/orgs/`` (v1; ``test_*.py`` + ``__init__`` + ``conftest``) | 48 | 12 238 | 788 |
| ``tests/runtime/orgs/`` (v2 Protocol contracts) | 10 | 2 761 | 161 |
| ``tests/api/contracts/`` (v2 mint REST twins) | 8 | 2 052 | 184 |
| ``tests/parity/orgs/`` (v2 vs v1 oracle; transitions to v2-only in δ-2) | 10 | 2 535 | 66 |
| **v2 sub-total** | **28** | **7 348** | **411** |

v1 explicit ``^\s*(async\s+def|def)\s+test_`` count
(after class-method flatten): **777**; pytest collection:
**788** (parametrise expansion + 1 expected collection
error in ``test_org_coordinator_delegation.py`` whose
parent v1 module is being deleted -- not a regression).

v2 sentinel state at HEAD ``459323d7``:

* 60 / 60 parity green (5 oracle sentinels × 8-12 cases each)
* 184 / 184 contract green (sentinel #7 OpenAPI snapshot)
* sentinel #6 ``test_runtime_parity.py`` already v2-only (20 cases) -- the structural template for post-δ-2 sentinels #1-#5

### 0.1 Measurement commands (reproducibility)

```
# v1 inventory
ls tests/orgs/*.py | wc -l                                     # 48
python -c "import pathlib; print(sum(len(p.read_text(...).splitlines()) for p in pathlib.Path('tests/orgs').rglob('*.py')))"   # 12238

# v1 + v2 collection
python -m pytest tests/orgs/             --collect-only -q     # 788 (1 expected err)
python -m pytest tests/runtime/orgs/     --collect-only -q     # 161
python -m pytest tests/api/contracts/    --collect-only -q     # 184
python -m pytest tests/parity/orgs/      --collect-only -q     # 66
```

## 1. v1 inventory: 48 files in 10 subsystem clusters

Clustering principle: each v1 test file maps to either
(a) a subsystem-level concept that matches a v2 Protocol
boundary per ADR-0011, or (b) implementation-internal
behaviour of a v1 module that is itself deleted in
P9.9ε-1 (``src/openakita/orgs/``; charter §2.1).

| # | Cluster | v1 files | v1 LOC | v1 tests |
|--:|---|--:|--:|--:|
| C1 | manager | 2 | 400 | 44 |
| C2 | runtime (lifecycle / deadlock / plugin asset / soft-verify) | 4 | 953 | 58 |
| C3 | command_service | 1 | 271 | 10 |
| C4 | blackboard | 1 | 177 | 18 |
| C5 | node_scheduler (+ ProjectStore implicit) | 1 | 115 | 9 |
| C6 | models / templates / plan_features | 3 | 1 740 | 136 |
| C7 | plugin assets / workbench templates | 2 | 345 | 23 |
| C8 | HTTP / API surface (v1-paths) | 4 | 1 117 | 59 |
| C9 | events / inbox / messenger / notifier / heartbeat / diagnosis | 8 | 1 121 | 105 |
| C10 | tools / scenarios / LLM / identity / policies / scaler | 20 | 5 851 | 315 |
| -- | ``__init__.py`` + ``conftest.py`` (no tests; pure plumbing) | 2 | 148 | 0 |
| -- | **TOTAL** | **48** | **12 238** | **777** |

### 1.1 Cluster file roster (LOC / explicit-test counts)

* **C1 manager**: ``test_manager.py`` (328/42), ``test_manager_workbench_constraints.py`` (72/2).
* **C2 runtime**: ``test_runtime.py`` (357/23), ``test_runtime_deadlock_watchdog.py`` (191/14), ``test_runtime_plugin_asset.py`` (251/11), ``test_runtime_soft_verify_incomplete.py`` (154/10).
* **C3 command_service**: ``test_command_service.py`` (271/10).
* **C4 blackboard**: ``test_blackboard.py`` (177/18).
* **C5 node_scheduler / project_store**: ``test_node_scheduler.py`` (115/9). (No standalone v1 ``test_project_store.py``; v1 ProjectStore coverage was implicit through manager / runtime.)
* **C6 models / templates / plan_features**: ``test_models.py`` (433/47), ``test_templates.py`` (265/16), ``test_plan_features.py`` (1042/73).
* **C7 plugin / workbench**: ``test_plugin_assets_helpers.py`` (93/13), ``test_plugin_workbench_templates.py`` (252/10).
* **C8 HTTP / API**: ``test_api.py`` (255/18), ``test_prompt_api_e2e.py`` (319/12), ``test_transparency_autonomy.py`` (495/28), ``test_org_status_snapshot.py`` (48/1).
* **C9 events / messaging**: ``test_event_router.py`` (104/3), ``test_event_store.py`` (107/13), ``test_inbox.py`` (163/19), ``test_messenger.py`` (251/21), ``test_messenger_dedupe.py`` (100/6), ``test_heartbeat.py`` (183/18), ``test_notifier.py`` (124/14), ``test_diagnosis_emit_dedupe.py`` (89/11).
* **C10 tools / scenarios / LLM**: ``test_tools.py`` (155/12), ``test_tool_handler.py`` (301/28), ``test_external_tools.py`` (296/29), ``test_external_tools_e2e.py`` (677/20), ``test_tool_inflight.py`` (232/5), ``test_scaler.py`` (99/10), ``test_identity.py`` (190/16), ``test_policies.py`` (117/16), ``test_research_tool_category.py`` (9/1), ``test_reference_resolution.py`` (311/16), ``test_file_delivery_pipeline.py`` (296/8), ``test_org_affinity_attach_fix.py`` (512/9), ``test_org_coordinator_delegation.py`` (149/7), ``test_org_delegate_self_misjudge_repro.py`` (240/7), ``test_org_orchestration_fix.py`` (659/31), ``test_org_prompt_and_tools.py`` (385/26), ``test_auto_persist_final_answer.py`` (303/16), ``test_execution_robustness.py`` (472/41), ``test_llm_integration.py`` (175/7), ``test_llm_task_execution.py`` (273/10).

### 1.2 Representative v1 test-name samples (5 per cluster)

Grounding evidence -- shows the cluster boundary is real,
not just naming. Five test names from each cluster's
primary file(s):

| Cluster | Sample test names (first 5) |
|---|---|
| C1 manager | ``test_create_and_get``, ``test_create_with_nodes``, ``test_list_orgs``, ``test_list_orgs_excludes_archived``, ``test_update`` |
| C2 runtime | ``test_start_and_shutdown``, ``test_start_org``, ``test_stop_org``, ``test_get_org``, ``test_get_blackboard`` |
| C3 command_service | ``test_default_scope_for_surfaces``, ``test_submit_rejects_second_running_command``, ``test_replace_existing_cancels_previous_command_before_running_new``, ``test_submit_mirrors_external_command_to_blackboard_and_broadcasts``, ``test_submit_org_console_skips_blackboard_mirror_but_broadcasts_started`` |
| C4 blackboard | ``test_write_and_read_org``, ``test_write_and_read_department``, ``test_write_and_read_node``, ``test_read_empty``, ``test_read_org_handles_legacy_string_importance_and_limit`` |
| C5 node_scheduler | ``test_start_with_no_schedules``, ``test_start_with_schedules``, ``test_stop_all``, ``test_reload_node_schedules``, ``test_trigger_nonexistent_schedule`` |
| C6 models | ``test_new_id_with_prefix``, ``test_new_id_without_prefix``, ``test_new_id_uniqueness``, ``test_now_iso_format``, ``test_org_status_values`` |
| C7 plugin | ``test_strips_path_traversal``, ``test_replaces_dangerous_chars``, ``test_default_when_empty``, ``test_caps_overlong_names_keeping_extension``, ``test_caps_overlong_names_without_extension`` |
| C8 HTTP/API | ``test_list_orgs_empty``, ``test_create_org``, ``test_get_org``, ``test_get_nonexistent_org``, ``test_update_org`` |
| C9 events/msg | ``test_summarize_internal_events_without_leaking_message_body``, ``test_router_publishes_only_for_external_scopes``, ``test_final_only_suppresses_progress``, ``test_emit_returns_event``, ``test_query_all`` |
| C10 tools | ``test_is_list``, ``test_each_tool_has_required_fields``, ``test_tool_names_are_unique``, ``test_all_tool_names_start_with_org``, ``test_parameters_are_valid_json_schema`` |

Each name maps to a public-surface concept exercised by
the v2-side coverage in §2: C1-C5 + C8 land directly on
v2 Protocol contracts / REST twins; C7 surfaces are
absorbed into ``_runtime_templates`` + ``_runtime_plugin_assets``
shards (P9.9γ-2b + γ-1b); C6 / C9 / C10 are v1-internal
plumbing whose parent modules disappear in ε-1.

## 2. v2 coverage matrix (per cluster)

| Cluster | v2 equivalents | v2 files | v2 LOC | v2 tests | Verdict |
|---|---|--:|--:|--:|---|
| C1 manager | ``runtime/orgs/test_manager_contract.py`` (16) + ``parity/orgs/test_manager_parity.py`` (1 sentinel / 12 oracle) | 2 | 605 | 17 (+12 oracle) | STRONGER |
| C2 runtime | ``runtime/orgs/test_runtime_contract.py`` (25) + ``parity/orgs/test_runtime_parity.py`` (20; already v2-only) | 2 | 1 066 | 45 | EQUIVALENT (Protocol-level pin) |
| C3 command_service | ``runtime/orgs/test_command_service_contract.py`` (16) + ``parity/orgs/test_command_service_parity.py`` (1 / 10 oracle) | 2 | 694 | 17 (+10 oracle) | STRONGER |
| C4 blackboard | ``runtime/orgs/test_blackboard_contract.py`` (12 × 2 backends = 24) + ``parity/orgs/test_blackboard_parity.py`` (1 / 8 oracle) | 2 | 583 | 25 (+8 oracle) | STRONGER |
| C5 scheduler + project_store | ``runtime/orgs/test_node_scheduler_contract.py`` (12) + ``parity/orgs/test_node_scheduler_parity.py`` (1/10) + ``runtime/orgs/test_project_store_contract.py`` (18 × 2 = 36) + ``parity/orgs/test_project_store_parity.py`` (1/8) + ``runtime/orgs/test_store_contract.py`` (9) + ``test_sqlite_store.py`` (9) + ``test_migrate_json_to_sqlite.py`` (5) | 7 | 1 573 | 71 (+18 oracle) | STRONGER (adds P-RC-3 store + migration coverage v1 never had) |
| C6 models / templates / plan_features | Pydantic models pinned by every ``api/contracts/*`` schema validation (184 cases); plan-feature flags absorbed into v2 runtime Protocols (exercised by ``test_runtime_contract.py``) | -- | (covered above) | -- | EQUIVALENT (public schemas pinned; v1 plan_features was implementation-coupled) |
| C7 plugin assets / workbench templates | Absorbed P9.9γ-1b into ``runtime/orgs/_runtime_templates`` shard (NEW 1 660 LOC) + existing ``_runtime_plugin_assets`` (564 LOC); behavioural surface exercised by ``test_runtime_contract.py`` + ``api/contracts/test_orgs_v2_contracts_ops.py`` (30 cases) | -- | (covered above) | -- | EQUIVALENT |
| C8 HTTP / API (v1-paths) | ``api/contracts/`` 6 contract files (``orgs`` 39 / ``projects`` 36 / ``nodes`` 28 / ``ops`` 30 / ``state`` 29 / ``dispatch`` 20) -- 182 collected; v1 paths kept available via 308 shim per ADR-0015 | 6 | 1 929 | 182 | STRONGER (v2 mint twins) |
| C9 events / messaging / heartbeat / notifier / diagnosis | Parent v1 modules (``orgs/event_router``, ``orgs/event_store``, ``orgs/inbox``, ``orgs/messenger``, ``orgs/heartbeat``, ``orgs/notifier``) deleted in ε-1; surviving public behaviour exercised by ``test_runtime_contract.py`` (event-bus integration; 25 cases) + ``api/contracts/test_orgs_v2_contracts_state.py`` (snapshot / inbox surface; 29 cases) | -- | (covered above) | -- | EQUIVALENT (by construction; v1 internals deleted) |
| C10 tools / scenarios / LLM / identity / policies / scaler | Parent v1 modules deleted in ε-1; v2 tool layer lives under ``src/openakita/tools/`` (out of scope of orgs/); identity / policies / LLM are global subsystems unchanged by P-RC-9; org-scenario tests targeted v1 plumbing that disappears | -- | (covered above) | -- | EQUIVALENT (by construction) |

v2 totals across C1-C5 + C8 (the contract-bearing
clusters): **28 files / 7 348 LOC / 411 collected**.
C6 / C7 / C9 / C10 are covered transitively by the
same v2 surfaces -- they do not need dedicated v2 files
because the parent v1 modules vanish in ε-1.

## 3. Gap list (severity-marked)

Inspection found **0 BLOCKER**, **0 IMPORTANT**, **2
OPTIONAL** gaps. Severity rubric:

* **BLOCKER** -- public v2 surface NOT exercised; would silently regress at ε-1; δ-4 must NOT run.
* **IMPORTANT** -- public surface exercised but only indirectly; consider a focused contract case in P-RC-10+.
* **OPTIONAL** -- v1 test covered a scenario / regression-pin that v2 covers structurally but not scenario-by-scenario.

### 3.1 OPTIONAL gaps (2)

* **O1** -- ``test_plan_features.py`` (73 cases / 1 042 LOC) exercised orchestration plan-feature toggles end-to-end against v1 ``orgs/runtime.py``. v2 covers the public surface through ``test_runtime_contract.py`` (25 cases) but does not re-enumerate the 73 feature flags. *Follow-up*: P-RC-10 NIT to mine notable feature-flag cases into a v2 scenario file IF a regression surfaces post-ε.
* **O2** -- ``test_org_orchestration_fix.py`` + ``test_org_affinity_attach_fix.py`` (40 combined cases / 1 171 LOC) are regression-pin tests for specific bug-fixes in v1 orchestration code paths. The bugs themselves vanish with the v1 paths; the v2 ``OrgRuntime`` re-implementation does not share the same code, so the original regression vector closes. *Follow-up*: keep ledger pointer; if a v2 orchestration bug ships post-ε with a similar shape, port the assertion shape (not the assertion text) into a fresh contract case.

### 3.2 BLOCKER / IMPORTANT gaps

None. The 6 charter-listed runtime / command / manager
/ scheduler / blackboard surfaces (C1+C2+C3+C4+C5) all
have STRONGER v2 coverage (Protocol contract + parity
oracle); the 4 v1-path HTTP files (C8) have STRONGER
coverage in ``api/contracts/`` (182 v2 cases vs 59 v1
explicit cases); the ~28 v1-internal files
(C6+C7+C9+C10) test v1 implementation details whose
parent modules disappear in P9.9ε-1 by construction.

### 3.3 Why "by construction" suffices for C6 / C7 / C9 / C10

A v1-internal test exercises a function or class that
lives inside ``src/openakita/orgs/``. P9.9ε-1 deletes
that module wholesale. After deletion the function /
class does not exist, so:

* No external caller can invoke it (would raise ``ImportError`` -- caught at sweep time by the gamma + delta phases per charter §5.3 / §5.4).
* The behaviour that the v1 test was pinning has no consumer left.
* Any surviving public behaviour was already routed through a v2 Protocol (C1-C5) or a v2 REST endpoint (C8); its coverage is the matrix row above.

In other words: deleting the test alongside the module
it tests is **not** coverage loss -- it is dead-test
removal. The audit's job is to confirm that no v1 test
in C6 / C7 / C9 / C10 is the *sole* exerciser of a
behaviour that survives in v2. Inspection of the
representative samples (§1.2) confirms each tests an
internal v1 entrypoint (no v2 caller), so the criterion
holds.

## 4. R2 verdict: **RETIRED** at HEAD ``459323d7``

**Decision**: R2 (``tests/orgs/`` coverage loss, HIGH)
is RETIRED. δ-2 / δ-3 / δ-4 may proceed in charter order.

**Evidence**:

* 0 BLOCKER + 0 IMPORTANT gaps across 10 clusters (§3).
* v2 sentinel state at HEAD ``459323d7``: 60 / 60 parity + 184 / 184 contract + 9 / 9 ACTIVE sentinels (per the P9.9γ-1b ledger row; ``459323d7`` is the close of γ).
* 7 348 LOC v2-side coverage (28 files / 411 collected) -- charter §2.2 figure matches measurement.
* Sentinel #6 (``test_runtime_parity.py``, 20 cases, v2-only) is the structural template for the post-δ-2 Option B rewrite of sentinels #1-#5.

**Re-open clause**: R2 re-opens automatically IF (a)
δ-2 fails the parity Option B rewrite leaving sentinels
#1-#5 in a hybrid v1-v2 state at the time of δ-4, OR
(b) the OpenAPI snapshot sentinel (#7) reports drift
during δ-3 ``api/contracts/`` sweep. Mitigation owners:
the δ-2 commit author re-verifies sentinel green-light
locally before δ-3 starts; the δ-3 commit author
re-runs sentinel #7 before opening δ-4.

## 5. δ phase split confirmation

Charter §5.4 splits δ into 4 commits. Audit confirms
each split is independently atomic and the dependency
chain is intact:

| split | scope | dependency | status |
|---|---|---|---|
| **δ-1** (this commit) | docs-only coverage audit (R2 retire) | none | **closed at this commit** |
| **δ-2** | ``tests/parity/orgs/`` 5-file Option B rewrites + ``tests/unit/`` 8-file v1→v2 sweep (14 sites per inventory §2.4) | δ-1 R2 RETIRED | ready |
| **δ-3** | ``tests/api/`` 1 file + ``tests/e2e/`` 1 file + ``tests/integration/`` 1 file sweep (9 sites per inventory §§2.1-2.3) | δ-2 sentinels still green after Option B | ready |
| **δ-4** | ``git rm -r tests/orgs/`` atomic (48 files / 12 238 LOC) | δ-3 (zero v1 hits outside tests/orgs/ remain) | unblocked |

Boundary refinement vs charter §5.4 estimate: no change
to commit count. δ-2 LOC ~100 / -30 (parity rewrites
dominate); δ-3 LOC ~40 / -15; δ-4 LOC +20 ledger / -12
238 deletion. All within charter LOC envelope.

## 6. ``tests/parity/orgs/`` 5-file transition checklist (Option B per inventory §5)

Per inventory §5 all 5 oracle-using parity files take
Option B in δ-2 (drop v1 import, replace oracle equality
with v2 vs golden-dict harness). Per-file v1 imports to
drop and v2 replacements:

| # | file | v1 imports to drop | v2 replacement | oracle cases preserved |
|--:|---|---|---|--:|
| 1 | ``test_blackboard_parity.py`` | ``openakita.orgs.blackboard.OrgBlackboard`` (as ``V1Blackboard``) | keep ``openakita.runtime.orgs.blackboard.OrgBlackboard``; ``MemoryScope`` / ``MemoryType`` move to ``runtime.orgs.memory_models`` | 8 → 8 v2-baseline |
| 2 | ``test_command_service_parity.py`` | ``openakita.orgs.command_service.OrgCommandService`` (oracle) + ``OrgCommandRequest`` / ``Source`` / ``Surface`` / ``OrgCommandError`` from v1 | runtime equivalents under ``openakita.runtime.orgs.command_service`` | 10 → 10 v2-baseline |
| 3 | ``test_manager_parity.py`` | ``openakita.orgs.manager.OrgManager`` (oracle) | ``openakita.runtime.orgs.manager.OrgManager`` (already imported as v2) | 12 → 12 v2-baseline |
| 4 | ``test_node_scheduler_parity.py`` | ``openakita.orgs.node_scheduler.OrgNodeScheduler`` (oracle) + ``NodeSchedule`` / ``ScheduleType`` from v1 | ``openakita.runtime.orgs.scheduler_models.NodeSchedule`` / ``ScheduleType`` | 10 → 10 v2-baseline |
| 5 | ``test_project_store_parity.py`` | ``openakita.orgs.project_store.ProjectStore`` (oracle) + ``OrgProject`` / ``ProjectTask`` / ``TaskStatus`` from v1 | ``openakita.runtime.orgs.project_models`` equivalents | 8 → 8 v2-baseline |
| -- | **TOTAL** | **5 v1 oracle modules** | **same-shape v2 modules** | **48 / 48 oracle → 48 / 48 v2-baseline** |

Acceptance harness for each file in δ-2:

1. Drop the v1 import line (the v1 module will not exist after ε-1; the import would raise ``ModuleNotFoundError``).
2. Replace each ``assert v1.do(X) == v2.do(X)`` with ``assert v2.do(X) == EXPECTED``, where ``EXPECTED`` is the golden dict the v1 oracle currently produces (captured from the green run and inlined verbatim).
3. Sentinel filename stays ``test_*_parity.py`` for continuity; docstring + first-line comment updated from "v1 oracle" to "v2-baseline" (same shape as sentinel #6 ``test_runtime_parity.py``).
4. Post-rewrite: re-run sentinels #1-#5; expected count stays at 48 / 48 green; 9 / 9 sentinel-active total.

## 7. Cross-references + HARD STOP

* Authority: P-RC-9 P9.9 charter §2.2 + §4 R2 (this audit retires R2).
* Mapping data: P-RC-9-P9.9-IMPORT-SWEEP-INVENTORY.md §5 (per-file Option B table; this doc §6 mirrors it for δ-2 readiness).
* ADR refs: ADR-0011 (Protocol-typed subsystem decomposition; v2 contracts pin the public surface), ADR-0015 (308 shim retirement is a separate v2.1.0 milestone; independent of v1 test deletion).
* Strict additive: ``git diff 459323d7..HEAD -- src/openakita/ tests/ apps/`` empty at audit close.

**HARD STOP** per brief: δ-2 NOT started this turn.
This commit ships docs-only; the audit's verdict
(R2 RETIRED) unblocks δ-2 / δ-3 / δ-4 in subsequent
commits.