# P-RC-9 P9.9 Charter -- final P-RC-9 round (v1 physical deletion + import sweep; planning)

**Status: PLANNED, NOT EXECUTED.** Planning charter for P9.9,
the **ninth and final** phase of P-RC-9
(``docs/revamp/P-RC-9-PLAN.md`` sec 4 P9.9). P9.9 retires the
v1 orgs surface end-to-end: deletes ``src/openakita/orgs/``
(v1 subsystem, **26 files / 20 237 LOC** measured), deletes
``src/openakita/api/routes/orgs.py`` (v1 router, **2 533 LOC /
89 endpoints** measured), deletes ``tests/orgs/`` (v1-surface
test bank, **48 files / 12 238 LOC** measured), and sweeps
every remaining ``from openakita.orgs.X`` import onto the v2
``openakita.runtime.orgs.X`` surface shipped in P9.1-P9.6.
After G-RC-9.9 PASS, **G-RC-9 final** gate signs off the entire
P-RC-9 revamp; P-RC-10 (``runtime/`` flattening) is a separate
epic.

**Branch** ``revamp/v3-orgs``; **HEAD at authorship**
``d49388bb`` (ADR-0015 just landed; 308 shim retirement
governance, **option (b) v2.1.0 retirement LOCKED**; P9.9zeta
NO-OP per ADR-0015). **Scope**: planning artefacts only;
``git diff d49388bb..HEAD -- src/openakita/ tests/ apps/``
returns empty bytes both before and after this commit.

## 1. Scope summary -- what P9.9 does and does NOT do

### 1.1 P9.9 DOES

* Delete v1 subsystem ``src/openakita/orgs/`` (26 .py /
  20 237 LOC; sec 2.1), v1 router ``api/routes/orgs.py``
  (2 533 LOC / 89 endpoints; **hard ``git rm`` in same
  commit**, no fresh 410 shim -- P9.7's v2 mint + 308 shim
  already gave callers one v2.0.x window and P9.8 swapped
  55 frontend hits), and v1-surface tests ``tests/orgs/``
  (48 files / 12 238 LOC; deleted in delta-4 after delta-1
  cross-coverage audit per sec 2.2).
* Mechanical import sweep of every ``from openakita.orgs.X``
  literal onto ``openakita.runtime.orgs.X``. Measured at
  HEAD: **31 src + 64 tests = 95 hit files**; internal
  (orgs=8 + tests/orgs=47) vanishes with parents, so
  **external sweep sites = 23 src + 17 tests = 40 files**
  (per-bucket breakdown sec 3).
* Channels gateway swap -- ``channels/gateway.py`` (5 in-process
  imports per G-RC-9.8 sec 12); lands in **beta BEFORE epsilon**
  (R3 invariant).
* 9th "v1 deletion guard" sentinel adoption in eta-1 (sec
  7.2; recommendation Y).
* G-RC-9.9 mini-gate (eta-1) + G-RC-9 final roll-up gate
  (eta-2).

### 1.2 P9.9 does NOT

* Touch ``api/routes/_orgs_v2_legacy_redirects.py`` (the
  **308 shim**, 9 routes / 101 LOC). **Per ADR-0015 option
  (b) LOCKED** the shim is byte-level untouched through
  P9.9; physical retirement is a single v2.1.0 milestone
  task (sec 8).
* Touch the v2 subsystem ``src/openakita/runtime/orgs/``
  (21 files / ~8 200 LOC) beyond import-target reads.
  Internal flattening is P-RC-10.
* Touch the frontend beyond sentinel #8's existing
  3-Group-C allowlist. P9.8 closed the caller migration;
  if v1 router deletion makes the 3 Group C paths in
  ``OrgEditorView.tsx`` 404, that is a P-RC-10 ticket;
  sentinel #8 gets a follow-up comment only.
* File new ADRs. ADR-0015 governs the 308 shim NO-OP and
  ratifies Q_DECISIONS Q-B; sec 12 adds at most an optional
  Q-D row for the 9th sentinel.

## 2. Deletion inventory (MEASURED at HEAD ``d49388bb``)

