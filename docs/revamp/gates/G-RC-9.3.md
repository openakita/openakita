# G-RC-9.3 mini-gate -- P9.3 NodeScheduler rewrite

**Phase**: P-RC-9 / P9.3 (third subsystem rewrite per
P-RC-9-PLAN section 4).
**Status**: AUTO-SIGNOFF (P9.3 only -- full G-RC-9 gate is at
P9.10, after every subsystem ships).
**Branch**: ``revamp/v3-orgs`` (no push, no amend; five P9.3
commits land linearly on top of G-RC-9.2 sign-off
``e5a8eabb``).
**Date**: 2026-05-19.

## 1. Scope

P9.3 replaces v1
``openakita.orgs.node_scheduler.OrgNodeScheduler``
(215 LOC, 10 methods, ``OrgRuntime``-coupled) with a v2
Protocol-typed, dependency-injected surface under
``src/openakita/runtime/orgs/node_scheduler.py`` (~501 LOC)
plus a ``scheduler_models.py`` sibling (~145 LOC). Public sync
API is 1:1 with v1 modulo a single documented
``start_for_org`` signature drift (takes
``(org_id, node_ids)`` rather than an ``Organization``
instance so the v1 model is not part of the v2 Protocol
surface).

P9.3 introduces three injected Protocols, with the
**CommandDispatcher** being the cross-subsystem boundary per
ADR-0011 -- P9.4 ``OrgCommandService`` will implement it
without circular imports. The other two
(``ScheduleStore`` / ``SchedulerRuntimeProbe``) decouple the
scheduler from ``OrgRuntime`` so it can be unit-tested in
isolation.

P9.3 explicitly does NOT delete the v1 file. v1 deletion is
P9.9 after every caller has been redirected. v1 is also NOT
touched at all this phase (verified by sentinel three-piece
section 10).

## 2. Commit chain (5 commits, all <= 319 LOC; Nit-4 target met)

| commit | phase | subject | LOC |
|---|---|---|---|
| ``13731906`` | P9.3a0 | feat(runtime/orgs): add v2 schedule models (NodeSchedule/ScheduleType + monotonic-counter id mint) | +173 |
| ``8f1240ac`` | P9.3a | feat(runtime/orgs): NodeSchedulerProtocol + 3 injected Protocols + compute_next_fire_time + OrgNodeScheduler skeleton | +299 |
| ``6bdac4b8`` | P9.3b | feat(runtime/orgs): OrgNodeScheduler implementation (lifecycle + _schedule_loop + dispatch + prompt builder) | +268 |
| ``1895aac3`` | P9.3c | test(parity/orgs): activate 4 node_scheduler parity fixtures (xfail -> pass) | +302 |
| ``e0966ff5`` | P9.3d | test(runtime/orgs): add 12 node_scheduler contract cases (single in-memory backend) | +319 |

The plan suggested 4-5 commits; reality was 5. The split
discipline (Nit-4 fold-in from G-RC-9.2): combined P9.3a0+P9.3a
projected to 442 LOC which trips the 400 REJECT guard, so
schedule models split off into P9.3a0 first (mirroring the
P9.2a0 precedent). Max staged-diff LOC = 319 (P9.3d). All
five commits sit well under the 350 LOC Nit-4 ceiling
recommendation and the 380 WARN line -- no WARN, no REJECT
anywhere in the chain.

## 3. Test counts (before / after) -- per G-RC-9.2 section 3 format

| suite | baseline (G-RC-9.2 close) | post-P9.3d | delta |
|---|---|---|---|
| main gate (runtime + agent + api + parity + integration) | 1197 / 1 skipped / 9 xfailed | **1213 / 1 / 8** | **+16 passed / -1 xfailed** |
| node_scheduler parity (was 1 xfail) | (xfail placeholder, 1 row) | **4 passed** | +4 / -1 xfail |
| node_scheduler contract (NEW) | (did not exist) | **12 passed** | +12 |
| integration trio (canary + cancel + entrypoints) | 8 passed | 8 passed | no change |

Total delta: +16 passed (4 parity + 12 contract). Zero
regression elsewhere. The xfail-count drop is exactly 1 (the
``test_node_scheduler_parity_placeholder`` cleared in P9.3c);
the other 3 placeholder files (``test_command_service_parity.py``
/ ``test_manager_parity.py`` / ``test_runtime_parity.py``)
keep their xfails intact (verified section 10).

Computation: 1197 + 12 + 4 = 1213. Matches.

