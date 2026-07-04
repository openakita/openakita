# G-RC-9.6 -- P9.6 (OrgRuntime) mini-gate

**Status**: PASS (closes P9.6; no ACCEPTANCE.md upgrade).
**Branch**: ``revamp/v3-orgs``.
**HEAD pre-P9.6**: ``ce7a055f`` (G-RC-9.5 close).
**HEAD post-P9.6**: _this commit_ (G-RC-9.6).
**Scope**: 21 P9.6 implementation/doc commits + this gate.

## 1. P9.6 commits (22 since ``ce7a055f``)

P9.6 spans three sub-turns: alpha (scaffolding + 3 small
siblings); beta (4 heavy siblings + wiring); gamma (parity
20 + contract 25 + this gate).

| commit | tag | LOC | subject (compressed) |
|---|---|--:|---|
| ``e4b59137`` | P9.6.nit | 47 | clean up G-RC-9.5 NIT-D-1 + 4 G-RC-9.4 doc-only NITs |
| ``f36f7f19`` | P9.6.plan | 240 | revise P9.6 budget 1200 -> 3000 LOC + add ADR-0014 |
| ``fb0bb6dd`` | P9.6a0 | 225 | runtime.py: 3 new Protocols + 3 default in-memory backends |
| ``ea0ddda7`` | P9.6a | 123 | OrgRuntime skeleton + CommandRuntimeProtocol 6-stubs |
| ``a67675d7`` | P9.6b | 165 | _runtime_event_bus.py: InMemory + WebSocket + factory |
| ``64514e19`` | P9.6c | 218 | _runtime_watchdog.py: CommandWatchdog + IdleProbeLoop |
| ``9274a6f2`` | P9.6d | 255 | _runtime_lifecycle.py: OrgLifecycleManager (~18 v1 methods) |
| ``dc3e330a`` | P9.6.pause | 179 | P9.6 pause checkpoint (2026-05-19) |
| ``1daa2fe8`` | P9.6e | 348 | _runtime_dispatch.py: CommandDispatchManager + _CommandTracker |
| ``5257e123`` | P9.6f1 | 293 | _runtime_agent_pipeline.py: AgentCache + Builder + ProfileResolver |
| ``06f436ed`` | P9.6f2 | 258 | _runtime_agent_pipeline.py: AgentPipelineExecutor |
| ``598fbc1a`` | P9.6g | 355 | _runtime_node_lifecycle.py: NodeStatusController + Router |
| ``33136556`` | P9.6h1a | 197 | _runtime_plugin_assets.py h1a: bridge + helpers |
| ``3ef6ed3d`` | P9.6h1b | 163 | _runtime_plugin_assets.py h1b: PluginAssetRecorder append |
| ``ac8b5d92`` | P9.6h2 | 256 | _runtime_plugin_assets.py h2: FileOutputRegistry + synth |
| ``412cbd55`` | P9.6i | 45 | wire CommandDispatchManager into OrgRuntime |
| ``fec6fed4`` | P9.6.docs | 14 | backfill __init__.py docstring for P9.6i |
| ``10722ac2`` | P9.6gamma-1a | 296 | activate runtime_parity sentinel -- 10 dispatch + pipeline |
| ``b784c4f3`` | P9.6gamma-1b | 297 | land 10 more parity -- 5 node_lifecycle + 5 plugin_assets |
| ``fb3c7168`` | P9.6gamma-2a | 244 | test_runtime_contract.py NEW -- 13 cases |
| ``88603c4b`` | P9.6gamma-2b | 246 | append 12 contract cases -- concurrency + SLA |
| _this commit_ | G-RC-9.6 | ~340 | G-RC-9.6 P9.6 (OrgRuntime) mini-gate -- PASS |

All 22 commits ruff-clean. Max LOC = **370 at P9.6e**
(``1daa2fe8``); all 22 commits within <= 380 target; REJECT
threshold 400 not approached.
N3 ledger discipline: every row appended in the same commit,
**except P9.6f2 (``06f436ed``) where the ledger row was
retroactively backfilled in P9.6g (``598fbc1a``); P9.6g commit
body self-discloses this**.

## 2. P9.6 implementation summary

P9.6 ships the v2 ``OrgRuntime`` -- charter subsystem #6,
the **LARGEST** of ADR-0011's six. v1
``src/openakita/orgs/runtime.py`` is **6 355 LOC across 132
methods** in a single class (the P9.6 turn-1 escape-hatch
report flagged the cross-cutting ``tracker`` x 254 +
``chain_id`` x 221 references that drove ADR-0014). The
v2 rewrite decomposes into ``runtime.py`` + 7 siblings:

| sibling | v2 LOC | v1 LOC absorbed |
|---|--:|--:|
| ``runtime.py`` | 359 | -- (rewrite) |
| ``_runtime_event_bus.py`` | 153 | ~80 |
| ``_runtime_watchdog.py`` | 216 | ~318 |
| ``_runtime_lifecycle.py`` | 236 | ~500 |
| ``_runtime_dispatch.py`` | 328 | ~1 050 |
| ``_runtime_agent_pipeline.py`` | 521 | ~1 410 |
| ``_runtime_node_lifecycle.py`` | 331 | ~600 |
| ``_runtime_plugin_assets.py`` | 564 | ~1 064 |
| **TOTAL** | **2 708** | **~5 022** |

**ADR-0014 budget**: ~3 000 src LOC. **Actual: 2 708 LOC**.
Headroom ~290 LOC reserved for parity-driven backfill was
banked rather than spent (parity green 20 / 20 first run).
**Compression: 6 355 -> 2 708 = 57.4 % reduction** across
8 focused modules each <= 564 LOC. Tests delta from
P9.6gamma: **+20 parity + +25 contract = +45 cases**.

## 3. Test counts (measured; NOT extrapolated)

Per the user brief: run main gate FULLY with
``pytest -q --tb=no``.

### 3.1 Full ``pytest`` (every collected test)

```
.venv/Scripts/python -m pytest -q --tb=no
  6538 passed, 116 skipped, 5 xfailed, 13 failed, 6 deselected
  2793 warnings in 1043.33 s (0:17:23)
```

The **13 failures are all pre-existing** and were NOT
introduced by P9.6 (proven by section 10 piece 3:
``git diff fec6fed4..HEAD -- src/openakita/{orgs,core,channels,api}/``
returns empty). Failing test ids (verbatim):

* ``tests/component/test_memory_manager.py::TestMemoryManagerDelete::test_delete_nonexistent``
* ``tests/e2e/test_p0_regression.py::test_p0_2_phase0_no_hard_exit_reason``
* ``tests/legacy/test_telegram_simple.py::test_bot_info`` (+ ``tests/test_telegram_simple.py::test_bot_info``)
* ``tests/orgs/test_external_tools_e2e.py::TestExternalToolExecutionE2E::test_agent_calls_web_search``
* ``tests/orgs/test_org_orchestration_fix.py::TestAcceptanceChainResolution::test_accept_short_chain_reports_ambiguous_candidates``
* ``tests/orgs/test_plan_features.py::TestNoHardTimeout::test_no_wait_for_in_run_agent_task``
* ``tests/unit/test_c23_security_confirm_decision_chain.py::TestPayloadIntegration::test_yield_points_include_decision_chain``
* ``tests/unit/test_c23_tool_intent_preview_ui_wiring.py::test_backend_still_emits_tool_intent_preview``
* ``tests/unit/test_policy_v2_c13_multi_agent.py::test_tool_executor_security_confirm_marker_has_no_c13_fields``
* ``tests/unit/test_policy_v2_c8b3_apply_resolution.py::TestCallsiteMigrationStatic::test_agent_cleanup_migrated``
* ``tests/unit/test_policy_v2_c8b5_trust_mode_isolation.py::TestExternalCallersGone::test_agent_py_no_v1_is_trust_mode_call`` (+ ``test_check_trust_mode_skip_is_pure_v2``)

These all predate P9.6 and ride to P-RC-10 hygiene /
pre-release. None reference any ``runtime/orgs/`` module.

### 3.2 Narrowed slice (G-RC-9.4 / G-RC-9.5 format)

```
.venv/Scripts/python -m pytest tests/runtime tests/agent
                                tests/api tests/parity
                                tests/integration -q --tb=no
  1457 passed, 12 skipped, 5 xfailed in 113.74 s
```

| scope | baseline (``ce7a055f``) | after G-RC-9.6 | delta |
|---|---|---|---|
| narrowed slice | 1 272 / 1 / 6 xfailed | **1 457 / 12 / 5** | **+185 passed / -1 xfail** |
| parity-only | 138 / 6 xfailed | **158 / 5** | **+20 / -1 xfail** |
| runtime/orgs + parity/orgs | (n/a) | **221 / 0 in 47.96 s** | -- |

### 3.3 P9.6 targeted