### 2.1 v1 source modules to delete (``src/openakita/orgs/``)

**26 files / 20 237 LOC**. Top-5 by LOC (full per-file
inventory deferred to alpha-1):

| # | module | LOC | v2 home (subsystem) |
|--:|---|--:|---|
| 1 | ``runtime.py`` | 6 355 | ``runtime/orgs/runtime.py`` (359) + 4 ``_runtime_*`` slices (~1 549) |
| 2 | ``tool_handler.py`` | 3 474 | absorbed across ``runtime/orgs/_runtime_agent_pipeline`` + ``runtime/dispatch`` (P9.4c) |
| 3 | ``templates.py`` | 1 266 | ``runtime/orgs/_runtime_plugin_assets.py`` (P9.2c) |
| 4 | ``models.py`` | 1 018 | split into 4 ``runtime/orgs/{command,memory,project,scheduler}_models.py`` typed shards |
| 5 | ``command_service.py`` | 963 | ``runtime/orgs/command_service.py`` (1 017; P9.4c rewrite) |
| .. | 21 remaining files | 7 161 | alpha-1 inventory (1-to-1 + absorbed per sec 3.3) |

### 2.2 v1 test bank to delete (``tests/orgs/``)

**48 files / 12 238 LOC** total. Cross-coverage asserted
in P9.9delta-1 audit doc BEFORE any ``git rm``:

| v1 surface | v2-side replacement | overlap |
|---|---|---|
| 6 runtime / command-service / manager / node_scheduler / blackboard v1 test files (~5 000 LOC) | ``tests/runtime/orgs/`` (10 files / 2 761 LOC; per-Protocol contract) + ``tests/parity/orgs/`` (10 files / 2 535 LOC; 60 / 60 parity green) | full |
| 4 v1-path-asserting HTTP files (``test_api`` / ``test_prompt_api_e2e`` / ``test_transparency_autonomy`` / ``test_org_status_snapshot``; 56 v1 hits per G-RC-9.7 sec 5) | ``tests/api/contracts/`` (8 files / 2 052 LOC; 184 / 184 contract cases green) | full (v2 mint twins) |
| ~38 v1-internal test files (models / policies / event_router / identity / inbox / scaler / tools / tool_handler / plugin_* / etc.) | parent v1 modules disappear in epsilon; v2 absorption tests in ``tests/runtime/orgs/`` cover surviving public behaviour | full (by construction) |

Total v2-side coverage at HEAD: **28 files / 7 348 LOC**,
validated against 60 / 60 parity + 184 / 184 contract green.

### 2.3 308 shim deferral (per ADR-0015)

``_orgs_v2_legacy_redirects.py`` (9 routes / 101 LOC)
**byte-level untouched** in P9.9; retirement is a single
v2.1.0 task (sec 8).

## 3. Import sweep matrix (per-tree v1 -> v2 mapping)

Measured 2026-05-20 at HEAD ``d49388bb`` (``grep -rln
"openakita.orgs"`` per-top-dir buckets):

### 3.1 Backend src (31 files; **23 external sweep**)

| tree | files | resolution |
|---|--:|---|
| ``src/openakita/api/`` | 7 | sweep -- 1-to-1 same-name + absorption (sec 3.3) |
| ``src/openakita/channels/`` (= gateway.py) | 1 | sweep in **beta** (R3); 5 in-process imports |
| ``src/openakita/core/`` | 1 | sweep |
| ``src/openakita/runtime/`` | 14 | sweep -- v2 internals with type / re-export back-refs to v1 |
| ``src/openakita/orgs/`` | 8 | **delete with parent** (internal) |

### 3.2 Test tree (64 files; **17 external sweep**)

| tree | files | resolution |
|---|--:|---|
| ``tests/api/`` | 1 | sweep |
| ``tests/e2e/`` | 1 | sweep |
| ``tests/integration/`` | 1 | sweep |
| ``tests/parity/`` | 6 | sweep (v2 parity tests sometimes import v1 for diff; rewrite to ``openakita.runtime.orgs``) |
| ``tests/unit/`` | 8 | sweep |
| ``tests/orgs/`` | 47 | **delete with parent** in delta-4 |