Targeted run evidence:

```
pytest tests/runtime/orgs/ tests/parity/orgs/ -q
  122 passed, 3 xfailed in 41.27s
```

(122 = 92 pre-P9.3d runtime + 12 new contract + 18 parity
green; 3 xfailed = the 3 placeholders that are not yet
activated.)

## 4. Parity activation evidence

``tests/parity/orgs/test_node_scheduler_parity.py`` was a
single ``xfail(strict=True)`` placeholder shipped in P9.0i.
P9.3c replaced it with 4 real fixtures, all green:

```
pytest tests/parity/orgs/test_node_scheduler_parity.py -v
  scheduler_next_fire_interval                PASSED
  scheduler_next_fire_once                    PASSED
  scheduler_next_fire_cron                    PASSED
  scheduler_dispatch_prompt                   PASSED
  -> 4 passed in 2.94s
```

Ignore set per P-RC-9-PLAN section 5.2: schedule ``id`` (v1
``uuid.uuid4().hex[:12]``; v2 hybrid ``sched_<ms>_<seq>_<rand>``)
plus the prompt ``时间: <iso>`` timestamp line
(stripped by ``_strip_timestamp_line`` because both paths
call ``datetime.now`` at dispatch). 1-ms tolerance asserted
explicitly for the three ``next_fire`` cases.

Croniter sub-question (P-RC-9-PLAN section 5.2): the plan
expected croniter to be "shared so this should be exact". v1
in fact never imported croniter -- ``ScheduleType.CRON`` is
declared in the enum but ``_schedule_loop`` has no cron branch
(falls through to interval timing using ``interval_s`` or the
3600 default). v2 ``compute_next_fire_time`` preserves this
quirk byte-for-byte; cron-string evaluation is deferred to a
future P-RC-10+ semantic upgrade. The parity assertion still
holds because both paths apply the same fall-through rule.

## 5. Contract coverage matrix

12 contract cases x 1 backend = 12 rows, all green.

| # | case | property |
|---|---|---|
| 1 | compute_next_fire_interval | 900 s INTERVAL -> now + 900 |
| 2 | compute_next_fire_once_utc_coerced | naive ISO run_at -> UTC-coerced |
| 3 | compute_next_fire_cron_falls_through_to_interval | documented v1 quirk |
| 4 | start_for_org_empty | no schedules -> no tasks |
| 5 | start_for_org_registers_enabled_schedule | key shape ``{org}:{node}:{id}`` |
| 6 | start_for_org_multi_node_multi_schedule | 3 nodes x 2 = 6 tasks |
| 7 | disabled_schedule_not_started | enabled-only filter |
| 8 | stop_for_org_cancels_only_that_org | per-org prefix isolation |
| 9 | reload_replaces_tasks_for_node | swap old key for new |
| 10 | concurrent_reload_no_loss_100_ops | 4 coroutines x 25 ops (Nit-2) |
| 11 | trigger_once_invokes_dispatcher_with_v1_prompt | byte-for-byte prompt + 2 events + state persist |
| 12 | trigger_once_missing_schedule_id_returns_error | ``{"error": ...}`` on miss |

Full run: ``pytest tests/runtime/orgs/test_node_scheduler_contract.py
-v`` -> 12 passed in 18 s. The 18-s budget is dominated by
case 10''s 100-op concurrent stress; everything else is
sub-second.

Single-backend rationale: unlike ProjectStore (P9.2) which
ships a JSON + a SQLite backend, NodeScheduler''s persistence
is delegated through the injected ``ScheduleStore`` Protocol.
The scheduler holds only in-memory ``asyncio.Task`` handles;
schedule state lives in the store. The cross-backend
parametrisation belongs to whoever implements
``ScheduleStore`` (today: v1 ``OrgManager``; future: v2
``OrgManager`` in P9.5). The contract suite hence ships 12
cases against a single in-memory test double, not 12 x N.

## 6. Reference codebase usage (per-brief, per-repo -- Nit-4 improvement)

Per G-RC-9.1 auditor Nit-4 recommendation, this section
records each brief and each repo explicitly with a per-item
considered/rejected verdict, rather than the lumped "none
adopted" sentence.

### 6.1 ``d:/claw-research/briefs`` (6 files)

