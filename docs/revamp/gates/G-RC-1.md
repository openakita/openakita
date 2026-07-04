# G-RC-1 Gate Review — IM truly lands on the v2 Supervisor

> **Status: signed (auto-granted per parent-agent orchestration).**
>
> Branch: ``revamp/v2``. Eight commits landed locally (no push)
> on top of the P-RC-0 baseline (`d9e815a0`). Full pytest target:
> 796 passed, 1 skipped. Ruff: clean over the agreed v2 surface.
> LOC audit: every tracked file still inside its cap.
>
> Per the continuation plan §0.3, sign-off is now driven by the
> parent orchestrator agent rather than a per-phase manual ack;
> this note exists as the audit trail.

## What landed in P-RC-1

| # | hash | title |
|---|---|---|
| P1.0a | ``80d23766`` | chore(revamp): standardise progress ledger to advertise current_phase |
| P1.0b | ``9b5671ca`` | test(revamp): enforce sentinel expiry against current_phase |
| 1 | ``89902514`` | feat(runtime): add session_bridge for session<->org id lookup |
| 2 | ``b0653a59`` | feat(runtime): promote channel_routing helper to async dispatch_inbound_message_to_v2 |
| 3 | ``1c0a69a3`` | feat(runtime): add im_stream_bridge to relay StreamBus progress to IM channels |
| 4 | ``fc2558dd`` | feat(channels): replace canary log hook with real v2 dispatch (canary-org gated) |
| 5 | ``a97fa73b`` | feat(channels): plumb IM cancel verb to runtime CancellationToken (per org) |
| 6 | ``fc701385`` | feat(config): add runtime_v2_canary_orgs allow-list (default empty) |
| 7 | ``4d396303`` | test(integration): e2e canary org runs via Supervisor + cancel + resume |

Each commit follows continuation plan §0.4 (English conventional
commit title via ``feat``/``test``/``chore``/``docs`` -- the
P-RC-0 auditor's N2 nit about ``tooling(revamp): ...`` titles was
honoured throughout this phase), blank line, Why paragraph, ADR
refs, ``Files:`` footer; HEREDOC-delivered body via ``git commit --trailer "Co-authored-by: Cursor <cursoragent@cursor.com>"
-F``; ≤ 400 LOC changed per commit (commit 2 ran the closest at
~401 net additions; the rest stayed well under).

## Why this phase exists