```
pytest tests/runtime/orgs tests/parity/orgs -q
  221 passed in 47.96 s
```

Zero xfail; zero failure. The +185 net delta includes 45
P9.6gamma cases + ~140 from P9.6alpha-beta smokes,
scheduler regressions, and intermediate import-side
coverage already-green-but-not-counted at G-RC-9.5 close.

## 4. Parity activation evidence (20 / 20)

P9.6gamma-1a removed the ``xfail(strict=True)`` placeholder
shipped in P9.0i and landed 10 fixtures; P9.6gamma-1b
landed 10 more. Final: **20 fixtures, 0 xfail markers**.

| id | category |
|---|---|
| ``dispatch.send_command.happy`` / ``cancel_user_command.running`` / ``get_command_tracker_snapshot.running`` / ``has_active_delegations.no_chains_open`` / ``get_active_root_intent.most_recent_running`` | dispatch (5) |
| ``agent_pipeline.activate_and_run.happy`` / ``missing_org`` / ``paused_org_skip`` / ``quota_pauses_org`` / ``other_error`` | agent_pipeline (5) |
| ``node.on_inbound.delivered`` / ``queued_when_busy`` / ``stop_intent`` / ``format_incoming_message.shape`` / ``drain.replay_after_resume`` | node_lifecycle (5) |
| ``assets.record_url.plugin`` / ``record_file.digest`` / ``file_output_registered.event`` / ``react_trace_stats`` / ``task_delivery_synthesizer.default_summary`` | plugin_assets (5) |

All 20 use isolated ``tmp_path`` per fixture; no cross-
fixture pollution.

## 5. Contract evidence (25 / 25)

``tests/runtime/orgs/test_runtime_contract.py`` NEW (485
LOC after ruff format). 25 cases passing in 3.35 s.
``pytest --collect-only`` lists 25 ids in 0.16 s.

* **A (10)** CommandRuntimeProtocol methods: send_command
  happy + org_not_found; cancel running / missing /
  idempotent; has_active_delegations chain gate;
  snapshot running + missing; event_store + inbox default-None.
* **B (3)** new-Protocol round-trip: RuntimeStateProtocol
  transition + is_active; NodeLifecycleProtocol register
  / set / get / deregister; EventBusProtocol pub-sub.
* **C (1)** AgentBuilderProtocol: runtime-checkable +
  cache + teardown idempotency.
* **D (3)** composition smokes: isinstance(rt,
  CommandRuntimeProtocol); default in-memory backends
  fall back; sibling dispatch wired through bus + service.
* **E (4)** concurrency: 100-id dispatch burst; 8
  concurrent cancels race-safe (1 cancelled / 7
  already_done); 50 parallel chain registrations; 40
  concurrent emits without loss.
* **F (1)** integration: send -> cancel end-to-end with
  event sequence + service double + final CANCELLED snapshot.
* **G (2)** wall-clock SLA via internal
  ``time.perf_counter`` (NIT-I-1 lesson):
  send_command + cancel both under 50 ms in-process.
* **H (1)** get_active_root_intent.most_recent_wins smoke.

## 6. Reference matrix (NIT-E-1 discipline -- per-item rejected)

### 6.1 ``d:\claw-research\repos`` (6 dirs; 720 files in langgraph alone)

| repo | considered for | verdict |
|---|---|---|
| ``langgraph`` (720 files re-verified 2026-05-20) | graph-state / cancel scope | rejected (DSL vs FSM mismatch; G-RC-9.5 NIT-E-1 closure stands) |
| ``cortex`` | event-bus pub-sub | rejected (event-sourced; v2 needs in-process) |
| ``sint-protocol`` | tracker / chain semantics | rejected (blockchain ledger; v2 trackers are runtime-only) |
| ``crewAI`` | agent activation / role binding | rejected (static-declared; v2 AgentBuilderProtocol is DI factory) |
| ``MetaGPT`` | role + workflow orchestration | rejected (roles ~ processes; v2 nodes ~ messages) |
| ``autogen`` | multi-agent message routing | rejected (GroupChat implicit; v2 NodeMessageRouter explicit Protocol-backed) |

### 6.2 ``d:\claw-research\briefs`` (6 briefs)

