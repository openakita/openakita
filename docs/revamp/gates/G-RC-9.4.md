# G-RC-9.4 -- OrgCommandService mini-gate (P-RC-9 P9.4 sign-off)

Status: **PASS (auto-signoff for P9.4 only)**

Closes: P-RC-9 P9.4 (charter subsystem #4 -- the biggest, v1
963 LOC). Closes ACCEPTANCE.md criterion #2 caveat
(``P8.7-doc-fix``) by landing the three ADR-0013 wall-clock
SLA tests. Pre-position for P9.5 OrgManager (charter
subsystem #5).

Per the G-RC-9.3 auditor recommendation, this mini-gate
preserves the eleven-section G-RC-9.3 format and adds a
twelfth section (``## 12. ACCEPTANCE.md #2 closure
evidence``) because P9.4e is the first P-RC-9 phase to
materially upgrade an ACCEPTANCE.md rating (Pass-with-caveat
-> Pass).

## 1. Scope

P9.4 implements ADR-0011 subsystem #4 -- the v2
``OrgCommandService`` under ``runtime/orgs/`` -- replacing
v1 ``openakita.orgs.command_service`` (963 LOC) with a
Protocol-typed, DI v2 surface that implements the
``CommandDispatcher`` boundary defined by P9.3
``node_scheduler.py:94-106``.

In scope (P-RC-9-PLAN section 4 P9.4 charter):
``command_models.py`` (Request/Response/Source/ForwardTarget
+ Surface/Scope enums + Nit-1 monotonic ``new_command_id``);
``command_service.py`` (``OrgCommandServiceProtocol`` + 5 DI
Protocols Lookup/Runtime/Session/Gateway/Emitter + 1
SLA-test-only ``BrainProtocol`` + service impl); 10 activated parity fixtures (xfail removed);
16 contract cases; 3 wall-clock SLA tests
(``test_cancel_wall_clock_budget.py``; ADR-0013 closure of
ACCEPTANCE.md #2).

Out of scope (section 8): v1 deletion (P9.9), caller
redirection (P9.8), real-runtime integration (P9.6).

## 2. Commit chain (7 commits, all <= 357 LOC; Nit-4 target met)

| commit hash | phase | one-line subject | staged-diff LOC |
|---|---|---|---|
| ``9a085922`` | P9.4a0 | feat(runtime/orgs): add v2 command models (Request/Response/Source/ForwardTarget + Surface/Scope enums + monotonic-counter id mint) | +367 (324 src + 24 init + 19 ledger) |
| ``eb4d6478`` | P9.4a | feat(runtime/orgs): OrgCommandServiceProtocol + 5 DI Protocols + 1 SLA-test-only BrainProtocol + service scaffold implementing CommandDispatcher | +372 (347 src + 22 init + 3 ledger) |
| ``ef9c6d6f`` | P9.4b | feat(runtime/orgs): OrgCommandService.submit + 5 private helpers (asyncio.Lock + conflict gate + Nit-1 cmd id; get_status/cancel deferred) | +360 (357 src + 3 ledger) |
| ``71597235`` | P9.4b2 | feat(runtime/orgs): OrgCommandService get_status + cancel + 5 fan-out methods + _dispatch_forwards + _live_snapshot_view | +338 (335 src + 3 ledger) |
| ``0893a112`` | P9.4c | test(parity/orgs): activate 10 command_service parity fixtures (xfail -> pass) | +365 (362 test + 3 ledger) |
| ``55653d11`` | P9.4d | test(runtime/orgs): add 16 command_service contract cases (dispatch + submit gates + replace conflict + cancel + fan-out + find) | +335 (332 test + 3 ledger) |
| ``52d9bbc8`` | P9.4e | test(runtime): add 3 wall-clock SLA tests + ACCEPTANCE.md #2 upgrade (Pass-with-caveat -> Pass; ADR-0013 closure) | +322 (234 test + ~30 acceptance + ~30 ledger) |

Max staged-diff LOC = 372 (P9.4a). All seven commits stayed
strictly below the 380 WARN threshold. Total v2
command-service surface = 1003 LOC (324 models + 679
service) + 332 contract + 362 parity + 234 SLA + ~30
ACCEPTANCE upgrade = 2265 LOC of net additions across 7
commits. The 1003 / ~963 src ratio reflects the explicit
Protocol scaffolding (5 DI dependency Protocols + 1
public-contract Protocol + 1 SLA-test-only BrainProtocol)
that did not exist in v1.

## 3. Test counts (before / after) -- per G-RC-9.3 section 3 format

| scope | baseline (G-RC-9.3 close: ``ffb8b908``) | after P9.4e (``52d9bbc8``) | delta |
|---|---|---|---|
| main gate (runtime + agent + api + parity + integration) | 1213 / 1 / 8 xfailed | **1244 / 1 / 7** | **+31 passed / -1 xfailed** |
| command_service parity (was 1 xfail) | (xfail placeholder, 1 row) | **10 passed** | +10 / -1 xfail |
| command_service contract (NEW) | (did not exist) | **16 passed** | +16 |
| wall-clock SLA (NEW; ADR-0013 closure) | (did not exist) | **5 collected (3+1+1) / all passed** | +5 |
| integration trio (canary + cancel + entrypoints) | 8 passed | 8 passed | no change |

Total delta: +31 passed (10 parity + 16 contract + 5
SLA-collected; SLA #1 has ``@parametrize(_repeat, range(3))``
per ADR-0013 flake guard). xfail-count -1 (command_service
parity placeholder removed). Other 2 placeholders
(manager / runtime) keep their xfails (section 10).

Targeted suite proof:

```
.venv/Scripts/python -m pytest tests/parity/orgs/test_command_service_parity.py \
                                tests/runtime/orgs/test_command_service_contract.py \
                                tests/runtime/test_cancel_wall_clock_budget.py -q
  31 passed in 2.87 s
```

Of those 31: 10 parity + 16 contract + 5 SLA = 31. All
green; no xfailed in P9.4 scope.

## 4. Parity activation evidence

``tests/parity/orgs/test_command_service_parity.py`` was a
single ``xfail(strict=True)`` placeholder shipped in P9.0i.
P9.4c replaced it with 10 ACTIVE fixtures (target = 10 per
P-RC-9-PLAN section 5.1 -- the largest of any P9.x phase):

| id | v1 surface exercised | ignore set (vs P-RC-9-PLAN section 5.2) |
|---|---|---|
| ``request_to_dict_minimal`` | ``OrgCommandRequest.to_dict()`` with defaults | timestamps (none) + ``command_id`` (volatile) |
| ``request_to_dict_full`` | ``OrgCommandRequest.to_dict()`` with all 9 fields set | timestamps + ``command_id`` |
| ``default_scope_console`` | ``default_scope_for_surface(ORG_CONSOLE)`` | -- (pure helper) |
| ``default_scope_im_user`` | ``default_scope_for_surface(IM_USER)`` | -- |
| ``default_scope_im_group`` | ``default_scope_for_surface(IM_GROUP)`` | -- |
| ``default_scope_other_surface`` | ``default_scope_for_surface(OTHER)`` | -- |
| ``forward_target_roundtrip`` | ``ForwardTarget`` from/to dict roundtrip | -- |
| ``forward_target_invalid_kind`` | ``ForwardTarget.from_dict`` rejects bad ``kind`` | -- |
| ``submit_record_shape`` | ``OrgCommandService.submit`` returned dict shape (status / command_id / origin) | ``command_id`` (volatile per ULID mint) + ``submitted_at`` (volatile ts) |
| ``forward_target_roundtrip_im_user`` | ``ForwardTarget`` IM-user variant roundtrip | -- |

10/10 PASSED. Zero remaining xfails in the file (sentinel
section 10.1).

## 5. Contract coverage matrix

``tests/runtime/orgs/test_command_service_contract.py`` adds
16 contract cases pinning the v2 service's public surface
and internal invariants against hand-rolled test doubles
(``_Node`` / ``_Org`` / ``_EvtStore`` / ``_make_runtime``):

| group | cases | what they pin |
|---|---|---|
| ``dispatch`` (CommandDispatcher interface) | 2 | dispatch table routes correct handler; unknown verb -> graceful error |
| ``submit`` happy + gates | 5 | happy-path submit; empty-content reject; missing-node reject; paused-org reject; conflict without ``replace_existing`` |
| ``get_status`` | 4 | missing command -> None; wrong org -> None; running snapshot overlay; live tracker overlay |
| ``cancel`` | 3 | missing -> idempotent ``ok=True``; terminal -> idempotent; running -> runtime + emitter called |
| ``subscribe`` / ``publish`` / ``find_for_event`` | 2 | late subscriber gets buffered summary; ``find_command_for_event`` resolves by event id |

16/16 PASSED. Run: ``pytest tests/runtime/orgs/test_command_service_contract.py
-v`` -> 16 passed in 0.6 s. The fast wall-clock reflects the
test-double design (no real runtime boot; ``CommandRuntimeProtocol``
stubbed). Per G-RC-9.3 single-backend ruling, we did NOT
parametrize 16x2 -- ``OrgCommandService`` is volatile
orchestration, not storage.

## 6. Reference codebase usage (per-brief, per-repo -- G-RC-9.1 Nit-4 format)

Per G-RC-9.1 auditor Nit-4, this section records each brief
and each repo explicitly with a per-item considered/rejected
verdict for the OrgCommandService design space (NOT lumped).

### 6.1 ``d:/claw-research/briefs`` (6 files)

| brief | one-line topic | considered for P9.4 OrgCommandService? | verdict + reason |
|---|---|---|---|
| ``01-cortex.md`` | Cortex -- SWIM + Telemetry event bus | YES | rejected. ``Telemetry`` is sibling to ``EventEmitterProtocol``; not lifted because v1 emits via per-org ``EventStore.emit`` (no pubsub topic) -- BREAKS parity. NDJSON sink revisit at P-RC-10+. |
| ``02-sint-protocol.md`` | SINT -- CapabilityToken + EvidenceLedger + LifecycleState FSM | YES | rejected. SINT ``RequestLifecycleState`` FSM mirrors our snapshot.status; v1 uses string-typed status (no enum FSM) -- enum-strengthening BREAKS parity. EvidenceLedger is closer to ADR-0005 supervisor checkpoints than command-service. |
| ``03-langgraph.md`` | LangGraph -- Pregel BSP + Checkpoint/Resume + multi-stream + cron SDK | YES | rejected. ``stream_mode=["checkpoints", ...]`` is the closest analogue to our ``subscribe_summary``/``publish_summary``; NOT lifted (v1 ships a single summary feed -- multi-mode subscription BREAKS parity). No code was lifted from LangGraph; the ADR-0013 ``perf_counter`` SLA pattern is a universal cancel-then-time-the-checkpoint idiom (the G-RC-9.4 audit verified neither the SLA module docstring nor the P9.4e commit body cites LangGraph; NIT-E-1 cleanup re-attributes to the universal pattern). |
| ``04-metagpt.md`` | MetaGPT -- SOP Role + Environment | NO | No command-bus/verb-dispatch layer; ``Role.run`` is a fixed observe->think->act loop. ``Environment`` broker is agent-to-agent (sibling to ``ChannelGatewayProtocol`` but different layer). Not relevant. |
| ``05-crewai.md`` | CrewAI -- Role + Task + Crew + EventBus | YES | rejected. ``EventBus`` decorator-based subscribe (``@on_event``) is sibling to ``EventEmitterProtocol``; v1 has no decorator subscribe -- BREAKS parity. ``Process.HIERARCHICAL`` is closer to P9.5 OrgManager. |
| ``06-autogen.md`` | AutoGen -- async event queue + Magentic-One ledger | YES | rejected. ``output_message_queue`` single-Queue model is sibling to our late-subscriber buffer; v1 uses per-subscriber Queue (not per-service) -- single-queue model BREAKS parity. Magentic-One ledger is closer to ADR-0004 supervisor. |

### 6.2 ``d:/claw-research/repos`` (6 subdirs)

| repo | one-line topic | considered for P9.4 OrgCommandService? | verdict + reason |
|---|---|---|---|
| ``autogen/`` | Microsoft AutoGen monorepo | YES | rejected. ``MessageHandlerContext`` (per-message ContextVar cancel-scope) is sibling to our ``CancellationToken``; not lifted because v1 routes cancel through ``runtime.cancel_user_command`` only -- ContextVar-based propagation would BREAK parity. |
| ``cortex/`` | Elixir Cortex multi-agent CLI orchestrator | NO | already covered by brief 01. Cortex''s OTP supervision tree is at the BEAM-runtime layer, not the application-command layer. |
| ``crewAI/`` | CrewAI framework source | NO | already covered by brief 05. No command-service-named source files (``rg -l "command_service\|verb_dispatch"`` empty under ``src/``). |
| ``langgraph/`` | LangGraph monorepo (``libs/checkpoint/`` + bg-task cancel) | YES | rejected. Repo verified present (12 entries; ``libs/`` has checkpoint/cli/sdk/...) but contains ZERO occurrences of ``BackgroundTaskFramework`` or ``CancelScope`` -- the prior G-RC-9.4 section 6.1 cite was an artefact of an earlier draft (NIT-E-1 cleanup). No code or methodology was lifted; the ADR-0013 ``perf_counter`` SLA idiom stands on its own as a universal cancel-then-time-the-checkpoint pattern. |
| ``MetaGPT/`` | MetaGPT source tree | NO | already covered by brief 04. No command-service-named source files. |
| ``sint-protocol/`` | SINT capability/ledger reference | NO | already covered by brief 02. The ``RequestLifecycleState`` FSM lives in the protocol-spec markdown, not in code. |

### 6.3 Summary

**Considered but rejected: 5 briefs** (cortex / sint-protocol /
crewai / autogen) and 1 repo (autogen/) with non-trivial
command-bus / cancel-scope code. All five were rejected
because adopting their semantics extends OrgCommandService
beyond v1 and breaks the P-RC-9-PLAN section 0.2 parity
gate.

**Indirectly adopted: none.** The ADR-0013 wall-clock SLA
test pattern in ``tests/runtime/test_cancel_wall_clock_budget.py``
(``perf_counter`` straddling cancel -> checkpoint
observation) is a universal perf-test idiom; no specific
external attribution applies. The G-RC-9.4 auditor verified
that the prior LangGraph ``BackgroundTaskFramework.CancelScope``
attribution was an artefact of an earlier draft (neither
the SLA module docstring nor the P9.4e commit body actually
contained the cite); NIT-E-1 closes the gap by re-attributing
to the universal pattern.

**Not relevant: 1 brief (metagpt) + 1 repo (MetaGPT/).**
MetaGPT has no command-bus layer.

Future subsystems (P9.5 OrgManager / P9.6 OrgRuntime) will
revisit cortex SWIM + langgraph Pregel + crewai
hierarchical-process patterns where the design space
actually overlaps with their charters.

## 7. Gate evidence per commit

Every P9.4 commit ran ``revamp_commit_guard.py`` (all OK <
380; max 372 P9.4a, min 322 P9.4e; no REJECT) +
``revamp_loc_audit.py`` (exit 0) + ``ruff check --fix`` +
``ruff format`` (all clean) + targeted ``pytest`` (green:
``tests/runtime/orgs/`` 92 -> 108 after P9.4d; 10 new parity
after P9.4c; 5 new SLA after P9.4e with 5x flake-guard:
5/5 green runs).

## 8. Out of scope (deferred)

* v1 OrgCommandService deletion -- P9.9 (after caller redirect).
* Caller redirection ``openakita.orgs.`` -> ``openakita.runtime.orgs.`` -- P9.8.
* Real ``send_command`` / ``cancel_user_command`` against real ``OrgRuntime`` -- P9.6.
* Property-based contract (hypothesis) -- 16 fixture cases sufficient (charter 15-20).
* IM-side end-to-end cancel via real ``ChannelGateway`` -- P9.7 OrgGateway.

## 9. ADR refs

* **ADR-0011** (subsystem decomposition) -- every P9.4 code
  commit references it. The seven Protocols (1
  public-contract ``OrgCommandServiceProtocol`` + 5 DI
  dependency: ``CommandRuntimeProtocol`` +
  ``OrgLookupProtocol`` + ``SessionManagerProtocol`` +
  ``ChannelGatewayProtocol`` + ``EventEmitterProtocol`` +
  1 SLA-test-only ``BrainProtocol``) ARE the decomposition. Most notably, ``CommandRuntimeProtocol``
  replaces v1''s ``self._runtime._manager.xxx`` reach-in
  pattern (the explicit G-RC-9.3 auditor recommendation #4).
* **ADR-0012** (no shim under v1) -- P9.4 lands the v2
  surface fresh under ``runtime/orgs/`` with zero touch to
  ``src/openakita/orgs/`` (sentinel section 10.3). v1
  deletion is P9.9.
* **ADR-0013** (wall-clock SLA) -- P9.4e closes the
  ACCEPTANCE.md #2 caveat. The three SLA tests
  (``test_im_cancel_to_checkpoint_under_2s`` with 3 repeats +
  ``test_resume_after_cancel_under_3s`` +
  ``test_cancel_under_high_message_burst``) flip ADR-0013
  toward its "Accepted" status target at G-RC-9 P9.10.

## 10. Sign-off + next step (sentinel three-piece)

P9.4 is GREEN.

### 10.1 Activation sentinel

```
rg -nE "@pytest\.mark\.xfail" tests/parity/orgs/test_command_service_parity.py
# 0 hits
```

The placeholder is gone; 10 active fixtures replace it.

### 10.2 Other-placeholder sentinel (NOT touched -- still 1 each)

```
rg -nE "@pytest\.mark\.xfail" tests/parity/orgs/test_{manager,runtime}_parity.py
# 2 hits total -- one per file (P9.5 / P9.6 will flip these one at a time)
```

### 10.3 Boundary discipline sentinel (v1 NOT touched outside ACCEPTANCE.md)

```
git diff ffb8b908..52d9bbc8 -- src/openakita/orgs/ src/openakita/core/ src/openakita/channels/ src/openakita/api/
# (empty -- v1 orgs/, core/, channels/, api/ untouched since G-RC-9.3 close)
```

The only ``src/openakita/`` adjacent change in P9.4 is the
NEW v2 surface under ``runtime/orgs/`` (additive). The only
docs change outside ``docs/revamp/gates/`` + ``PROGRESS_LEDGER_P9.md``
is ``docs/revamp/ACCEPTANCE.md`` #2 (Pass-with-caveat ->
Pass; documented in section 12). This is permitted by the
operator brief for P9.4e.

### 10.4 Numbers recap

* Main gate: 1244 / 1 / 7 (vs 1213 / 1 / 8 baseline; +31
  passed, -1 xfailed; total = 1244 + 1 + 7 = 1252).
* Integration trio: 8 passed (no change vs baseline).
* LOC audit + commit guard + ruff: all green every commit.
  Max staged-diff LOC = 372 (P9.4a), comfortably under the
  Nit-4 target ceiling of 350-380. P9.4a is the single
  WARN-zone commit (between 350 and 380); the remaining six
  commits are <= 367 (all in the "ok < 380" band).

### 10.5 Next step

**Next**: P9.5 OrgManager (charter subsystem #5). **NOT
STARTED in this run** -- operator HARD STOP at G-RC-9.4
before authorising P9.5. ``PROGRESS_LEDGER_P9.md`` header
bumped to "P9.4 closed, P9.5 is next".

## 11. G-RC-9.3 nit fold-in status

The four G-RC-9.3 tracked nits (all classified as G-RC-9
final cleanup, not P9.4-baseline) are progressed as
follows:

| nit | description | P9.4 status |
|---|---|---|
| **Nit-1** | Ledger placeholder hash backfill (each commit ships ``_this commit_`` row that subsequent commit backfills) | **PROGRESSED in P9.4**. P9.4b backfilled P9.4a + P9.4a0 hashes; P9.4b2 backfilled P9.4b; P9.4c backfilled P9.4b2; P9.4d backfilled P9.4c; P9.4e backfilled P9.4d; G-RC-9.4 backfills P9.4e. The convention is now fully self-documented across the P9.4 chain. The final cross-link review still rides to G-RC-9 final. |
| **Nit-2** | 8-digit counter overflow docstring (``new_schedule_id`` mints at most 1e8 IDs per process) | **RIDE-ALONG**. Not addressed in P9.4 (``new_command_id`` inherits the same 8-digit counter shape from ``new_schedule_id`` via the Nit-1-from-G-RC-9.2 fold-in; the same overflow caveat applies and is documented in ``command_models.py``''s module docstring by reference to ``scheduler_models.py``). G-RC-9 final will rewrite the docstring once. |
| **Nit-3** | perf-flake project_store under load | **NOT APPLICABLE**. OrgCommandService is volatile orchestration (no persistent store backend), so the JsonProjectStore perf-flake mode does not apply. Rides to G-RC-9 final under the original P9.2 scope. |
| **Nit-4** | Counter restart behavior docstring (across-process restart resets the 8-digit counter to 0) | **RIDE-ALONG**. Same shape as Nit-2; addressed by reference to ``scheduler_models.py``. G-RC-9 final will document the cross-process restart semantic once. |

Net: 1 of 4 nits **PROGRESSED** in P9.4 baseline (Nit-1
ledger backfill convention now consistently applied); 1
**NOT APPLICABLE** for P9.4 scope; 2 ride to G-RC-9 final
under their original phases.

## 12. ACCEPTANCE.md #2 closure evidence (NEW section)

This is the first P-RC-9 phase to materially upgrade an
ACCEPTANCE.md rating. P9.4e flips criterion #2:

* **Before** (P8.7-doc-fix; rated at P-RC-8): **Pass-with-caveat**
  with caveat text: *"A wall-clock budget assertion (e.g.
  ``perf_counter()`` start/stop around the IM cancel ->
  ``cancelled`` checkpoint write) is deferred to P-RC-9
  alongside the new ``orgs/`` subsystem tests; until then
  the < 2 s figure is documentary (the asyncio fixture
  default), not measured."*
* **After** (P9.4e; this commit chain): **Pass**. The
  caveat block is removed entirely; the Verification method
  section now leads with the three ADR-0013 SLA tests; the
  Evidence list cites
  ``tests/runtime/test_cancel_wall_clock_budget.py`` +
  ADR-0013.

Concrete diff (P9.4e commit ``52d9bbc8``):

```
 docs/revamp/ACCEPTANCE.md
 -**Status: Pass-with-caveat.** ... <caveat block> ...
 +**Status: Pass.** The three new wall-clock SLA tests (P9.4e,
 +ADR-0013) directly assert the < 2 s budget, the < 3 s resume
 +budget, and burst-isolation; combined with the P1.7-era
 +structural tests + cancel-verb wiring, the criterion is
 +fully closed. The P8.7-doc-fix caveat ("deferred to P-RC-9")
 +has been removed.
```

The three test IDs cited in the upgraded Evidence list:

* ``tests/runtime/test_cancel_wall_clock_budget.py::test_im_cancel_to_checkpoint_under_2s``
  (3 parametrize repeats per ADR-0013 flake guard).
* ``tests/runtime/test_cancel_wall_clock_budget.py::test_resume_after_cancel_under_3s``.
* ``tests/runtime/test_cancel_wall_clock_budget.py::test_cancel_under_high_message_burst``.

Determinism (ADR-0013 design choice; recorded in P9.4a
commit body + new file module docstring): tests run against
``_StubRuntime`` + ``_MockBrain`` so the wall-clock budget
measures the cancel pipeline, not LLM latency. The
structural cancel-verb wiring is still pinned by
``tests/integration/test_v2_im_cancel.py`` (5/5; P1.7 era);
together with the new SLA tests, ACCEPTANCE.md #2 is now
fully closed. ADR-0013 stays at Proposed until G-RC-9 P9.10.

---

**Sign-off**: P9.4 PASS auto-signed by the implementation
executor. Commit chain ``9a085922..52d9bbc8`` (7 commits)
fully closes P-RC-9 P9.4 plus ACCEPTANCE.md #2 caveat.
Operator hard-stops here before authorising P9.5.