### 3.3 Absorbed-not-1-to-1 mappings (per G-RC-9.6 sec 13)

Several v1 modules have **no same-name v2 file** and were
absorbed into v2 internal slices during P9.4c / P9.5c /
P9.6gamma. Sweep must target the absorbed slice:

| v1 import | absorbed-to v2 import | absorbed in |
|---|---|---|
| ``openakita.orgs.command_tracker`` | ``openakita.runtime.orgs._runtime_dispatch`` (+ ``_runtime_watchdog`` half) | P9.4c + P9.6gamma |
| ``openakita.orgs.failure_diagnoser`` | ``openakita.runtime.orgs._runtime_watchdog`` | P9.6gamma |
| ``openakita.orgs.heartbeat`` | ``openakita.runtime.orgs._runtime_watchdog`` + ``_runtime_event_bus`` | P9.6gamma |
| ``openakita.orgs.event_router`` + ``openakita.orgs.messenger`` | ``openakita.runtime.orgs._runtime_event_bus`` (+ ``command_service`` half) | P9.4c |
| ``openakita.orgs.identity`` | ``openakita.runtime.orgs.manager`` (folded into OrgManager) | P9.5c |
| ``openakita.orgs.templates`` + ``openakita.orgs.plugin_workbench_templates`` | ``openakita.runtime.orgs._runtime_plugin_assets`` | P9.2c |
| ``openakita.orgs.tool_handler`` (3 474 LOC) | absorbed across ``_runtime_agent_pipeline`` + ``runtime/dispatch`` + ``runtime/tools``; per-symbol mapping in alpha-1 | P9.4c + P9.6gamma |
| ``openakita.orgs.models`` (1 018 LOC) | split into 4 ``runtime/orgs/{command,memory,project,scheduler}_models.py`` shards | P9.1c-P9.4c |

Same-name 1-to-1 mappings (no rewrite): ``blackboard``,
``command_service``, ``manager``, ``node_scheduler``,
``project_store``, ``runtime`` (+ ``__init__`` re-exports).

## 4. Risks (R1-R6; P9.9-specific)

* **R1 -- import sweep miss -> silent ImportError (HIGH)**.
  40 external sites + 8 absorbed-not-1-to-1 cases (sec
  3.3); misses surface only at module-import time.
  **Mitigation**: alpha-1 per-file:line inventory; gamma
  / delta commits re-run grep monotonically; 9th sentinel
  asserts zero hits at G-RC-9.9.
* **R2 -- tests/orgs/ coverage loss (HIGH)**. 48 files /
  12 238 LOC vanish in delta-4. **Mitigation**: sec 2.2
  cross-coverage matrix asserted by delta-1 audit doc
  BEFORE any ``git rm``; 7 348 LOC v2-surface coverage
  validated against 60 / 60 parity + 184 / 184 contract
  green.
* **R3 -- channels gateway swap order (MEDIUM)**.
  ``channels/gateway.py`` has 5 v1 imports; if any deletion
  lands BEFORE the gateway sweep, the IM channel fails at
  import and the backend cannot start. **Mitigation**: beta
  BEFORE epsilon; beta-1 runs ``python -c "from
  openakita.channels.gateway import *"`` smoke.
* **R4 -- orphaned re-exports in ``__init__.py`` (MEDIUM)**.
  Top-level ``__init__.py`` files may carry ``from
  openakita.orgs.X import Y`` re-exports for backward-compat.
  **Mitigation**: alpha-1 lists every ``__init__.py`` hit;
  per-file decision = rewrite-to-v2 (preferred) OR
  delete-with-CHANGELOG-note.
* **R5 -- docs / AGENTS.md / README references (LOW)**.
  Stale narrative may exist. **Mitigation**: G-RC-9.9 gate
  includes a docs sweep; narrative refs become follow-up
  NITs, not release blockers.