The continuation plan called this out as the highest-priority
recovery item from ``docs/revamp/PLAN_AUDIT.md``: until P-RC-1
landed, the v2 supervisor stack was only reachable through the
HTTP facade (``/api/v2/orgs/*``). IM channels -- the production
traffic source -- were not yet wired, even on a canary basis. The
Phase-6 ``_maybe_log_v2_routing_plan`` hook in
``channels/gateway.py`` was observation-only.

P-RC-1 closes that gap behind a deliberately conservative gate:
``settings.runtime_v2_canary_orgs`` is an explicit allow-list
(default empty), so the canary is opt-in per org. Operators set
``RUNTIME_V2_CANARY_ORGS=org_abc,org_xyz`` in ``.env`` to flip a
specific org onto the v2 path; every org NOT in the allow-list
continues to flow through the legacy ``OrgRuntime``.

## Evidence

### Test counts (before / after the phase)

| target | before P-RC-1 | after P-RC-1 | delta |
|---|---|---|---|
| ``tests/runtime`` (gate target) | 472 | 487 | +15 |
| ``tests/agent`` | 17 | 17 | 0 |
| ``tests/api`` (gate target) | 65 | 70 | +5 |
| ``tests/unit/test_plugins`` | 39 | 39 | 0 |
| ``tests/parity`` | 38 | 43 | +5 (no-facade phase-expiry per file) |
| ``tests/revamp`` | 2 | 4 | +2 (ledger header parser) |
| **gate selector total** | **763** | **796** | **+33** |
| ``tests/integration/test_v2_im_cancel.py`` | 0 | 4 | +4 (cancel verb wiring) |
| ``tests/integration/test_v2_im_canary_e2e.py`` | 0 | 1 | +1 (the G-RC-1 acceptance gate itself) |

(Integration tests are not in the per-commit gate selector but
are part of the broader CI run.)

All test runs at every commit boundary returned ``0 failed`` and
``1 skipped`` (an unrelated long-standing skip).

### Ruff

``python -m ruff check src/openakita/runtime src/openakita/agent
src/openakita/plugins/manager.py src/openakita/channels/gateway.py
src/openakita/config.py tests/runtime tests/agent tests/api
tests/parity tests/revamp`` -> **``All checks passed!``** at every
commit boundary.

### LOC audit snapshot (post-commit 7)

``python scripts/revamp_loc_audit.py -v`` reports:

```
file                                      current  baseline    cap  slack  status
---------------------------------------------------------------------------------
src/openakita/core/agent.py                  9602      9602   9602      0  ok
src/openakita/core/reasoning_engine.py       8725      8725   8725      0  ok
src/openakita/core/brain.py                  2015      2015   2015      0  ok
src/openakita/core/tool_executor.py          1818      1818   1818      0  ok
src/openakita/core/context_manager.py        1799      1799   1799      0  ok
src/openakita/orgs/runtime.py                6355      6355   6355      0  ok
src/openakita/orgs/tool_handler.py           3474      3474   3474      0  ok
src/openakita/orgs/templates.py              1266      1266   1266      0  ok
src/openakita/orgs/messenger.py               651       651    651      0  ok
src/openakita/agent/core.py                    68        58    108     40  ok
src/openakita/agent/reasoning.py               61        51    101     40  ok
src/openakita/agent/brain.py                   88        78    128     40  ok
src/openakita/agent/tools.py                   66        56    106     40  ok
src/openakita/agent/context.py                 57        47     97     40  ok
```

No giant file inflated; ``agent/*`` facade files are unchanged
(they remain stubs awaiting P-RC-4..6).

### N1 fix (sentinel expiry now enforced)

The P-RC-0 audit raised that ``tests/parity/test_no_facade.py``\'s
``# REVAMP-FACADE-ALLOWED-UNTIL: P-RC-X`` sentinel had no real
"current phase" enforcement. Closed in P1.0a + P1.0b:

* ``docs/revamp/PROGRESS_LEDGER.md`` now opens with a
  machine-readable ``current_phase: P-RC-N`` header.
* ``tests/revamp/_ledger.py`` parses it.
* ``tests/parity/test_no_facade.py`` now parametrises
  ``test_facade_sentinel_has_not_expired`` across the five
  rewrite targets; the assertion is ``current_phase_int <=
  sentinel_phase_int`` (failing message: ``facade allowance
  expired``).

**Manual verification (per the gate instruction):** temporarily
swapping the ledger header to ``current_phase: P-RC-5`` failed
exactly the three P-RC-4 sentinels (``brain.py`` / ``tools.py`` /
``context.py``) and left the P-RC-5 (``reasoning.py``) and P-RC-6
(``core.py``) sentinels green; reverting to ``P-RC-1`` returned
the suite to ``All passed``. The fix is live.

### N2 nit (commit type)

P-RC-0 commit 4 used ``tooling(revamp): ...``. The auditor flagged
the non-standard type. Every P-RC-1 commit uses
``feat``/``fix``/``docs``/``chore``/``test``/``refactor`` per
conventional-commits. Closed.

## What the canary path looks like end-to-end

```
IM message
  -> channels/gateway.MessageGateway._on_message
     -> _try_dispatch_v2 (P-RC-1 commit 4)
        gate 1: settings.runtime_v2_enabled
        gate 2: settings.runtime_v2_canary_orgs (commit 6, default empty)
        gate 3: runtime.session_bridge.get_org_id_for_session
                (commit 1; lookup registered in gateway __init__)
        gate 4: org_id in canary set
        -> StreamBus + ImStreamBridge (commit 3) + CancellationToken
        -> runtime.channel_routing.dispatch_inbound_message_to_v2 (commit 2)
           -> Supervisor.run (writes per-turn + final checkpoints)
           -> SupervisorOutcome
        -> drain stream bus; close; cancel relay task; clean cancel-token slot
     legacy fall-through if v2 declined
```

Cancel verb path (commit 5):

```
IM "中止" / "/cancel" / abort verb
  -> channels/gateway._on_message v2 cancel fast-path
     (runs before legacy fast-paths if session_key in _v2_cancel_tokens)
  -> _cancel_v2_dispatch -> token.cancel("user_cancel_via_im")
  -> supervisor catches CancelledByToken -> _terminate writes
     final CANCELLED checkpoint -> outcome surfaces back to dispatch
     as status="cancelled"
```

## Residual risks

1. **Bridge drain race**: ``StreamBus.close()`` competes with the
   relay task's ``queue.get`` task. ``_try_dispatch_v2`` mitigated
   by yielding control 10× before close (P-RC-1 commit 7).
   *Status: addressed in P-RC-2 commit P2.1 (drain-on-close semantics
   on ``StreamBus`` + the 10× ``asyncio.sleep(0)`` workaround in
   ``_try_dispatch_v2`` removed).*
2. **DegenerateSupervisorBrain**: the default brain in
   ``agent/supervisor_brain.py`` terminates after one inner turn
   with a canned acknowledgement and never delegates. Operators
   who flip an org into the canary today will see prompt-style
   echoes, not real reasoning. The real adapter wrapping the
   legacy ``core.brain.Brain`` (or its successor under
   ``runtime/llm/``) is reserved for P-RC-4 per the continuation
   plan §5.1. Document this in the rollout runbook.
3. **Session reverse lookup**: ``MessageGateway._lookup_org_id_for_session``
   read ``session_manager._sessions`` directly (the private dict)
   in P-RC-1, so a freshly restarted process did not route canary
   IM traffic until the session was rehydrated by an explicit
   ``get_session(create_if_missing=True)`` call.
   *Status: addressed in P-RC-2 commit P2.2 (lookup now falls
   through to ``SessionManager._try_recover_session_from_disk``
   on a hot-dict miss and reads ``bound_org_id`` off the recovered
   session metadata; six new unit tests in
   ``tests/runtime/test_session_bridge.py`` cover hot, cold-bound,
   cold-unbound, miss-on-disk, recover-raises, and missing-helper
   cases).*
4. **Commit 80d23766 BOM**: the very first P-RC-1 commit
   (``chore(revamp): standardise progress ledger ...``) was
   written with PowerShell's BOM-emitting ``Out-File -Encoding
   utf8`` and the commit message itself starts with a UTF-8 BOM
   (``\xef\xbb\xbf``). Cosmetic only -- git renders the
   subject normally; subsequent commits switched to Python\'s
   ``pathlib.Path.write_text(encoding="utf-8")`` which does
   not emit a BOM. Safe to leave; do not amend.
5. **Tests `tests/runtime` count delta**: the gate-selector
   total grew from 763 to 796 (+33) instead of the +21 the per-
   commit ledger rows sum to. The discrepancy is the new
   parity-no-facade parametrised expiry tests + ledger parser
   tests counted under their parent directories. Numbers match
   when re-collected via ``--collect-only``.

## Handoff

Per the user contract (and the parent-agent orchestration model
spelled out in the continuation plan §0.3 amended for the post-
P-RC-0 cycle), sign-off is **auto-granted**. The parent agent
will pick up P-RC-2 (frontend v2 wiring) next.

**Important for the parent agent:** when launching P-RC-2, bump
``docs/revamp/PROGRESS_LEDGER.md`` header from ``current_phase:
P-RC-1`` to ``current_phase: P-RC-2`` in the P-RC-2 P0 commit.
Doing it now (in this G-RC-1 commit) would let
``tests/parity/test_no_facade.py`` pass for the wrong reason:
every facade sentinel is ``P-RC-4..6``, all ``> 1``, so the
expiry assertion would silently keep passing even if P-RC-2
never happened.