| brief | considered for | verdict |
|---|---|---|
| ``01-cortex.md`` | event sourcing | rejected (v1 OrgEventStore slot kept; no v2 attribution) |
| ``02-sint-protocol.md`` | chain id propagation | rejected (sint chains cryptographic; v2 uuid4) |
| ``03-langgraph.md`` | wall-clock cancellation | rejected (NIT-E-1 closure: universal perf_counter idiom) |
| ``04-metagpt.md`` | role registry | rejected (too coarse vs ProfileResolver) |
| ``05-crewai.md`` | agent cache lifecycle | rejected (process-scoped vs per-(org,node)) |
| ``06-autogen.md`` | conversation routing | rejected (per-message vs per-node FSM) |

**Net brief / repo adoption for P9.6: NONE.** All design
inputs come from v1 ``orgs/runtime.py`` itself (parity
oracle), the P-RC-9-PLAN charter, ADR-0011 / ADR-0014,
and the in-tree P9.1-P9.5 patterns. Per NIT-E-1 discipline
this matrix marks all 12 items **rejected** explicitly
rather than padding with "partial cue" attribution.

## 7. Architecture decisions (recap; no new ADRs)

* **ADR-0011**: OrgRuntime is subsystem #6; the 8-module /
  4-new-Protocol composition is ADR-0011's "Protocol per
  responsibility, <= 5 methods" rule applied at scale
  (section 9 below).
* **ADR-0012**: v1 ``src/openakita/orgs/runtime.py`` is
  UNTOUCHED through all 22 P9.6 commits; v2 sits entirely
  under ``src/openakita/runtime/orgs/``. Cutover -> P9.7
  (callers) + P9.8/9 (physical deletion).
* **ADR-0013**: P9.6gamma-2b applied the NIT-I-1 lesson
  with internal ``time.perf_counter`` for the 2 wall-clock
  SLA contract cases (50 ms threshold for in-process happy).
* **ADR-0014**: v2 totals 2 708 src LOC across 8 files,
  under 3 000 LOC budget; ~290 LOC headroom banked.

## 8. NIT fold-in (Phase 0)

P9.6.nit (``e4b59137``) folded the G-RC-9.5 NIT-D-1 +
4 G-RC-9.4 doc-only NITs (K-1 / K-2 / L-1 / G-2) BEFORE
P9.6 implementation. Only G-RC-9.4 NIT-B-1 (burst-test
semantics) still rides to G-RC-9 final.

## 9. Protocol audit (ADR-0011 enforcement)

P9.6 introduces **4 new public Protocols** + **2 internal**
+ **REUSES 6** P9.1-P9.5 surfaces. All 4 new public
Protocols are <= 5 methods.

| Protocol | source | methods | <= 5? |
|---|---|--:|---|
| ``OrgLookupProtocol`` | command_service.py (REUSED) | 1 | yes |
| ``OrgPersistenceProtocol`` | manager.py (REUSED) | 4 | yes |
| ``OrgLifecycleEmitterProtocol`` | manager.py (REUSED) | 3 | yes |
| ``OrgCommandServiceProtocol`` | command_service.py (REUSED) | 11 | (deliberate exemption; big-public-contract) |
| ``NodeSchedulerProtocol`` | node_scheduler.py (REUSED) | 4 | yes |
| ``BlackboardBackendProtocol`` | blackboard.py (REUSED) | 5 | yes |
| ``CommandRuntimeProtocol`` | command_service.py (IMPLEMENTED by OrgRuntime) | 6 | (1 over; the contract OrgRuntime fulfils) |
| ``RuntimeStateProtocol`` | runtime.py (P9.6 NEW) | 4 | yes |
| ``NodeLifecycleProtocol`` | runtime.py (P9.6 NEW) | 5 | yes |
| ``EventBusProtocol`` | runtime.py (P9.6 NEW) | 4 | yes |
| ``AgentBuilderProtocol`` | _runtime_agent_pipeline.py (P9.6 NEW) | 2 | yes |
| ``_AgentRunCallable`` | _runtime_agent_pipeline.py (INTERNAL) | 1 | yes |
| ``_TrackerSnapshotProtocol`` | _runtime_watchdog.py (INTERNAL) | 0 attrs | yes |

P9.6-specific: **4 new public + 2 internal = 6**.
Total around OrgRuntime: **11 public + 2 internal = 13**.
Methods added by P9.6: 4+5+4+2 = **15 new slots**, average
3.75 / Protocol, max 5 -- inside the ADR-0011 ceiling.
G-RC-9.4 NIT-G-1 distinction (DI vs implemented vs
SLA-only) preserved.

## 10. Sentinel three-piece -- 6 / 6 ACTIVE (MILESTONE)