* **R6 -- ``commit_guard`` and epsilon negative-LOC (MEDIUM)**.
  Epsilon atomic deletion ~-22 770 LOC (largest single
  commit of P-RC-9). ``scripts/commit_guard.py`` checks
  **insertions only** (verified at P9.6gamma multi-thousand
  negative absorptions; zero guard complaint).
  **Mitigation**: epsilon insertions stay <= 50 LOC (mount
  drops + ledger row); deletions unbounded. Contingency:
  split epsilon into 3 by-subsystem commits if guard trips.

## 5. Phase breakdown (alpha / beta / gamma / delta / epsilon / zeta / eta)

### 5.1 alpha -- import sweep inventory (1 commit, docs only)

**alpha-1** (~280 LOC):
``docs/revamp/P-RC-9-P9.9-IMPORT-INVENTORY.md`` -- per
file:line table of every ``openakita.orgs.X`` import (31
src + 64 tests), mapping each v1 symbol to its v2 target
(including 8 absorbed cases from sec 3.3). Mirrors P9.7
ENDPOINT-INVENTORY + P9.8 CALLER-INVENTORY format; includes
R4 ``__init__.py`` re-export audit.

### 5.2 beta -- channels gateway swap (1 commit)

**beta-1** (~50 LOC): rewrite 5
``from openakita.orgs.X`` imports in
``channels/gateway.py`` per alpha-1; smoke import +
``pytest tests/channels/ -q`` + narrow slice. **R3
invariant**: beta BEFORE epsilon.

### 5.3 gamma -- backend src sweep (3 commits)

23 external-to-v1 src files batched by subsystem:

| commit | scope | files | est ins |
|---|---|--:|--:|
| gamma-1 | ``src/openakita/api/`` (routes + server) | 7 | ~60 |
| gamma-2 | ``src/openakita/runtime/`` cross-refs | 14 | ~100 |
| gamma-3 | ``src/openakita/core/`` + 3 ``__init__.py`` (R4) | 2 + 3 | ~40 |

Each commit: narrow slice + parity/orgs + sentinels #7+#8
green; inventory grep monotonically shrinks.

### 5.4 delta -- test sweep + tests/orgs/ delete (4 commits)

| commit | scope | est ins / del |
|---|---|---|
| delta-1 | ``tests/runtime/orgs/coverage_audit.md`` (sec 2.2 cross-coverage audit; docs only) | +150 / 0 |
| delta-2 | ``tests/parity/`` (6) + ``tests/unit/`` (8) sweep | +100 / ~30 |
| delta-3 | ``tests/api/`` (1) + ``tests/e2e/`` (1) + ``tests/integration/`` (1) sweep | +40 / ~15 |
| delta-4 | ``git rm -r tests/orgs/`` (47 internal + ``__init__`` + ``conftest``) atomic | +20 ledger / **-12 238** |

### 5.5 epsilon -- physical deletion (1 atomic; contingency 3)

**epsilon-1** (default): ``git rm -r src/openakita/orgs/``
+ ``git rm api/routes/orgs.py`` + drop two mount lines from
``api/server.py`` + ledger row + sentinel #7 OpenAPI snapshot
regenerate (same commit). Insertions <= 50; deletions
**-22 770 LOC**. **Contingency** if guard trips: split into
epsilon-1a router + epsilon-1b subsystem + epsilon-1c cleanup.

### 5.6 zeta -- 308 shim NO-OP (0 commits; documented)

**Per ADR-0015 option (b) LOCKED**: zero source / test /
apps changes; documented in G-RC-9.9 sec 8 + ledger close
summary citing ADR-0015 + Q-B. v2.1.0 task list in sec 8.2.

### 5.7 eta -- G-RC-9.9 mini-gate + G-RC-9 final (2 commits)

* **eta-1** (~450 LOC): ``gates/G-RC-9.9.md`` + 9th sentinel
  ``test_v1_deletion_guard_sentinel.py`` (~120-180 LOC) +
  ledger close. Splits into eta-1a/eta-1b if needed.
* **eta-2** (~250 LOC): ``gates/G-RC-9.md`` **G-RC-9 final**
  -- closes entire P-RC-9; ACCEPTANCE.md #4 + #5; NIT
  residue; Y3 BOM.

### 5.8 Phase totals

