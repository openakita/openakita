# P-RC-9 P9.9ε Charter -- v1 src physical-deletion phase (PLANNED)

Authority: P-RC-9 P9.9 charter (``d49388bb``) §5.5 ε phase
sketch; ADR-0012 (v1 deletion at P9.9 per Q-B ACCEPTED (b));
ADR-0015 (308 shim retirement deferred to v2.1.0 -- ε is
byte-untouched for ``_orgs_v2_legacy_redirects.py``). Closes
the final physical-deletion sub-phase of P-RC-9 P9.9 after
δ-4 RETIRED R2.

HEAD at authorship: ``4b5499a6`` (close of δ-4 -- atomic
``git rm -r tests/orgs/``, 48 files / 12 238 LOC removed).
All ε scope numbers MEASURED at this HEAD. Strict-additive
on v1 src still holds going INTO ε-1:
``git diff a3a5fde6..HEAD -- src/openakita/orgs/`` returns
empty bytes.

## 0. Mission and exit criteria

ε is the **final physical-deletion phase** for the v1 org
subsystem under ``src/openakita/orgs/``. After P9.9ε all v1
code under that directory is gone from the tree; v2
``src/openakita/runtime/orgs/`` (23 files / 10 886 LOC at HEAD
``4b5499a6``) becomes the **sole** in-tree home of the org
runtime.

**Exit criteria (all GREEN before G-RC-9.9):**

1. ``git ls-files src/openakita/orgs/`` = **0**.
2. ``git grep -ln "openakita\.orgs" -- src/openakita/
   ":(exclude)src/openakita/orgs/" apps/ scripts/ identity/`` =
   **0 production files**.
3. 8 / 8 P-RC-9 sentinels still ACTIVE (case counts
   8 / 6 / 4 / 10 / 12 / 20 / 1 / 1; 9th deferred to G-RC-9.9).
4. v2 IM canary 1 / 1 PASS ×3 within ±5 % of 1.62 s baseline
   (audit §5 reference run).
5. Narrow slice 585 / 585 PASS unchanged.
6. ``pytest --collect-only`` no new ``ImportError`` /
   ``ModuleNotFoundError`` beyond the 6 deselected baseline
   (collect total stays at 6 160 / 6 166).

**Out of scope (per ADR-0015 + PLAN):**

* 308 shim ``api/routes/_orgs_v2_legacy_redirects.py``
  (101 LOC / 9 routes) -- byte-untouched per ADR-0015; v2.1.0
  milestone task list lives in main P9.9 charter §8.2.
* v2 runtime flattening (``runtime/orgs`` → ``orgs`` rename,
  ``_runtime_*`` shard consolidation) -- P-RC-10 epic.
* v1 router ``src/openakita/api/routes/orgs.py`` (2 533 LOC /
  89 endpoints) -- treated as the **ε-2a residual-caller
  retirement** (see §2 scheme C). NOT folded into the atomic
  ε-2b ``git rm`` for review clarity, even though the original
  P9.9 charter §1.1 grouped it with the subsystem delete; this
  charter narrows ε scope to the directory
  ``src/openakita/orgs/`` only.

## 1. Scope (MEASURED at HEAD ``4b5499a6``)

**ε deletes**: 26 ``.py`` files under ``src/openakita/orgs/``,
totalling **20 237 LOC** (audit §1 per-file table). Top-5 by
LOC -- ``runtime.py`` 6 355, ``tool_handler.py`` 3 474,
``templates.py`` 1 266, ``models.py`` 1 018,
``command_service.py`` 963 -- account for **13 076 LOC = 65 %**
of the subsystem; the remaining 21 files cover the long tail
(blackboard / manager / messenger / event_router / inbox /
heartbeat / failure_diagnoser / scheduler / etc.).

**ε does NOT delete**: 308 shim (101 LOC; ADR-0015 NO-OP);
``runtime/orgs/`` (23 files / 10 886 LOC -- the v2 destination
the production imports already point to since β-1 / γ-1 / γ-2);
any tests (R2 RETIRED at δ-4; 0 tests under ``tests/orgs/``
since ``4b5499a6``).

## 2. Sub-phase breakdown (scheme C -- 4 commits)

Audit §6 verdict elevates ε-2 from the default 2-commit GREEN
path to **scheme C YELLOW** because the strict-grep production
caller scan returns **3 files / 30 sites** outside
``src/openakita/orgs/`` (audit §2). Scheme C splits ε into 4
commits (2 docs + 2 deletion):

| commit | scope | est ins / del |
|---|---|---|
| **ε-1a** _this commit_ | ε charter (this doc) + ledger row | ~245 / 0 |
| **ε-1b** | ε audit doc + ledger row | ~245 / 0 |
| **ε-2a** | retire v1 router + dev scripts: ``git rm src/openakita/api/routes/orgs.py`` (2 533 LOC / 89 endpoints) + drop router mount from ``api/server.py`` (~3 LOC) + ``git rm scripts/run_org_live_test.py`` + ``git rm scripts/test_org_full_task.py`` (~560 LOC scripts) + sentinel #7 OpenAPI snapshot regenerate (drops 89 v1 routes) + ledger row | ~80 / **-3 100** |
| **ε-2b** | atomic ``git rm -r src/openakita/orgs/`` (26 files / 20 237 LOC) + ledger row | ~30 / **-20 237** |

