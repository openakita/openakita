# P-RC-10 Charter -- ``runtime/orgs/`` namespace flatten + 5 deferred nits + merge-to-main

**Status: PLANNED.** Charter ratification commit;
docs-only. P-RC-10 picks up the 5 nits the G-RC-9 final
roll-up gate (``docs/revamp/gates/G-RC-9.9.md`` section
3) deferred, completes the namespace finalisation that
P-RC-9 left half-done (v1 ``src/openakita/orgs/`` is
gone but the v2 surface still lives under the
transitional ``src/openakita/runtime/orgs/`` slot), and
ratifies the v2.0.0 merge-to-main plan.

**Branch**: ``revamp/v3-orgs``. **HEAD at authorship**:
``acc7241a`` (post-P-RC-9 + 7 hygiene epics).
**Supersedes**: prior 220-LOC P-RC-10 charter (vintage
2026-05-19, archived as ``P-RC-10-CHARTER.md.archived``
alongside this commit). That draft scoped the broader
Category A/B/C ``runtime/*`` triage; this expanded
version narrows P-RC-10 to (i) the ``runtime/orgs/ ->
orgs/`` flatten + (ii) 5 deferred nits + (iii)
merge-to-main planning. The wider Category B/C
``runtime/*`` triage is deferred to a follow-on P-RC-11
candidate epic (not committed here).

## 0. Mission and exit criteria

P-RC-10 is the **closing epic of the v2.0.0 release
train**, with three axes:

* **(i) Namespace flatten** -- collapse the
  transitional ``src/openakita/runtime/orgs/*`` (25 .py
  files) back to the conventional
  ``src/openakita/orgs/*`` slot now that v1 src is gone
  (P-RC-9 P9.9 epsilon-2b ``90a7d77f``). The import
  surface returns to ``openakita.orgs.*`` for every
  downstream caller.
* **(ii) Close 5 deferred nits** -- M-2 / P9.7-B /
  epsilon-O1 / epsilon-O2 / GroupC roster carried over
  from G-RC-9.9 section 3; one mini-commit each.
* **(iii) Merge-to-main planning** -- ratify the merge
  strategy for ``revamp/v3-orgs -> main``, v2.0.0 tag
  flow, rollback window, post-merge milestone gating.

**Exit (all GREEN before G-RC-10 final mini-gate signs PASS):**

1. ``src/openakita/orgs/`` exists with 25+ .py files
   (the flattened v2 surface).
2. ``src/openakita/runtime/orgs/`` either gone
   (post-shim removal at P10.6) or contains only the
   temporary re-export shim ``__init__.py`` (one-release
   backward compat).
3. ``git grep -l 'openakita\.runtime\.orgs' -- src/openakita/ tests/ apps/ scripts/``
   returns 0 files (or the shim file only, pre-P10.6).
4. All 5 deferred nits CLOSED with commit references in
   ``PROGRESS_LEDGER_P10.md``.
5. Sentinels ACTIVE; sentinel #9 augmented (per P10.4)
   so new ``openakita.runtime.orgs.*`` imports are
   rejected outside the shim allowlist.
6. Narrow slice (parity + runtime + api + canary)
   green; v2 IM canary 3 reruns within +-5 % of the
   G-RC-9.9 1.92 s baseline; Playwright e2e green; full
   pytest green.
7. G-RC-10 final mini-gate signs PASS.
8. Merge-to-main plan ratified by operator (section 4).

**Out of scope (explicit):**

* 308 shim
  (``api/routes/_orgs_v2_legacy_redirects.py``) --
  locked to **v2.1.0** per ADR-0015 option (b);
  byte-untouched in P-RC-10.
* Category B/C ``runtime/{llm,io,context,desktop,
  guardrail,state_graph,nodes,templates,backends}/``
  triage from archived charter section 2 -- deferred to
  P-RC-11 candidate epic.
* Wider ``runtime/`` refactors (Category A keeps under
  ``runtime/`` per archived charter section 2.1).
* Any changes to ``agent/`` or ``api/`` package layout.

## 1. Scope (MEASURED at HEAD ``acc7241a``)

### 1.1 File inventory -- ``src/openakita/runtime/orgs/`` (25 .py)

```
__init__.py                  _runtime_event_store.py      memory_models.py
_org_layout.py               _runtime_lifecycle.py        node_scheduler.py
_runtime_agent_pipeline.py   _runtime_node_lifecycle.py   org_models.py
_runtime_dispatch.py         _runtime_plugin_assets.py    project_models.py
_runtime_event_bus.py        _runtime_templates.py        project_store.py
_runtime_watchdog.py         _slug.py                     runtime.py
blackboard.py                command_models.py            scheduler_models.py
command_service.py           manager.py                   sqlite_store.py
store.py
```

(Verbatim from ``Get-ChildItem src/openakita/runtime/orgs -File``.)
P10.1 is a one-directory rename
(``git mv src/openakita/runtime/orgs src/openakita/orgs``);
no shard split or content re-layout (M-2 sub-cap rebalance
rides P10.5).

### 1.2 Import-site inventory -- 71 files / 157 occurrences

``git grep -c 'openakita\.runtime\.orgs' -- src/ apps/ tests/ scripts/``
at HEAD ``acc7241a``:

| directory             | files | typical caller |
|---|--:|---|
| ``src/openakita/``      | 21    | api/routes/orgs_v2*, channels/gateway, server, runtime/channel_routing, self-refs inside runtime/orgs/ |
| ``tests/runtime/``      | 18    | runtime/orgs/ contract tests |
| ``tests/api/``          | 11    | contract tests for orgs_v2 endpoints |
| ``tests/parity/``       | 8     | 9 sentinel files + parity tooling |
| ``tests/unit/``         | 6     | scattered unit tests |
| ``tests/integration/``  | 3     | v2 IM canary + integration smokes |
| ``tests/e2e/``          | 1     | one e2e harness |
| ``scripts/``            | 3     | migrate_orgs_to_v2 + migrate_non_ascii_template_ids + migrate_orgs_v2_json_to_sqlite |
| ``apps/``               | 0     | frontend is pure HTTP; no Python imports |
| **TOTAL**               | **71** | **157 line occurrences** |

~2.2 hits per file on average; clusters:
``api/routes/orgs_v2_runtime_orgs.py`` 7,
``api/routes/orgs_v2_runtime_projects.py`` 6,
``channels/gateway.py`` 6, ``api/server.py`` 4,
``runtime/orgs/manager.py`` 4.

### 1.3 Five deferred nits from G-RC-9.9 section 3

* **M-2 -- ADR-0014 sub-cap breach** (from G-RC-9.6).
  ``_runtime_agent_pipeline.py`` (521 LOC) and
  ``_runtime_plugin_assets.py`` (564 LOC) exceed the
  per-shard soft cap. P10.5a splits each into 2 sibling
  shards. Net LOC ~0 (pure splitting).
* **P9.7-B -- contract files over 350 LOC** (from
  G-RC-9.7). 2 files in ``tests/api/contracts/`` exceed
  the soft cap by 30-45 LOC. P10.5b extracts shared
  fixtures into a ``_shared_contract_fixtures.py``
  helper. Net LOC ~0 (extraction).
* **epsilon-O1 -- ``test_plan_features`` 73 cases not
  re-enumerated** (from G-RC-9.9 delta-1 audit).
  OPTIONAL severity. P10.5c adds 5-10 strategic v2
  contract cases targeting high-value scenarios
  (state-machine edges + cancel-during-plan) rather
  than literal scenario recovery.
* **epsilon-O2 -- ``test_org_*_fix`` regression-pins
  not re-enumerated** (from G-RC-9.9 delta-1 audit).
  OPTIONAL severity. P10.5d records a "monitor and
  back-fill on regression" disposition without porting
  cases unless a real regression surfaces.
* **GroupC -- 3 stale v1 HTTP literals in
  ``OrgEditorView.tsx``** (from G-RC-9.8). Three
  ``/api/orgs/...`` paths in the frontend OrgEditorView
  component point to v1 routes that P9.9 epsilon-2a
  (``857a5a35``) made 404. Sentinel #8 allowlist holds
  the line. P10.5e either (a) deletes the dead UI code
  paths, or (b) repoints them to ``/api/v2/orgs/...``
  equivalents.

## 2. Sub-phase breakdown (P10.0 .. P10.7 -- 8 mini-gates)

Mirrors the P-RC-9 cadence (P9.0 .. P9.9 -> G-RC-9.0 ..
G-RC-9.9). Each sub-phase gets its own mini-gate doc
under ``docs/revamp/gates/G-RC-10.x.md``.

* **P10.0 -- Charter + recon + import-sweep inventory.**
  This commit (charter overwrite + new ledger) =
  P10.0a; P10.0b mints a recon doc (``P-RC-10-RECON.md``)
  enumerating the 71 import sites with per-site rewrite
  category (mechanical / manual / non-trivial).
  Mini-gate ``G-RC-10.0.md``.
* **P10.1 -- Atomic ``git mv``.** Physical directory
  rename ``src/openakita/runtime/orgs ->
  src/openakita/orgs``. 25 files relocated; re-seed
  ``src/openakita/orgs/__init__.py`` (verbatim copy of
  runtime/orgs ``__init__.py``); re-seed
  ``src/openakita/runtime/__init__.py`` to drop the
  ``orgs`` re-export. Net +0 + ~30 LOC __init__
  shuffle. Mini-gate ``G-RC-10.1.md``.
* **P10.2 -- Temporary re-export shim.** Add
  ``src/openakita/runtime/orgs/__init__.py`` (<= 30
  LOC) doing ``from openakita.orgs import *`` plus a
  ``DeprecationWarning`` on first import. Keeps any
  third-party plugin code that pinned the transitional
  path working for one release window. Sentinel #9
  augment at P10.4 allowlists this single shim file.
  Mini-gate ``G-RC-10.2.md``.
* **P10.3 -- Sweep 71 in-tree import sites.** 157
  occurrences across 71 files. Mechanical rewrites via
  deterministic ``re.sub`` driven from
  ``tmp_p10/_p10_sweep.py`` (not committed). Chunked
  into 6-8 sub-commits by directory cluster (P10.3a
  api / P10.3b channels+server / P10.3c runtime/orgs
  self-refs / P10.3d tests/runtime / P10.3e
  tests/api+parity / P10.3f tests/unit+integration+e2e
  / P10.3g scripts). Each sub-commit <= 30 file edits,
  <= 200 LOC delta. Mini-gate ``G-RC-10.3.md``.
* **P10.4 -- Sentinel #9 augment.** Extends
  ``test_v1_src_retired_sentinel.py`` (or splits into
  ``test_namespace_flatten_sentinel.py`` -- decision at
  P10.4a) with a 3rd assertion: zero
  ``openakita.runtime.orgs.*`` import sites outside the
  shim allowlist. Strict line-anchored regex same shape
  as the v1 sentinel. Becomes the **10th sentinel** if
  split, or sentinel #9's 3rd case if extended in
  place. Mini-gate ``G-RC-10.4.md``.
* **P10.5 -- Close 5 deferred nits.** One mini-commit
  per nit (5 + 1 ledger close = 6 total): P10.5a M-2
  shard split (~0 net); P10.5b P9.7-B fixture extract
  (~0 net); P10.5c epsilon-O1 +5-10 v2 contract cases
  (~+200-300); P10.5d epsilon-O2 monitor-disposition
  (~+20 ledger only); P10.5e GroupC frontend
  repoint/delete (~+10/-30); P10.5f roster sign-off.
  Rolled-up ``G-RC-10.5.md`` or per-step sub-gates.
* **P10.6 -- Remove the section P10.2 temporary shim.**
  Trigger: either (a) one full release shipped (v2.0.0
  minted, >= 7 day burn-in), or (b) zero third-party
  dependency observed at operator discretion.
  ``git rm src/openakita/runtime/orgs/__init__.py`` +
  drop sentinel-#9 allowlist entry from P10.4. Net -30
  (shim) + -2 (allowlist). Mini-gate ``G-RC-10.6.md``.
* **P10.7 -- G-RC-10 final roll-up gate + merge-to-main
  charter.** Final mini-gate ``G-RC-10.md`` (no
  decimal -- matches G-RC-9.md pattern). Rolls up P10.0
  .. P10.6, closes ACCEPTANCE.md row(s) for namespace
  finalisation, ratifies section 4 merge-to-main plan
  as ``docs/revamp/MERGE_TO_MAIN_v2.md``.

**Sub-phase LOC envelope:**

| sub-phase | est ins | est del | net |
|---|--:|--:|--:|
| P10.0 (charter + recon) | ~+800 | 0 | +800 |
| P10.1 (rename + __init__) | ~+30 | ~-30 | +0 |
| P10.2 (shim) | ~+30 | 0 | +30 |
| P10.3 (157 sweeps) | ~+200 | ~-200 | +0 |
| P10.4 (sentinel) | ~+80 | 0 | +80 |
| P10.5 (5 nits) | ~+500 | ~-100 | +400 |
| P10.6 (shim removal) | ~+5 | ~-32 | -27 |
| P10.7 (final gate + merge doc) | ~+600 | 0 | +600 |
| **P-RC-10 total** | **~+2 245** | **~-362** | **~+1 883** |

Order of magnitude smaller than P-RC-9 (~+25 000 ins /
-35 493 del); mostly mechanical.

## 3. Risk register

* **R-10-1 (MED) -- shim coexistence import ambiguity.**
  During P10.2 .. P10.6 both ``openakita.orgs.X`` and
  ``openakita.runtime.orgs.X`` resolve to the same
  class; new code could accidentally re-import via the
  old path. **Mitigation**: sentinel #9 augment at
  P10.4 rejects any new ``openakita.runtime.orgs.*``
  import outside the shim file itself.
* **R-10-2 (MED) -- 71-file sweep collides with
  in-flight branches.** If feature branches land new
  ``openakita.runtime.orgs.*`` imports during the P10.3
  window, the merge bypasses the sweep.
  **Mitigation**: P10.1 .. P10.4 land as a contiguous
  block in one working session; declare a 24 h freeze
  window on ``revamp/v3-orgs``; rebase any in-flight
  feature branches onto post-P10.4 HEAD before merging.
* **R-10-3 (LOW) -- third-party plugins import
  ``openakita.runtime.orgs.*``.** Plugin authors may
  have pinned the transitional path. **Mitigation**:
  section P10.2 shim provides one-release
  backward-compat with ``DeprecationWarning``; release
  notes document the new canonical path; P10.6
  shim-removal only triggers once observation confirms
  zero external dependency or one full release has
  elapsed.
* **R-10-4 (LOW) -- ``tests/runtime/orgs/`` directory
  needs move or path redirect.** 18 test files mirror
  the source path. After P10.1 they still test
  ``openakita.orgs.X`` correctly, but the directory
  name misleads. **Mitigation**: P10.3d sweeps imports
  in place; an optional sub-step P10.3d-bis (deferred
  to P-RC-11) physically renames
  ``tests/runtime/orgs/ -> tests/orgs/`` later.
* **R-10-5 (LOW) -- M-2 shard split exposes hidden
  global state.** Splitting could surface module-level
  singletons (caches / sentinels) that previously
  co-located. **Mitigation**: sentinel #6 (20 cases) +
  sentinel #4 (10 cases) are the regression net;
  P10.5a runs the narrow slice + canary 3x pre-commit.

## 4. Merge-to-main plan

* **Pre-merge gate**: P10.7 G-RC-10 final mini-gate
  PASS + all hygiene epics #1 -> #7 closed and merged
  onto ``revamp/v3-orgs`` (status at charter
  authorship: #1 through #7 signed in respective
  hygiene ledger; verify zero open at P10.7).
* **Strategy**: ``git merge --no-ff revamp/v3-orgs``
  from ``main``. Preserve the full P-RC-9 + P-RC-10
  epic commit graph; **DO NOT squash** -- the P9.x and
  P10.x mini-gate trails are the audit substrate the
  G-RC-9 + G-RC-10 gate docs cite by commit hash.
* **Tag flow** (option A locked unless operator
  overrides):
  - **(A, default)** Move the local ``v2.0.0`` tag from
    its current branch position to the merge commit on
    ``main``. Single canonical v2.0.0 hash; rewrites a
    local tag (not yet pushed per prior "no push" rule).
  - **(B, fallback)** Cut a fresh ``v2.0.0`` on the
    post-merge ``main`` commit; leave the dev-branch
    tag as ``v2.0.0-dev``. Cleaner git log; two hashes
    for "v2.0.0".
* **Rollback window**: keep ``revamp/v3-orgs`` alive
  for **30 days** post-merge.
  ``git revert -m 1 <merge-commit>`` restores ``main``
  in one commit if a v2.0.0 critical regression
  surfaces. After 30 days quiet, the branch may be
  deleted (recoverable via ``git reflog`` for another
  30 days).
* **Release-branch decision**: **NO** ``release/2.0.x``
  hotfix lane by default -- single forward-moving
  ``main``. Cherry-pick to ``hotfix/2.0.x`` ad-hoc if
  needed. Operator may override.
* **Post-merge milestones**:
  - **v2.0.0 burn-in** -- 7+ day operator-driven smoke
    window per P9.9 directive.
  - **v2.0.1** (if needed) -- accumulate non-critical
    hygiene + nit-fixes for one minor bump cycle.
  - **v2.1.0** -- 308 shim retirement per ADR-0015
    option (b); 3-step task list in P-RC-9 P9.9 main
    charter section 8.2; sentinel #7 OpenAPI snapshot
    is the forcing function. Kick-off conditional on
    v2.0.0 stable >= 1 week + zero open P-RC-10 nits +
    operator approval.
  - **P-RC-11 candidate** -- deferred Category B/C
    ``runtime/*`` triage from archived charter; opens
    after v2.1.0 ships.

## 5. Acceptance criteria

| # | criterion | how verified |
|--:|---|---|
| 1 | ``src/openakita/orgs/`` exists with 25+ .py files | ``Get-ChildItem`` at P10.1 close |
| 2 | ``src/openakita/runtime/orgs/`` gone or shim-only | ``Get-ChildItem`` at P10.6 |
| 3 | ``git grep -l 'openakita\.runtime\.orgs' -- src/openakita/ tests/ apps/ scripts/`` = 0 (or shim only pre-P10.6) | grep at P10.3 + P10.6 close |
| 4 | All 5 deferred nits CLOSED with commit refs | ``PROGRESS_LEDGER_P10.md`` final |
| 5 | Sentinels ACTIVE; #9 augmented per P10.4 | ``pytest tests/parity/orgs/ -q`` collect count |
| 6 | v2 IM canary 3x within +-5 % of 1.92 s baseline | ``pytest tests/integration/test_v2_im_canary_e2e.py`` x3 |
| 7 | Narrow slice green (parity+runtime+api+canary) | ``pytest tests/parity/orgs/ tests/runtime/orgs/ tests/api/ tests/integration/test_v2_im_canary_e2e.py -q`` |
| 8 | Full pytest green | ``pytest -q --tb=no`` |
| 9 | Playwright e2e green | ``cd apps/setup-center && npm run e2e`` |
| 10 | G-RC-10 final mini-gate signs PASS | ``docs/revamp/gates/G-RC-10.md`` verdict |
| 11 | Merge-to-main plan ratified by operator | ``docs/revamp/MERGE_TO_MAIN_v2.md`` signed at P10.7 |

## 6. Estimated LOC + duration

* **Total LOC delta**: ~+1 900 net (per section 2
  envelope). Volume sits in P10.0 (charter + recon) +
  P10.7 (final gate + merge doc); file moves at P10.1
  are 0 net; import sweeps at P10.3 are ~0 net
  (rewrites); 5 nits at P10.5 add ~+400.
* **Estimated commit count**: 8-15 mini-commits over
  **1-2 weeks**. P10.0: 2, P10.1: 1, P10.2: 1, P10.3:
  6-8, P10.4: 1, P10.5: 6, P10.6: 1, P10.7: 1-2.
* **Cadence reference**: similar shape to P-RC-9 P9.7
  .. P9.9 (10-18 commits over 5-10 days), but each
  commit is smaller (mechanical rewrites instead of new
  subsystem implementations).

## 7. References

* ``docs/revamp/gates/G-RC-9.md`` -- P-RC-9 final
  roll-up; section 5 enumerates the 5 deferred nits;
  section 7 cross-references this charter.
* ``docs/revamp/gates/G-RC-9.9.md`` -- P9.9 mini-gate;
  section 3 nit-roster final-disposition; section 6
  confirms "``runtime/orgs/ -> orgs/`` flattening ->
  P-RC-10".
* ``docs/revamp/P-RC-9-P9.9-epsilon-CHARTER.md`` --
  charter-style template (mission / scope / sub-phases
  / risk / acceptance / sign-off).
* ``docs/revamp/P-RC-9-P9.9-COVERAGE-AUDIT.md`` --
  audit-style template for the P10.0b recon doc.
* ``docs/adr/0011-org-subsystem-decomposition.md`` --
  6-subsystem layout that P10.1 physically relocates;
  no contract change.
* ``docs/adr/0014-orgruntime-budget-revision.md`` -- M-2
  sub-cap breach closed at P10.5a.
* ``docs/adr/0015-308-shim-retirement-governance.md`` --
  explicit OUT-OF-SCOPE boundary; P-RC-10 is
  byte-untouched for ``_orgs_v2_legacy_redirects.py``;
  v2.1.0 carries the retirement per option (b) LOCKED.
* ``docs/revamp/P-RC-9-CHARTER.md`` -- procedural
  template (deferred-work charter pattern P-RC-8 P8.4
  -> P-RC-9 -> P-RC-10).
* ``docs/revamp/P-RC-10-CHARTER.md.archived`` -- prior
  220-LOC P-RC-10 draft preserved alongside this
  commit; its broader Category A/B/C triage rides into
  the P-RC-11 candidate.
* ``docs/revamp/PROGRESS_LEDGER_P10.md`` -- sibling
  ledger to PROGRESS_LEDGER_P9; one row per P10.x
  commit.

---

**Charter ratification commit log**: P-RC-10 P10.0a --
this commit. Docs-only. Zero touch on ``src/``,
``tests/``, ``apps/``, ``scripts/``, ADRs, gate docs,
ACCEPTANCE.md, 308 shim, or sentinel files. Pre-approves
the section 2 sub-phase plan + section 4 merge-to-main
strategy; awaits operator green-light before P10.0b
recon doc opens.