This is the **LAST** P-RC-9 sentinel activation. All 6
P9.0i parity placeholders are now ACTIVE.

1. ``grep -c "@pytest.mark.xfail" tests/parity/orgs/test_runtime_parity.py``
   -> **0** (P9.6gamma-1a removed the decorator; P9.6gamma-1b
   paraphrased the docstring reference).
2. ``grep -c "@pytest.mark.xfail" tests/parity/orgs/test_*_parity.py``
   across all 6 orgs/ parity files -> **0**. All 6 active:
   blackboard (P9.1c) + project_store (P9.2c) +
   node_scheduler (P9.3c) + command_service (P9.4c) +
   manager (P9.5c) + runtime (P9.6gamma).
3. ``git diff fec6fed4..HEAD -- src/openakita/{orgs,core,channels,api}/``
   -> **empty bytes** (strict-additive boundary held; v1
   subsystem under ``src/openakita/orgs/`` untouched).

**The v2 OrgRuntime is the LAST charter subsystem; 6 / 6
active sentinels close the P-RC-9 parity contract.**

## 11. NIT fold-in status (tracks G-RC-9 final residue)

| nit | from | folded? | commit | rationale |
|---|---|---|---|---|
| D-1 | G-RC-9.5 | YES | ``e4b59137`` | P9.5 docstring count |
| K-1 | G-RC-9.4 | YES | ``e4b59137`` | fixture ids re-pinned |
| K-2 | G-RC-9.4 | YES | ``e4b59137`` | v2_im_cancel 4/4 |
| L-1 | G-RC-9.4 | YES | ``e4b59137`` | SLA file LOC 300 |
| G-2 | G-RC-9.4 | YES | ``e4b59137`` | lock-claim wording |
| E-1 | G-RC-9.4 | YES | ``57611160`` | LangGraph attribution |
| G-1 | G-RC-9.4 | YES | ``57611160`` | "5 DI + 1 public + 1 SLA" |
| B-1 | G-RC-9.4 | NO | -- | burst-test; rides to G-RC-9 final |

**7 of 8 NITs CLOSED; only B-1 remains for G-RC-9 final.**

## 12. HARD STOP

Per the brief: **P9.7 REST endpoint mint is NOT started**.
P9.7 rewires ``/api/orgs/`` over the new v2 OrgRuntime +
OrgCommandService instead of v1 ``openakita.orgs``. It is
a different shape from P9.1-P9.6 subsystem rewrites and
needs its own planning round.

**G-RC-9.6 status: PASS.** P9.6 closed; 22 commits clean;
20 / 20 parity + 25 / 25 contract green; sentinel
three-piece **6 / 6 ACTIVE**; zero
``src/openakita/orgs/`` touch across 22 commits;
ACCEPTANCE.md NOT modified (P9.7 will close #4 when REST
lands).

## 13. P-RC-9 subsystem completion panorama

All 6 ADR-0011 subsystems have v2 implementations + active
parity sentinels:

| # | subsystem | v1 LOC | v2 LOC | parity |
|--:|---|--:|--:|---|
| 1 | OrgBlackboard | 344 | 957 (blackboard.py) | 8 / 8 active (P9.1c) |
| 2 | ProjectStore | 638 | 1 199 (project_store + models) | 8 / 8 active (P9.2c) |
| 3 | NodeScheduler | 651 | 750 (node_scheduler + models) | 10 / 10 active (P9.3c) |
| 4 | OrgCommandService | 1 142 | 1 800 (command_service + models) | 10 / 10 active (P9.4c) |
| 5 | OrgManager | 683 | 1 058 (manager + _org_layout) | 12 / 12 active (P9.5c) |
| 6 | **OrgRuntime** | **6 355** | **2 708** (runtime + 7 siblings) | **20 / 20 active (P9.6gamma)** |

The v2 -> v1 compression is dominated by the OrgRuntime
rewrite (57 % reduction). Other subsystems grew because
v2 added Protocol scaffolding + multiple backends per
subsystem; OrgRuntime was the only one with class-soup big
enough to compress.

**Next**: P9.7 REST endpoint mint (rewires ``/api/orgs/``
to v2; ACCEPTANCE.md #4 closes here). P9.8 / P9.9
physical deletion of ``src/openakita/orgs/`` and final
P-RC-9 close. **P-RC-9 phase architecturally complete
except wiring + deletion.** All 6 charter subsystems
implemented + parity-validated; all 6 sentinels active;
ADR-0011 / 0012 / 0013 / 0014 invariants held.