Scheme C **trigger conditions** (any one triggers; audit §6
finds T1 + T2 fired):

* **T1** -- v1 router ``api/routes/orgs.py`` still imports
  ``openakita.orgs.*`` at ε authorship time
  **[FIRED at 4b5499a6: 24 sites]**.
* **T2** -- any ``scripts/*.py`` imports ``openakita.orgs.*``
  **[FIRED at 4b5499a6: 2 files / 6 sites]**.
* **T3** -- any file under ``src/openakita/`` outside both
  ``orgs/`` and ``api/routes/orgs.py`` still imports v1
  **[NOT fired at 4b5499a6: γ-1 / γ-2 swept this clean]**.

**GREEN-path fallback**: a future operator may fold
``api/routes/orgs.py`` deletion into the same atomic commit as
the subsystem delete (the original P9.9 charter §1.1 sketch),
collapsing ε-2a + ε-2b into one commit (deletions -22 770
LOC + 89 endpoints). Scheme C is the **safer default** because
the two deletions have different review surfaces (HTTP contract
vs internal runtime) and the OpenAPI snapshot regen in ε-2a is
a separate auditable event.

## 3. Risk register

* **R-ε-1 (HIGH)** -- residual v1 imports in production code.
  Caller scan (audit §2) finds 3 files / 30 sites:
  ``api/routes/orgs.py`` (24 sites; v1 router itself, slated
  for retirement); ``scripts/run_org_live_test.py`` (3 sites);
  ``scripts/test_org_full_task.py`` (3 sites). All three are
  retired in ε-2a before the atomic delete in ε-2b, so by
  ε-2b authorship the strict-grep count is **0**. Audit §6
  marks R-ε-1 **CONDITIONAL on ε-2a landing first**; the
  conditional retires unconditionally at ε-2a close.
* **R-ε-2 (MED)** -- 308 shim accidentally imports v1. The
  shim must remain byte-untouched per ADR-0015. Audit §4
  confirms shim is 101 LOC / **zero ``openakita.orgs``
  literals** at HEAD ``4b5499a6``; ε-2a + ε-2b touch the shim
  zero times. **Mitigation**: each ε commit re-runs
  ``git grep "openakita\.orgs" --
  src/openakita/api/routes/_orgs_v2_legacy_redirects.py`` =
  expect 0; sentinel #7 OpenAPI snapshot retains the 9
  ``include_in_schema=False`` shim entries verbatim before and
  after ε. **R-ε-2 RETIRED at audit close** (no v1 literals
  exist to retire).
* **R-ε-3 (MED)** -- pytest collect-only ImportError inflation
  post-deletion. Baseline at HEAD ``4b5499a6``: 6 160 collected
  / 6 deselected = 6 166 total (audit §5). ε-2a removes the
  v1 router (24 internal imports vanish with the file) plus 2
  dev scripts (not collected by pytest -- not under
  ``tests/``); ε-2b removes ``src/openakita/orgs/`` (49
  internal imports vanish with the directory). Expected
  post-ε-2b delta: **0 new ImportError** -- every v1 site is
  either inside a deleted tree or already swept by γ / δ.
  **Mitigation**: each ε commit re-runs
  ``pytest --collect-only -q`` and asserts the trailing summary
  equals the baseline.
* **R-ε-4 (LOW)** -- ``runtime/orgs`` absorption gap discovered
  late. Audit §3 builds a 26-row v1 → v2 absorption matrix:
  21 COMPLETE (1:1 or absorbed-into-named-shard with live
  caller) + 5 ABSORBED-TRANSITIVELY (no live caller exists for
  the v1 surface -- parent module deletes by construction) +
  **0 ABSENT**. ``openakita.orgs.tool_handler`` (3 474 LOC) is
  the largest absorbed-transitively case: γ-2b absorbed its 8
  org-graph symbols into ``org_models``; the agent-pipeline
  half is exercised through ``_runtime_agent_pipeline`` and
  has no live v1 caller per audit §2. **R-ε-4 RETIRED at
  audit close.**

Severity rubric: HIGH = blocks ε-2 directly; MED = needs
explicit per-commit mitigation gate; LOW = monitor-only.

## 4. Pre-deletion sanity checklist (run before ε-2a AND ε-2b)

1. ``git status`` clean; branch ``revamp/v3-orgs``; HEAD on the
   previous ε sub-commit.
2. ``git diff a3a5fde6..HEAD -- src/openakita/orgs/`` = 0 bytes
   (strict-additive on v1 src still holds going INTO the
   deletion commit).
3. ``git grep -ln "openakita\.orgs" -- src/openakita/
   ":(exclude)src/openakita/orgs/" apps/ scripts/ identity/``:
   expected **3 files before ε-2a**, **0 files before ε-2b**.
4. Narrow slice 585 / 585 in ≤ 75 s (matches audit §5 baseline
   65.62 s + 10 s envelope).
5. Canary 1 / 1 ×3 within ±5 % of 1.62 s baseline.
6. ``git ls-files src/openakita/orgs/`` = 26 before ε-2b,
   **0 after**.

## 5. Commit specs

### 5.1 ε-2a -- retire v1 router + dev scripts

* Subject: ``chore(api,scripts): P9.9ε-2a retire v1 router +
  dev scripts (89 endpoints / 2 v1-import scripts; R-ε-1
  CONDITIONAL retires) [P-RC-9 P9.9ε-2a]``
* LOC envelope: ~80 ins (snapshot regen JSON delta, mount-line
  drop, ledger row) / **~-3 100 del** (router 2 533 + scripts
  ~560 + mount line). LOC counter on insertions only per N12;
  snapshot JSON delta is auto-generated and exempt per
  ``revamp_commit_guard.py`` skip rule.
* Body: enumerate 24 v1 sites vanishing with router; 6 v1 sites
  vanishing with scripts; sentinel #7 snapshot regenerate
  evidence (89 v1 routes removed); cite ADR-0012 + ADR-0015.
  Python tempfile message, BOM-free, LF.

### 5.2 ε-2b -- atomic src/openakita/orgs/ delete

* Subject: ``chore(orgs): P9.9ε-2b atomic delete
  src/openakita/orgs/ (26 files / 20237 LOC, v1 subsystem
  retired) [P-RC-9 P9.9ε-2b]``
* LOC envelope: ~30 ins (ledger row) / **-20 237 del**. Largest
  single deletion of P-RC-9 -- exceeds δ-4 (-12 238).
* Body: R-ε-1..R-ε-4 all RETIRED post-commit; 9th sentinel
  deferred to G-RC-9.9 η-1; Python tempfile message, BOM-free,
  LF.

## 6. Post-deletion verification matrix

| metric | baseline (4b5499a6) | post-ε-2a | post-ε-2b |
|---|---|---|---|
| ``git ls-files src/openakita/orgs/`` | 26 | 26 | **0** |
| strict-grep v1 production callers | 3 files / 30 sites | 0 files | 0 files |
| pytest collect-only | 6 160 / 6 166 | 6 160 / 6 166 ±0 | 6 160 / 6 166 ±0 |
| narrow slice | 585 / 585 | 585 / 585 | 585 / 585 |
| canary avg (s) | 1.62 | 1.62 ±5 % | 1.62 ±5 % |
| sentinels ACTIVE | 8 / 8 | 8 / 8 | 8 / 8 (9th @ G-RC-9.9) |
| 308 shim ``git diff`` | empty | empty | empty |

Exact OpenAPI snapshot route counts pre-/post-ε-2a remeasured
at ε-2a authorship from ``tests/parity/orgs/_openapi_snapshot.json``;
table shows scale (89 v1 routes drop), not authoritative counts.

## 7. Hard rules

* Deletion ONLY in ``src/openakita/orgs/`` (ε-2b) and
  ``src/openakita/api/routes/orgs.py`` + the 2 dev scripts
  under ``scripts/`` (ε-2a).
* ``src/openakita/runtime/orgs/`` -- untouched.
* ``src/openakita/api/routes/_orgs_v2_legacy_redirects.py`` --
  byte-untouched per ADR-0015 (R-ε-2 invariant).
* ``apps/`` -- untouched (P9.8δ-2 closed caller migration;
  3 Group C allowlist entries sit under sentinel #8 follow-up
  to P-RC-10).
* ``tests/`` -- untouched (R2 RETIRED at δ-4).
* 8 / 8 sentinels ACTIVE throughout ε; 9th deferred to
  G-RC-9.9 η-1.

## 8. ε → G-RC-9.9 → G-RC-9 sequence

1. **ε-1a** _(this commit)_ -- charter doc + ledger row.
2. **ε-1b** -- audit doc + ledger row (R-ε verdicts; ε-2
   readiness color).
3. **ε-2a** -- retire v1 router (2 533 LOC) + 2 dev scripts.
4. **ε-2b** -- atomic ``git rm -r src/openakita/orgs/``
   (20 237 LOC).
5. **G-RC-9.9 mini-gate** + 9th sentinel adoption (per main
   P9.9 charter §5.7 η-1).
6. **G-RC-9 final** roll-up gate (per main charter §5.7 η-2).

**HARD STOP**: ε-1b NOT started this commit; awaits explicit
operator signal after ε-1a charter closes. ε-2a / ε-2b NOT
started; await further explicit signals after ε-1b audit
closes with the YELLOW (scheme C) verdict confirmed.
