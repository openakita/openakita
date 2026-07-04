# OpenAkita v2.0.0-rc2 — Acceptance Criteria Verification

This document maps the **five acceptance criteria** from the original
backend revamp plan
(`openakita_full_backend_revamp_e6d8610d.plan.md` §9) to the
shipped v2 implementation on `revamp/v2`. Each criterion is rated
**Pass / Partial / Deferred-to-P-RC-9** with the concrete evidence
(test files, commit hashes, ADRs, doc references) that backs the
rating.

Source plan reference:

> §9. Acceptance criteria for v2 GA
>
> 1. AIGC video studio kickoff runs once end-to-end without
>    duplicate storyboard.
> 2. An IM-side cancel triggers a cooperative cancel and a
>    checkpoint save in < 2 seconds.
> 3. Resume after cancel continues from the last checkpoint
>    (not from scratch).
> 4. The `happyhorse-video` plugin shows up as a single multi-mode
>    WorkbenchNode (not one node per role).
> 5. All built-in templates load on first launch and a new org
>    can be created from any template with one click.

Continuation plan reference: the post-RC continuation plan
`openakita_revamp_continuation_plan_d6192647.plan.md` §9 says this
file MUST be written in P-RC-8 before the v2.0.0-rc2 tag is cut.

---

## Criterion 1 — Single end-to-end AIGC video kickoff, no duplicate storyboard

**Criterion text.** "AIGC video studio kickoff runs once end-to-end
without duplicate storyboard." This is the headline regression the
v2 revamp set out to fix — the legacy ``ReasoningEngine`` would
wall-clock-cancel a long-running storyboard step and re-delegate
the same sub-task, producing duplicate work.

**Verification method.**

* **Regression test** —
  `tests/runtime/test_stall_detector.py::test_regression_long_progressing_storyboard_does_not_replan`
  encodes the duplicate-storyboard root cause: a progressing long
  step where the LLM says ``is_progress_being_made=true`` resets
  the stall counter and the supervisor does NOT replan/re-delegate.
* **Module-level proof** — the v2 ``Supervisor.run()`` decision
  table (`runtime/supervisor.py`) has no ``max_task_seconds``
  branch; the legacy cancel-on-wall-clock path has no v2
  equivalent.
* **End-to-end canary** —
  `tests/integration/test_v2_im_canary_e2e.py` runs a canary org
  through `Supervisor` -> ledger -> ProgressLedger -> stream and
  asserts no duplicate ``task_started`` events for the same
  ``task_id`` in a single run.
* **Manual smoke** — the canary org is the AIGC studio template
  instantiated via ``runtime.templates.aigc_video_studio`` and
  exercised through the IM gateway; the seven-node
  Producer / Screenwriter / Art Director / WB image / WB video /
  WB human / WB stitch graph cannot reach the duplicate-storyboard
  regression by construction because (a) the supervisor's stall
  detector regenerates the counter on progressing turns, (b)
  cancels save a final checkpoint and resume rewinds from it.

**Status: Pass.** The regression test + the structural absence of
the legacy wall-clock cancel branch + canary E2E all back the
rating. The proof is mechanical (no max_task_seconds in v2
supervisor) rather than statistical (1 000 production runs without
a duplicate), so technically the rating could be **Pass-with-caveat
"awaiting production burn-in numbers"** — but the algorithmic
contract is closed and the structural test pins it.

**Evidence.**

* `tests/runtime/test_stall_detector.py::test_regression_long_progressing_storyboard_does_not_replan`
  (P-RC-1 baseline test).
* `src/openakita/runtime/supervisor.py` (no `max_task_seconds`
  branch in the decision table; ledger-driven verdict only).
* `tests/integration/test_v2_im_canary_e2e.py` (commit
  `4d396303`, P1.7).
* `docs/revamp/STATUS.md` "How v2 already delivers" section.
* ADR-0004 (dual-ledger supervisor).

---

## Criterion 2 — IM cancel triggers cooperative cancel + checkpoint save < 2s

**Criterion text.** "An IM-side cancel triggers a cooperative
cancel and a checkpoint save in < 2 seconds." This pins the cancel
contract: not a process-kill but a cooperative ``CancellationToken``
flip + final ``ProgressLedger`` checkpoint.

**Verification method.**

* **Wall-clock SLA tests** (ADR-0013, P9.4e) —
  `tests/runtime/test_cancel_wall_clock_budget.py` asserts the three
  budgets directly via ``time.perf_counter()``:
  * ``test_im_cancel_to_checkpoint_under_2s`` (3 repeats per ADR-0013) —
    IM cancel verb on a running ``OrgCommandService`` writes the
    ``cancelled`` checkpoint in < 2.0 s wall-clock.
  * ``test_resume_after_cancel_under_3s`` — a new IM message
    after cancel resumes from checkpoint in < 3.0 s.
  * ``test_cancel_under_high_message_burst`` — with 10
    concurrent commands in flight, cancelling one closes that one
    in < 2.0 s and the other 9 remain unaffected (isolation).