| phase | commits | positive LOC | negative LOC |
|---|--:|--:|--:|
| alpha | 1 | ~280 | 0 |
| beta | 1 | ~50 | 0 |
| gamma | 3 | ~200 | ~50 net |
| delta | 4 | ~310 | **-12 238** |
| epsilon | 1 (or 3 fallback) | ~50 | **-22 770** |
| zeta | 0 | 0 | 0 |
| eta | 2 | ~700 | 0 |
| **Total** | **12 (or 14 fallback)** | **~1 590 pos** | **~-35 058 neg** |

**Net LOC delta vs HEAD ``d49388bb``: ~-33 500 LOC** --
the largest single phase of P-RC-9 by an order of
magnitude.

## 6. LOC budget

Positive insertions stay disciplined under the per-commit
350-LOC soft cap and ADR-0014 +/-10% tolerance:

| bucket | positive LOC |
|---|--:|
| alpha-1 inventory doc | ~280 |
| beta-1 channels swap | ~50 |
| gamma-1..3 src sweep | ~200 |
| delta-1 coverage audit doc | ~150 |
| delta-2..3 test sweep | ~140 |
| delta-4 + epsilon-1 deletion plumbing | ~70 |
| eta-1 gate + 9th sentinel | ~450 |
| eta-2 G-RC-9 final | ~250 |
| **Total positive** | **~1 590 LOC** |

Within ADR-0014 +/-10% against ~1 500 anchor (1 500 x 1.10
= 1 650; 1 590 < 1 650). Per-commit insertions **<= 350 LOC**
throughout (~450-LOC eta-1 splits if needed). **Negative
LOC: ~-35 000.** commit_guard checks insertions only
(P9.6gamma precedent); deletions unbounded.

## 7. Sentinel strategy

**8 / 8 P-RC-9 sentinels ACTIVE at HEAD ``d49388bb``** (6
parity P9.1c-P9.6gamma + 1 REST contract P9.7gamma-2 + 1
frontend stale-path P9.8delta-1).

### 7.1 Existing sentinels through deletion

* **#1-#6 (parity)**: stay ACTIVE; compare v2 vs v2
  contract post-deletion (zero xfail throughout).
* **#7 (REST contract / OpenAPI snapshot at
  ``tests/parity/orgs/_openapi_snapshot.json``)**: stays
  ACTIVE; **at epsilon-1** the v1 router (89 endpoints) is
  removed from the FastAPI registry. Snapshot is
  **regenerated in the same epsilon commit, dropping 89 v1
  entries**. Expected post-epsilon: 83 mint + 9 spec + 9
  shim = 101 routes (alpha-1 confirms exact pre-deletion
  counts).
* **#8 (frontend stale-path)**: ACTIVE; the 3 Group C
  allowlist entries stay (P-RC-10 follow-up comment).

### 7.2 9th "v1 deletion guard" sentinel -- recommend **ADOPT (Y)**

Mirrors the P9.7 + P9.8 grep-sentinel pattern
(collection-time scan; no test execution). Three
assertions in
``tests/parity/orgs/test_v1_deletion_guard_sentinel.py``:
(1) ``not pathlib.Path("src/openakita/orgs").exists()``;
(2) ``not pathlib.Path("src/openakita/api/routes/orgs.py").exists()``;
(3) regex scan for zero ``from openakita.orgs`` or
``import openakita.orgs`` matches under
``src/openakita/`` or ``tests/``.

Rationale: forcing function for R1 (silent ImportError)
+ R4 (orphaned re-exports). Cheap (~30 ms collection;
same shape as 7th + 8th). The 308 shim under
``api/routes/_orgs_v2_legacy_redirects.py`` does **NOT**
import from ``openakita.orgs`` (it imports from
``openakita.api.routes.orgs_v2_*``), so assertion 3 is
**compatible with ADR-0015's NO-OP** for zeta (verified
at HEAD). ADR-0011 ceiling held (no new Protocol).

**Post-G-RC-9.9 sentinel count: 9 / 9 ACTIVE.**

## 8. 308 shim retirement (per ADR-0015)