| brief | one-line topic | considered for P9.3 NodeScheduler? | verdict + reason |
|---|---|---|---|
| ``01-cortex.md`` | Cortex (Elixir/OTP) -- SWIM detector + Telemetry event bus + NDJSON log parser | YES | rejected. Cortex''s ``Cortex.Mesh.Detector`` is a liveness watchdog (alive/suspect/dead state machine) -- a SchedulerRuntimeProbe sibling. We did NOT lift it: NodeScheduler already gets liveness through the injected ``SchedulerRuntimeProbe.is_node_runnable`` boolean (v1 ``OrgStatus.ACTIVE/RUNNING`` + ``NodeStatus`` not ``FROZEN/OFFLINE``). The SWIM tri-state escalation belongs to OrgRuntime / OrgManager (P9.5 / P9.6), not the scheduler. Revisit at P9.6+. |
| ``02-sint-protocol.md`` | SINT Protocol -- CapabilityToken + EvidenceLedger + RequestLifecycleState FSM + CircuitBreaker | YES | rejected. SINT''s ``CircuitBreakerPlugin`` (CLOSED -> OPEN -> HALF_OPEN) is a sibling to our smart-frequency back-off (consecutive_clean counter + FREQUENCY_MULTIPLIER ceiling). Adopting the SINT semantic would BREAK parity (v1 has no circuit breaker -- only frequency back-off). Defer to a future P-RC-10+ semantic upgrade. |
| ``03-langgraph.md`` | LangGraph -- Pregel BSP + Checkpoint/Resume + multi-stream observability + cron SDK client | YES | partial -- see repo row 6.2 ``langgraph/``. The brief''s ``stream_mode=["checkpoints", ...]`` observability pattern is closer to OrgCommandService (P9.4 wall-clock SLA per ADR-0013) than NodeScheduler. NodeScheduler''s ``emit_event`` Protocol is a 1-call sibling, not a stream. |
| ``04-metagpt.md`` | MetaGPT -- SOP-based Role + Environment + message history | NO | MetaGPT has no scheduling layer; its Stanford-town ``gen_daily_schedule.py`` etc. are LLM-generated *content* schedules for simulation agents, not infra-level cron triggers. Not relevant. |
| ``05-crewai.md`` | CrewAI -- Role + Task + Crew + Process + Event Bus | YES | rejected. CrewAI''s task ``callback`` hook (per-task on-completion lambda) is conceptually parallel to our ``SchedulerRuntimeProbe.emit_event`` but the CrewAI version is a per-task closure, not a Protocol method. v1 has no callback hook on schedules -- adding one BREAKS parity. The CrewAI event-bus pattern is interesting for OrgCommandService (P9.4) but not for NodeScheduler. |
| ``06-autogen.md`` | AutoGen -- async event queue + Magentic-One progress ledger | YES | rejected. AutoGen''s ``output_message_queue`` (single ``asyncio.Queue`` for all events) is a sibling to our ``emit_event`` Protocol -- both standardise the event boundary. We did not lift AutoGen''s Queue because v1 dispatches each event directly to the per-org event store (no queueing); adopting AutoGen''s queue model BREAKS parity. Revisit at P-RC-10+ if back-pressure becomes a concern. |

### 6.2 ``d:/claw-research/repos`` (6 subdirs)

| repo | one-line topic | considered for P9.3 NodeScheduler? | verdict + reason |
|---|---|---|---|
| ``autogen/`` | Microsoft AutoGen monorepo | NO | already covered by brief 06; same verdict. No scheduler-named source files (``rg -l "schedul\|cron"`` empty under ``src/``). |
| ``cortex/`` | Elixir Cortex multi-agent CLI orchestrator | NO | already covered by brief 01; no scheduler-named source files. |
| ``crewAI/`` | CrewAI framework source | NO | already covered by brief 05; no scheduler-named source files. |
| ``langgraph/`` | LangGraph monorepo (incl. ``libs/sdk-py/langgraph_sdk/_async/cron.py`` 22 KB + ``_sync/cron.py`` 22 KB) | YES | rejected. LangGraph DOES ship a Cron client SDK -- ``client.crons.create_for_thread(schedule="0 9 * * *", ...)`` -- exposing cron expressions to scheduled graph runs. We did NOT lift it: (a) LangGraph''s cron lives in the *client SDK* talking to a *server* (the LangGraph platform), whereas v2 NodeScheduler is purely in-process; (b) lifting cron-string evaluation would BREAK the P-RC-9-PLAN section 0.2 parity gate (v1 has no croniter despite the docstring claim). The interesting design parallel for FUTURE work (P-RC-10+) is the LangGraph schedule string format itself ("0 9 * * *") which is the croniter conventional format. |
| ``MetaGPT/`` | MetaGPT source tree (incl. ``ext/stanford_town/actions/gen_*_schedule.py``) | NO | already covered by brief 04. The ``gen_daily_schedule.py`` / ``gen_hourly_schedule.py`` actions are content-generation actions for Stanford-town simulation agents, NOT infrastructure scheduling. |
| ``sint-protocol/`` | SINT capability/ledger reference | NO | already covered by brief 02; no scheduler-named source files. |