* **Unit/contract test** —
  `tests/runtime/test_supervisor.py::test_cancel_writes_final_checkpoint`
  (and siblings) — supervisor receives cancel, writes a
  ``cancelled`` checkpoint, returns within the asyncio loop tick.
* **Integration test** —
  `tests/integration/test_v2_im_cancel.py` — 4 cases covering
  cancel-no-op (no token), cancel-with-token-raise, supervisor
  writes final cancelled checkpoint.
* **Wiring proof** — `src/openakita/channels/gateway.py` per-org
  cancel verb plumbs ``Messenger.cancel(org_id)`` ->
  ``CancellationToken.cancel()`` (P1.5 `a97fa73b`).

**Status: Pass.** The three new wall-clock SLA tests (P9.4e,
ADR-0013) directly assert the < 2 s budget, the < 3 s resume
budget, and burst-isolation; combined with the P1.7-era structural
tests + cancel-verb wiring, the criterion is fully closed. The
P8.7-doc-fix caveat ("deferred to P-RC-9") has been removed.

**Evidence.**

* `tests/runtime/test_cancel_wall_clock_budget.py`
  (3 SLA tests + 3 repeats of SLA #1; P9.4e).
* `tests/integration/test_v2_im_cancel.py` (4/4 passed; P1.7 era).
* `tests/runtime/test_supervisor.py::test_cancel_writes_final_checkpoint`.
* `src/openakita/channels/gateway.py` (cancel verb wiring).
* `src/openakita/runtime/cancel_token.py` (`CancellationToken`).
* ADR-0005 (checkpoint contract); ADR-0013 (wall-clock SLA design).

---

## Criterion 3 — Resume after cancel continues from last checkpoint

**Criterion text.** "Resume after cancel continues from the last
checkpoint (not from scratch)." Pins resume-from-checkpoint
semantics so a user who cancels and then re-engages does not
re-execute completed steps.

**Verification method.**

* **Unit test** —
  `tests/runtime/test_checkpoint.py::test_resume_picks_up_at_last_checkpoint`
  and siblings; `MemoryCheckpointer` and `SqliteCheckpointer`
  contract suites both pass.
* **Integration test** —
  `tests/integration/test_v2_im_canary_e2e.py::test_canary_org_runs_through_supervisor_and_then_cancels_and_resumes`
  — runs a canary org through Supervisor -> cancel ->
  re-dispatch and asserts the resume picks up at the last
  checkpoint via the ProgressLedger turn marker.
* **Storage contract suite** —
  `tests/runtime/orgs/test_store_contract.py` (18 cases) shares
  the resume invariants across JSON + SQLite backends.

**Status: Pass.** All four test paths are green.

**Evidence.**

* `tests/integration/test_v2_im_canary_e2e.py` (canary E2E,
  commit `4d396303`).
* `tests/runtime/test_checkpoint.py` and
  `tests/runtime/orgs/test_store_contract.py`.
* `src/openakita/runtime/checkpoint.py` +
  `runtime/backends/sqlite.py` / `runtime/backends/json_file.py`.
* ADR-0005 (checkpoint contract).

---

## Criterion 4 — `happyhorse-video` shows as single multi-mode WorkbenchNode

**Criterion text.** "The `happyhorse-video` plugin shows up as a
single multi-mode WorkbenchNode (not one node per role)." Pins the
ADR-0009 manifest contract — one plugin = one node, with role-
specific behaviour expressed as ``WorkbenchMode`` entries inside
the manifest.

**Verification method.**

* **Manifest validation** —
  `tests/test_workbench_manifest.py` parses the
  ``plugins/happyhorse-video/plugin.py`` ``WORKBENCH`` constant
  through ``runtime.nodes.manifest.parse`` and asserts a single
  manifest with four ``WorkbenchMode`` entries
  (``art_director`` / ``image_artist`` / ``video_animator`` /
  ``portrait_actor``).
* **Loader discovery** —
  `tests/unit/test_plugins/test_workbench_discovery.py` exercises
  ``plugins.manager`` end-to-end: the manager parses ``WORKBENCH``
  at load time and exposes the manifest via
  ``list_workbench_plugins()``.
* **Node behaviour** —
  `tests/runtime/test_workbench_node.py` exercises mode-scoped
  tool allow-list + explicit mode switching +
  ``workbench_ready`` / ``workbench_mode_switched`` /
  ``workbench_cancelled`` lifecycle envelopes for a single
  ``WorkbenchNode`` instance.

**Status: Pass.** All three test paths are green; the manifest
contract is structurally enforced.

**Evidence.**

* `plugins/happyhorse-video/plugin.py` (``WORKBENCH`` constant).
* `tests/test_workbench_manifest.py`.
* `tests/unit/test_plugins/test_workbench_discovery.py`.
* `tests/runtime/test_workbench_node.py`.
* `src/openakita/runtime/nodes/workbench_node.py` +
  `src/openakita/runtime/nodes/manifest.py`.
* ADR-0009 (plugin workbench manifest).

**P-RC-9 epic-closure note (P9.9eta-2b).** Status: **CLOSED**.
Additional evidence backing this criterion at P-RC-9 epic close:
satisfied by G-RC-9 sec 2; v1 surface retired in P9.9eps-2a
(``857a5a35``) + P9.9eps-2b (``90a7d77f``); v2 REST mint completed
in P9.7beta (G-RC-9.7 mini-gate ``8b0a1bbf``).

---

## Criterion 5 — All built-in templates load on first launch; one-click instantiation

**Criterion text.** "All built-in templates load on first launch
and a new org can be created from any template with one click."
Pins the ADR-0008 template registry contract plus the
``POST /api/v2/orgs/templates/{id}/instantiate`` REST surface and
the ``TemplatePickerDrawer`` UI flow.

**Verification method.**

* **Registry discovery** —
  `tests/runtime/templates/test_builtin_discovery.py` asserts
  ``discover_builtins()`` imports every non-underscore module
  under ``runtime/templates/builtin/``; every registered
  ``TemplateSpec`` validates and instantiates; the four flagship
  template ids (``aigc_video_studio`` / ``software_team`` /
  ``startup_company`` / ``content_ops``) are present.
* **REST surface** —
  `tests/api/test_orgs_v2.py` covers ``GET /api/v2/orgs/templates``
  (list), ``GET /api/v2/orgs/templates/{id}`` (one),
  ``POST /api/v2/orgs/templates/{id}/instantiate`` (mint a fresh
  ``OrgV2``). 15 cases.
* **UI flow** —
  ``apps/setup-center/src/components/TemplatePickerDrawer.tsx``
  + vitest coverage in
  ``apps/setup-center/src/components/__tests__/TemplatePickerDrawer.test.tsx``.
  The drawer reads the v2 endpoints and ``instantiate`` is wired
  to the same backend route.

**Status: CLOSED** (upgraded from Partial at P9.9eta-2b).
The deferred UI default-front-door caveat is moot: the v1 ``orgs/``
package + the v1 ``/api/orgs/`` router were physically retired in
P9.9eps-2a (``857a5a35``) and P9.9eps-2b (``90a7d77f``), so the v2
REST surface is now the only orgs front-door by construction.
Parity with the retired v1 surface is locked by the 9 / 9 sentinel
matrix (G-RC-9.9 sec 2.3; 68 collected cases) -- sentinel #9
(``test_v1_src_retired_sentinel.py``) was added in P9.9eta-1a
(``21e26467``) and pins the zero-import + zero-residue invariant.

**Evidence.**

* `tests/runtime/templates/test_builtin_discovery.py` (registry).
* `tests/api/test_orgs_v2.py` (15 cases REST).
* `src/openakita/api/routes/orgs_v2.py` (route definitions).
* `src/openakita/runtime/templates/registry.py` +
  ``runtime/templates/builtin/*.py``.
* `apps/setup-center/src/components/TemplatePickerDrawer.tsx`.
* ADR-0008 (template registry).
* Deferred caveat: `docs/revamp/P-RC-9-CHARTER.md`
  (orgs/ integral migration).

**P-RC-9 epic-closure note (P9.9eta-2b).** Status: **CLOSED**.
Evidence: satisfied by G-RC-9.9 sec 2.3 sentinel matrix
(9 / 9 ACTIVE; 68 cases) + sentinel #9 added in P9.9eta-1a
(``21e26467``); v2 REST surface live, v1 ``/api/orgs/`` retired.

---

## Summary

| # | Criterion | Status |
|---|---|---|
| 1 | AIGC video kickoff E2E no duplicate storyboard | Pass |
| 2 | IM cancel cooperative + checkpoint save < 2s | Pass |
| 3 | Resume after cancel from last checkpoint | Pass |
| 4 | happyhorse-video single multi-mode WorkbenchNode | **CLOSED** |
| 5 | Built-in templates load + one-click from any | **CLOSED** (P9.9eta-2b; v1 UI surface retired in P9.9eps) |

**5 / 5 CLOSED** is the P-RC-9 epic-closure acceptance posture
(P9.9eta-2b). Criteria #1..#3 retained their P-RC-8 Pass rating;
criterion #4 promoted to CLOSED with a P-RC-9 epic stamp
(v2 REST mint live, v1 surface retired); criterion #5 promoted
from Partial to CLOSED -- the deferred UI default-front-door
caveat is moot because the v1 ``orgs/`` package and ``/api/orgs/``
router were physically retired in P9.9eps. The original v2.0.0-rc2
P-RC-8 posture (4 Pass + 1 Partial) is preserved in the per-criterion
narrative above for historical reference.
Historical note: criterion #5's earlier Partial rating mapped to
the R-RC-7-A residual risk G-RC-7 escalated and that P-RC-9 has
now closed (see ``P-RC-9-CHARTER.md`` + G-RC-9 sec 2). The v1
``orgs/`` UI is no longer a candidate front-door because the v1
package itself is gone; the v2 REST surface is the sole orgs
front-door at HEAD.