**ADR-0015 LOCKED option (b)**: shim
``api/routes/_orgs_v2_legacy_redirects.py`` (9 routes /
101 LOC) is **byte-level untouched through P9.9**;
physical retirement is a single v2.1.0 milestone task.

### 8.1 P9.9zeta NO-OP (this round)

* ``git diff d49388bb..HEAD --
  api/routes/_orgs_v2_legacy_redirects.py`` expected
  **empty bytes** across every P9.9 commit.
* Sentinels #7 + #8 continue to observe the shim through
  G-RC-9.9 (#7 snapshot retains 9 ``include_in_schema=False``
  entries verbatim; #8 scan is path-only).
* 9 shim paths enumerated in ADR-0015 Implementation notes;
  G-RC-9.9 sec 8 records the NO-OP citing ADR-0015 + Q-B.

### 8.2 v2.1.0 milestone task list (3 atomic steps)

1. ``git rm api/routes/_orgs_v2_legacy_redirects.py``
   (-101 LOC; -9 routes from FastAPI registry).
2. Drop shim router mount from ``api/server.py`` (1-2 LOC).
3. Regenerate ``tests/parity/orgs/_openapi_snapshot.json``
   to remove 9 shim entries (sentinel #7 fails as intended
   forcing function until snapshot regenerates).
4. (Audit-only) sweep sentinel #8 frontend allowlist for
   Group C drift.

Net v2.1.0 delta: **~-102 src LOC + -9 snapshot entries**.

## 9. G-RC-9.9 gate criteria

1. All alpha + beta + gamma + delta + epsilon + eta-1
   commits land per sec 5 phase ordering (beta BEFORE
   epsilon; tests/orgs/ delete in delta-4 BEFORE epsilon).
2. ``src/openakita/orgs/`` directory **absent** post-eta
   (9th sentinel assertion 1).
3. ``api/routes/orgs.py`` **absent** post-eta (9th
   sentinel assertion 2).
4. ``tests/orgs/`` directory **absent** post-eta.
5. Zero ``from openakita.orgs`` or ``import openakita.orgs``
   imports under ``src/openakita/`` or ``tests/`` (9th
   sentinel assertion 3).
6. ``_orgs_v2_legacy_redirects.py`` **byte-identical to
   HEAD ``d49388bb``** per ADR-0015 NO-OP (``git diff``
   empty).
7. **9 / 9 sentinels ACTIVE** with zero xfail (8 prior +
   1 NEW).
8. Main gate measured FULLY (``pytest -q --tb=no``; per
   G-RC-9.6 NIT-G-1 / G-RC-9.7 / G-RC-9.8 auditor mandate
   -- no extrapolation). Expected: **>= G-RC-9.8 baseline
   minus deleted tests/orgs/ cases**; 12 carry-over
   failures stay verbatim unchanged (none under tests/orgs/).
9. ``tests/integration/test_v2_im_canary_e2e.py`` green
   (R3 + P9.7.nit-a regression check).
10. Narrow slice (api + runtime/orgs + parity/orgs +
    canary): zero regression vs G-RC-9.8 baseline (585
    passed); ALL 9 sentinels included.
11. ADR-0011 / 0012 / 0013 / 0014 / 0015 invariants held.
12. ``docs/revamp/gates/G-RC-9.9.md`` lands in eta-1;
    ledger close summary in same commit.

ACCEPTANCE.md is **not** modified by G-RC-9.9 alone;
#4 + #5 close in **G-RC-9 final** (eta-2).

## 10. G-RC-9 final preview (eta-2)

After G-RC-9.9 PASS, eta-2 lands ``G-RC-9.md`` signing
off the entire P-RC-9 revamp:

* Closes **ACCEPTANCE.md #4** (v2 REST mint + v1 surface
  retired) and **#5** (9 / 9 parity sentinels active).
* Closes NIT residue (G-RC-9.8 sec 11): **B-1** burst-test;
  **M-1** runtime_parity golden-dict; **M-2** ADR-0014
  sub-cap breach (agent_pipeline / plugin_assets); **M-3**
  v1 method residue (closes by construction at epsilon);
  **M-4** P9.6.pause cosmetic; **P9.7-B** two contract files
  30-45 LOC over 350 cap.