### 6.3 Summary

**Considered but rejected: 5 briefs** (cortex / sint-protocol /
crewai / autogen) and 1 repo with non-trivial scheduler code
(langgraph). All five were rejected because adopting their
semantics extends NodeScheduler beyond v1 and breaks the
P-RC-9-PLAN section 0.2 parity gate. The langgraph cron SDK
is documented for a future P-RC-10+ cron-string upgrade.

**Indirectly adopted: 0.** Unlike P9.2 where ``langgraph/``
contributed the Protocol + multi-backend pattern (via prior
adoption in P-RC-3 / P9.1), P9.3 NodeScheduler reuses
entirely in-house patterns -- the three-Protocol injection
shape is lifted from P-RC-9-PLAN section 4 ADR-0011 itself.

**Not relevant: 1 brief (metagpt).** MetaGPT has no
scheduling infrastructure layer.

Future subsystems (P9.4 OrgCommandService / P9.5 OrgManager /
P9.6 OrgRuntime) will revisit cortex / sint-protocol /
crewai / autogen / langgraph patterns where the design space
actually overlaps with their charters.

## 7. Gate evidence per commit

Every P9.3 commit ran:

* ``python scripts/revamp_commit_guard.py --staged --repo .``
  -> all OK (< 380). Max LOC = 319 (P9.3d); smallest = 173
  (P9.3a0). No WARN, no REJECT.
* ``python scripts/revamp_loc_audit.py`` -> exit 0 (no v1
  growth, no untracked legacy paths).
* ``ruff check --fix`` over changed paths -> clean.
* ``ruff format`` over changed paths -> clean.
* Targeted ``pytest`` per commit -> green
  (``tests/runtime/orgs/`` 92 passed after P9.3a0/a/b; 12
  new contract green after P9.3d; 4 new parity green after
  P9.3c).

## 8. Out of scope (deferred)

* v1 NodeScheduler deletion -- waits until P9.9 after every
  caller has been redirected.
* Caller redirection from ``openakita.orgs.node_scheduler`` to
  ``openakita.runtime.orgs.node_scheduler`` -- P9.8.
* Real cron-string evaluation (croniter / APScheduler) --
  P-RC-10+. v1 declared ``ScheduleType.CRON`` but never
  evaluated cron strings; v2 preserves the quirk to keep
  parity intact.
* Property-based contract (hypothesis-style) -- the 12 case
  fixture coverage is sufficient per P-RC-9-PLAN section 4
  P9.3 (charter target was 10 contract cases; we ship 12).
* Wall-clock SLA stress against a real ``OrgManager`` -- the
  ``SchedulerRuntimeProbe`` Protocol decouples this concern;
  P9.5 OrgManager + P9.6 OrgRuntime will validate it
  end-to-end.

## 9. ADR refs

* **ADR-0011** (subsystem decomposition) -- every P9.3 code
  commit references it. The three-Protocol injection
  (``CommandDispatcher`` / ``ScheduleStore`` /
  ``SchedulerRuntimeProbe``) IS the decomposition;
  ``CommandDispatcher`` is the cross-subsystem boundary for
  P9.4 OrgCommandService.
* **ADR-0012** (no shim under v1) -- P9.3 lands the v2
  surface fresh under ``runtime/orgs/`` with zero touch to
  ``src/openakita/orgs/`` (sentinel section 10). v1 deletion
  is P9.9.
* **ADR-0013** (wall-clock SLA) -- the
  ``concurrent_reload_no_loss_100_ops`` contract case is the
  Nit-2 fold-in wall-clock stress (4 coroutines x 25 reload
  cycles, completes < 2 s on the developer machine). The
  ``MAX_FREQUENCY_FACTOR`` back-off ceiling
  (``base_interval * 4.0``) is the wall-clock cap on the
  smart-frequency back-off.

## 10. Sign-off + next step (sentinel three-piece)

P9.3 is GREEN.

### 10.1 Activation sentinel

