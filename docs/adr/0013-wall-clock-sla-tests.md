# ADR-0013 -- Wall-clock SLA tests for cancel + checkpoint

- **Status**: Proposed
- **Date**: 2026-05-19
- **Phase**: P-RC-9 P9.0h (will flip to Accepted at G-RC-9 / P9.10)
- **Decision owner**: project owner
- **Implementer**: AI agent on ``revamp/v3-orgs``

## Context

ACCEPTANCE.md criterion 2 (P-RC-8 ``709767b3``) says the
IM-cancel-to-checkpoint pipeline finishes in under 2 seconds.
P-RC-8 P8.7-doc-fix rated this **Pass-with-caveat** because the
existing 5 integration cases (``tests/integration/test_v2_im_cancel.py``)
do not assert the wall clock; the < 2 s figure is documentary
(the asyncio test fixture happens to fast-resolve the timer).
The caveat explicitly defers an explicit ``perf_counter()``
budget assertion to P-RC-9.

The v1 ``orgs/runtime.py`` has a wall-clock cancel branch
(``max_task_seconds``) the v2 ``runtime/supervisor.py`` removes
by design (ADR-0004). The deferred wall-clock budget test is
not just a paperwork closure -- it pins the contract that the
v2 cancel path is **structurally** under 2 s (cooperative
``CancellationToken`` flip + final ``ProgressLedger`` checkpoint
write) and not because some asyncio test fixture happens to
schedule the cancel inside the same event loop tick as the
checkpoint write.

P-RC-9 reshapes the cancel pipeline: the cancel verb moves from
the legacy ``channels/gateway.py`` -> ``OrgRuntime.cancel_user_command``
path to v2 ``OrgCommandService.cancel`` -> ``CancellationToken``
-> ``Supervisor.run`` final-checkpoint path. Any regression that
pushes the pipeline above 2 s in the rewrite will silently ship
unless the test pins it.

## Decision

Land **three wall-clock budget tests** in P9.4 under
``tests/runtime/test_cancel_wall_clock_budget.py``:

1. ``test_im_cancel_to_checkpoint_under_2s`` -- simulate an IM
   cancel verb on a running supervisor; assert
   ``perf_counter()`` delta from cancel-verb receipt to written
   ``cancelled`` checkpoint < 2.0 s. Three repeats with
   ``pytest.mark.parametrize`` to catch flake.
2. ``test_resume_after_cancel_under_3s`` -- after a cancel +
   a new IM message that arrives after the checkpoint is
   written, assert the resumed supervisor's first new turn
   completes within 3.0 s of the new message.
3. ``test_cancel_under_high_message_burst`` -- 10 concurrent
   IM commands in flight (each its own supervisor instance);
   cancel one; assert the cancelled one closes within 2.0 s
   and the other 9 remain unaffected (their first-turn
   completion times do not regress beyond their pre-cancel
   baseline).

The SLA values (2 s / 3 s / 2 s) are the v2 cancel contract.
They are deliberate, not best-effort: a regression that pushes
any of them above SLA blocks the commit at CI.

## Alternatives considered

**A1: Keep ACCEPTANCE.md #2 at Pass-with-caveat indefinitely.**
The asyncio fixture default happens to fast-resolve the timer
in the existing 5 integration cases; nobody has complained
about cancel slowness. Rejected because the asyncio default is
implementation-dependent and silently changes when fixtures get
reused across tests; the user-facing SLA deserves a
machine-enforced floor.

**A2: SLA values 1 s / 2 s / 1 s (tighter).** Rejected because
the test machine baseline (a CI worker with no GPU and a slow
filesystem) is not the same as a production server; the SLA
must hold on the slowest reasonable CI machine, not the fastest.
The 2 s figure from ACCEPTANCE.md is the floor; tightening it
during P-RC-9 is scope creep.

**A3: SLA values 5 s / 10 s / 5 s (looser).** Rejected because
the user-facing contract (IM cancel feels immediate) collapses
above ~3 s. The 2 s figure is the contract; the test pins it.

## Consequences

### Positive

* ACCEPTANCE.md #2 upgrades from Pass-with-caveat to Pass at
  P9.10 with a structural rather than documentary basis.
* Any future regression that adds a sync I/O call or a
  blocking ``asyncio.sleep`` to the cancel path fails the
  budget test before it lands.
* The 10-concurrent-burst case exercises the v2 supervisor's
  per-org isolation contract (each org runs in its own
  ``Supervisor`` instance with its own ``CancellationToken``).

### Negative / Accepted Cost

* The wall-clock test must be **machine-tolerant**: a CI
  worker under heavy load may take > 2 s for the cancel
  pipeline simply because the worker is starved. Mitigation:
  the test runs ``pytest --reruns 2`` (one retry on
  flake) and uses ``time.perf_counter()`` deliberately
  (not ``time.time()``) so clock jumps don't poison the
  measurement.
* The 10-concurrent-burst case may be slow on small CI
  machines (10 supervisors instantiated). Mitigation: use
  ``MockBrain`` (from ``tests/runtime/test_supervisor.py``) so
  no LLM calls happen; the supervisors are pure orchestration
  state.

## Links

* Closes: ACCEPTANCE.md criterion 2 caveat (P-RC-8 P8.7-doc-fix
  ``8ecff79f``).
* Charter: ``docs/revamp/P-RC-9-CHARTER.md`` -- the
  OrgCommandService rewrite that touches this contract.
* Plan: ``docs/revamp/P-RC-9-PLAN.md`` ?4 (P9.4 gate criteria),
  ?5.3 (wall-clock budget tests), ?8 (acceptance upgrades).
* Foundational ADRs: ADR-0004 (dual-ledger supervisor),
  ADR-0005 (checkpoint contract).
* Sibling P-RC-9 ADRs: ADR-0011 (subsystem decomposition),
  ADR-0012 (deletion strategy).

## Closure (P-RC-9 epic close, P9.9eta-2b)

- **Status**: **CLOSED-EFFECTIVE**. P-RC-9 epic CLOSED at gate
  ``e4d963e6`` (G-RC-9 final roll-up gate; eta-2a) + this commit
  (eta-2b acceptance / ADR / BOM follow-up).
- **Production use**: the ``time.perf_counter()`` wall-clock pattern
  decided here was applied across P9.x for IM canary measurements
  (3 repeats per canary by ADR-0013 convention). Baseline avg
  ~1.62 s (pre P9.9eps); post P9.9eps-2b avg ~1.64 s (delta +1.4 %,
  inside the +/- 5 % canary gate per G-RC-9.9 sec 2.5). No
  regression introduced by the v1 retirement axis (-35 493 LOC
  net).
- **SLA test preserved**: the BrainProtocol / OrgCommandService SLA
  wall-clock test (per ADR-0013 sec "Decision" -- 3 budget tests
  in ``tests/runtime/test_cancel_wall_clock_budget.py`` or
  equivalent) is preserved post-epic; the cancel-to-checkpoint
  < 2 s budget, the resume-after-cancel < 3 s budget, and the
  burst-isolation case all remain green at HEAD ``e4d963e6``.
- **Acceptance crosswalk**: ACCEPTANCE.md criterion #2 (already
  Pass at P-RC-8 P8.7) is unchanged; criteria #4 + #5 closed at
  P9.9eta-2b (see ACCEPTANCE.md "P-RC-9 epic-closure note" under
  each criterion).
- **Reference**: G-RC-9 sec 4 (ADR closure pointers).