* Mints the **Y3 BOM** (final P-RC-9 manifest; mirrors
  P-RC-8 close).
* Signs off P-RC-9: 6 / 6 ADR-0011 subsystems migrated +
  parity-validated + v1 deleted; v2 REST mint (83 + 9 +
  9) + frontend caller migration (55 swaps) + 9 / 9
  sentinels active; ADR-0011 / 0012 / 0013 / 0014 / 0015
  honoured.
* **P-RC-10 (runtime/ flattening) is a SEPARATE EPIC** per
  ``docs/revamp/P-RC-10-CHARTER.md`` (next-epic pointer only).

## 11. Reference matrix (NIT-E-1 discipline)

Re-scan of ``d:\claw-research
epos`` (autogen / cortex /
crewAI / langgraph / MetaGPT / sint-protocol) +
``d:\claw-research\briefs`` (01..06) for **physical
legacy-deletion + atomic-import-sweep** patterns. **All 12
inputs expected REJECTED for mechanical deletion**: this
round is a literal ``git rm`` +
``s/openakita.orgs/openakita.runtime.orgs/g`` sweep + a
9th instance of the established grep-sentinel pattern.
**Net adoption for P9.9: NONE.** Design inputs come
entirely from in-tree precedent: P9.4c / P9.5c / P9.6gamma
absorption history (sec 3.3); P9.7 308 shim governance
(ADR-0015); P9.8 caller-side literal sweep (9th sentinel
mirrors 8th's grep shape). External corpus offers nothing
superior for a physical deletion-plus-import-rewrite
event. NIT-E-1 satisfied at G-RC-9.9 eta-1 audit time with
all 12 explicitly rejected.

## 12. Q_DECISIONS update preview

* **Q-B (1-release HTTP shim; accepted 2026-05-19)**: fully
  ratified by ADR-0015 at ``d49388bb``. No further Q_DECISIONS
  edit required in P9.9 (ratification note already lives in
  ``Q_DECISIONS.md`` per the P9.9.adr commit body).
* **Q-D (CANDIDATE; optional new row)**: governance for
  9th sentinel adoption. Suggested text: *"Does P-RC-9
  close with a 9th 'v1 deletion guard' sentinel (assert
  ``src/openakita/orgs/`` + ``api/routes/orgs.py`` absent
  + zero ``openakita.orgs`` imports), or do the existing 8
  sentinels suffice?"* Recommended row: **ACCEPTED (Y)**
  -- adopt in eta-1 per sec 7.2. Non-blocking: sec 7.2
  adoption stands on architectural merit alone (7th + 8th
  sentinels both adopted without dedicated Q rows).

## 13. Cross-references + HARD STOP

PLAN sec 4 P9.9 (charter row this expands; 308 shim
deferred per ADR-0015); ``gates/G-RC-9.8.md`` (prior
mini-gate; 8 / 8 sentinels); ``gates/G-RC-9.6.md`` sec 13
(absorption panorama -- input for sec 3.3); ``ADR-0011``
(no new Protocol); ``ADR-0012`` (v1 deletion at P9.9 per
Q-B); ``ADR-0014`` (LOC budget ~1 590 within +/-10%);
``ADR-0015`` (308 shim governance -- option (b) LOCKED;
zeta NO-OP).

**HARD STOP**: planning round only. **P9.9alpha-1 NOT started
in this commit.** Next agent run, on operator signal, opens
P9.9alpha-1 (import inventory doc). Until then: HEAD =
``d49388bb`` + this charter + a single ledger row; ``git diff
d49388bb..HEAD -- src/openakita/ tests/ apps/`` returns empty
bytes.

**P-RC-9 status after this commit**: 6 / 6 ADR-0011 subsystems
complete + parity-validated (P9.1-P9.6); v2 REST mint complete
(P9.7; 83 + 9 + 9); frontend caller migration complete (P9.8;
55 swaps); ADR-0015 filed (P9.9.adr); **P9.9 charter LANDED**;
P9.9alpha-1 NOT started; G-RC-9 final unscheduled.