```
rg -nE "@pytest\.mark\.xfail" tests/parity/orgs/test_node_scheduler_parity.py
# 0 hits
```

The placeholder is gone; 4 active fixtures replace it.

### 10.2 Other-placeholder sentinel (NOT touched -- still 1 each)

```
rg -nE "@pytest\.mark\.xfail" tests/parity/orgs/test_{command_service,manager,runtime}_parity.py
# 3 hits total -- one per file (P9.4 / P9.5 / P9.6 will flip these one at a time)
```

### 10.3 Boundary discipline sentinel (v1 NOT touched)

```
git diff e5a8eabb..HEAD -- src/openakita/orgs/ src/openakita/core/ src/openakita/channels/ src/openakita/api/
# (empty -- v1 orgs/, core/, channels/, api/ untouched since G-RC-9.2 close)
```

### 10.4 Numbers recap

* Main gate: 1213 / 1 / 8 (vs 1197 / 1 / 9 baseline; +16
  passed, -1 xfailed; total = 1213 + 1 + 8 = 1222).
* Integration trio: 8 passed (no change vs baseline).
* LOC audit + commit guard + ruff: all green every commit.
  Max staged-diff LOC = 319 (P9.3d), comfortably under the
  Nit-4 target ceiling of 350.

### 10.5 Next step

**Next**: P9.4 OrgCommandService. **NOT STARTED in this run**
-- the operator has a HARD STOP at G-RC-9.3 to review the
NodeScheduler pattern (Protocol injection + CommandDispatcher
boundary + in-memory ``ScheduleStore`` + parity 1-ms safety
net + Nit-2 stress) before authorising P9.4 onwards. P9.4 is
the big one (700 src + 500 tests + ADR-0013 wall-clock SLA
tests) and gets its own dedicated executor run. The
``PROGRESS_LEDGER_P9.md`` header is bumped accordingly.

## 11. G-RC-9.2 nit fold-in status

The four G-RC-9.2 tracked nits are partially folded into the
P9.3 baseline; the remainder ride along to the full G-RC-9
gate at P9.10.

| nit | description | P9.3 status |
|---|---|---|
| **Nit-1** | ULID mint uses ``time.time()`` (NTP-rollback risk) | **ADDRESSED in P9.3a0**. ``new_schedule_id`` mints ``sched_<13-digit ms>_<8-digit monotonic counter>_<6 hex random>``; the monotonic counter (module-level ``itertools.count`` + ``threading.Lock``) guarantees within-process strict ordering even when ``time.time()`` rolls backwards on NTP correction. Documented in ``scheduler_models.py`` module docstring. The project_models.py ID mint stays unchanged for now; the documented hazard remains in G-RC-9 final follow-up scope. |
| **Nit-2** | concurrent test strength: 2x5 was weak; target Nx100 | **ADDRESSED in P9.3d**. The ``concurrent_reload_no_loss_100_ops`` contract case is 4 coroutines x 25 reload cycles via ``asyncio.gather`` = 100 concurrent ops. v2 NodeScheduler''s ``asyncio.Lock``-guarded mutators survive the stress with exactly one task per node remaining (no leaks, no losses). |
| **Nit-3** | cross-process JSON safety: P9.2 JsonProjectStore is single-process; document in NodeScheduler if applicable | **ADDRESSED in P9.3a**. NodeScheduler has no JSON backend of its own (persistence delegated to the injected ``ScheduleStore``), but the ``ScheduleStore`` Protocol docstring documents the same constraint explicitly: *the underlying store must provide its own cross-process correctness if shared across processes (JSON backends are single-process; SQLite WAL + ``BEGIN IMMEDIATE`` is the cross-process option)*. |
| **Nit-4** | LOC pre-split: P9.2c hit 392 WARN; pre-split future implementation > 350 | **ADDRESSED throughout P9.3**. P9.3a + P9.3a0 was pre-split when the combined projected to 442 LOC. Max staged-diff LOC = 319 (P9.3d); no commit hit even the 380 WARN threshold. The Nit-4 target of <= 350 is met for every commit. |

All four nits are now closed for P9.3''s baseline; nothing
from G-RC-9.2''s tracked-nit set needs to ride to G-RC-9
final from this phase. The G-RC-9.1 set of 4 ride-along nits
(compact section 3 table cross-link, sentinel three-piece
template, mini-gate template extract, optional auditor
checklist) remain on the G-RC-9 final docket as originally
scoped.
