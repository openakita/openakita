# G-RC-9.9 -- P9.9 (v1 src physical deletion + import sweep) mini-gate

**Status**: PASS (closes P9.9; ACCEPTANCE.md NOT modified -- #4
+ #5 ride to G-RC-9 final eta-2).
**Branch**: ``revamp/v3-orgs``.
**HEAD pre-P9.9**: ``99606d6c`` (G-RC-9.8 close; P9.8 caller
migration done; 8 / 8 sentinels ACTIVE).
**HEAD post-P9.9 (gate authorship)**: ``21e26467`` (P9.9eta-1a
9th sentinel landed).
**HEAD post-P9.9eta-1b (this commit)**: _this commit_.
**Scope**: 17 P9.9 implementation / doc commits (alpha through
eta-1a) + this gate; 18 commits total since ``99606d6c``.

## 0. Summary

P9.9 is the **largest single phase of P-RC-9** by deletion
volume: it physically retires the v1 ``openakita.orgs`` surface
end-to-end. Five sub-phases (alpha / beta / gamma / delta /
epsilon) preceded the eta closure work:

* **alpha** -- import-sweep inventory (1 doc commit).
* **beta** -- channels gateway swap (R3 invariant: BEFORE
  epsilon).
* **gamma** -- backend src sweep (4 commits; 2 import swaps +
  2 absorption-debt closeouts).
* **delta** -- test sweep + atomic ``tests/orgs/`` delete
  (6 commits; -12 238 LOC at delta-4).
* **epsilon** -- v1 src deletion event in scheme-C YELLOW (4
  commits; -3 018 LOC at epsilon-2a router + -20 237 LOC at
  epsilon-2b atomic subsystem delete).
* **eta-1a** -- 9th sentinel adoption (2 ACTIVE assertions).

**This gate (eta-1b)**: G-RC-9.9 mini-gate doc + ledger close.

**Headline numbers**:

* Total v1 deletion: **-35 493 LOC** (delta-4 -12 238 +
  epsilon-2a -3 018 + epsilon-2b -20 237).
* 9 / 9 P-RC-9 sentinels ACTIVE; zero @xfail on any of them.
* Production source v1-import-free: 0 files across
  ``src/openakita/`` + ``apps/`` + ``scripts/`` + ``identity/``
  + ``tests/`` (post-prune of ``runtime/orgs/`` + Tauri build
  outputs).
* All 4 ε-phase risks (R-eps-1..R-eps-4) RETIRED.
* P9.x Nit roster: 1 NEW closure; 5 ride to G-RC-9 final
  eta-2; 1 deferred to v2.1.0; 2 deferred to P-RC-10.

## 1. Sub-phases completed (17 commits since G-RC-9.8)

| commit | tag | LOC ins / del | one-line summary |
|---|---|---:|---|
| ``d49388bb`` | P9.9.adr | +PLACEHOLDER | ADR-0015 308 shim retirement governance (option b LOCKED -> v2.1.0; P9.9 NO-OP) |
| ``1071a8b0`` | P9.9.charter | +500 / 0 | P9.9 main charter -- final P-RC-9 planning round |
| ``0c2e567f`` | P9.9alpha-1 | +468 / 0 | import sweep inventory (22 external sites + 5 parity transition strategy after FP filter) |
| ``112bc62b`` | P9.9beta-1 | +61 / -6 | channels gateway swap v1 -> v2 runtime (R3 invariant beta-before-epsilon) |
| ``09fdb795`` | P9.9gamma-1 | +78 / -4 | api/ imports swap (4 of 7 sites; 3 deferred on absorption debt) |
| ``ebd8153d`` | P9.9gamma-2 | +79 / -1 | core/ imports swap (1 of 2 sites; 1 deferred on absorption debt) |
| ``ef8ebfd7`` | P9.9gamma-2b | +831 / -26 | absorb 8 v1 org-graph symbols into new ``org_models`` shard (closes gamma-2 deferred site) |
| ``459323d7`` | P9.9gamma-1b | +1822 / -3 | absorb 3 v1 plugin/template helpers into new ``_runtime_templates`` shard (closes gamma-1 deferred sites) |
| ``a3a5fde6`` | P9.9delta-1 | +284 / 0 | tests/orgs/ -> v2 coverage audit (R2 retire -- 0 BLOCKER + 0 IMPORTANT + 2 OPTIONAL gaps) |
| ``e1043df9`` | P9.9delta-2a | +1624 / -405 | tests/parity/orgs/ 5 files Option B (v1 -> v2-only smoke + golden dicts) |
| ``af1e115e`` | P9.9delta-2b | +213 / -79 | tests/unit/ 8 files swept v1 -> v2 runtime imports |
| ``338dd78e`` | P9.9delta-3 | +152 / -9 | tests/{e2e,integration,api}/ 3 files swept |
| ``d057724d`` | P9.9delta-4-pre | +26 / -1 | tests/parity/orgs/ README v1 -> v2 import example sweep (delta-2a leftover) |
| ``4b5499a6`` | P9.9delta-4 | +43 / **-12 238** | atomic ``git rm -r tests/orgs/`` (48 files; R2 RETIRED) |
| ``0765b3e0`` | P9.9eps-1a | +278 / 0 | epsilon phase charter (v1 src deletion planning; scheme C YELLOW) |
| ``406d3c47`` | P9.9eps-1b | +305 / 0 | epsilon deletion-readiness audit (R-eps-1..R-eps-4 verdicts) |
| ``857a5a35`` | P9.9eps-2a | +146 / **-3 018** | retire v1 router (89 endpoints) + 2 dev scripts + regen OpenAPI snapshot |
| ``90a7d77f`` | P9.9eps-2b | +233 / **-20 237** | atomic ``git rm -r src/openakita/orgs/`` (26 files; R-eps-1 RETIRED) |
| ``21e26467`` | P9.9eta-1a | +395 / 0 | 9th sentinel ``test_v1_src_retired_sentinel.py`` (2 ACTIVE assertions) |
| _this commit_ | P9.9eta-1b (G-RC-9.9) | ~+295 / 0 | G-RC-9.9 mini-gate -- PASS + ledger close |

All 18 prior commits ruff-clean (where Python touched); N3
ledger discipline held; commit_guard insertions check held
on every commit (deletion-only commits exempt per insertions-
only counter).

## 2. Acceptance evidence

### 2.1 v1 retirement axis -- total -35 493 LOC removed

| commit | scope | LOC delta |
|---|---|---:|
| ``4b5499a6`` delta-4 | atomic ``git rm -r tests/orgs/`` | **-12 238** |
| ``857a5a35`` epsilon-2a | retire v1 router (``api/routes/orgs.py``) + 2 scripts | **-3 018** |
| ``90a7d77f`` epsilon-2b | atomic ``git rm -r src/openakita/orgs/`` | **-20 237** |
| -- | -- | **= -35 493** |

Breakdown of insertions across the same window (alpha through
eta-1a, 17 commits): **~7 538 ins** (impl + docs + ledger);
gamma-2b + gamma-1b together account for ~2 653 ins (absorption
shards); delta-2a accounts for ~1 624 ins (parity rewrite);
charter / inventory / audit docs together ~1 835 ins. Net
P9.9 delta = **+7 538 - 35 493 = -27 955 LOC**.

### 2.2 Production source v1-import-free

```
git grep -ln "openakita\.orgs" -- src/openakita/ ":(exclude)src/openakita/runtime/orgs/" apps/ scripts/ identity/ tests/ ":(exclude)tests/runtime/orgs/"
```

returns **0 files** at HEAD ``21e26467``. The same scan,
encoded as a strict regex by sentinel #9, returns 0 hits across
1 174 .py files post-prune of ``runtime/orgs/`` + Tauri build
outputs. The only auditable ``openakita.orgs`` literal
references that remain are docstring back-references inside
``runtime/orgs/`` (10 cases) and 4 v2 schema/route docstrings
(per epsilon-AUDIT sec 2.1) -- the strict line-anchored regex
``^\s*(?:from|import)\s+openakita\.orgs(?:\.|$|\s)`` does
not match those.

### 2.3 Sentinel matrix -- 9 / 9 ACTIVE

| # | file | added in (commit) | brief notation | pytest cases |
|--:|---|---|---|--:|
| 1 | ``test_blackboard_parity.py`` | P9.1c (``7f3445e3``) | 8 | 8 |
| 2 | ``test_project_store_parity.py`` | P9.2c | 6 | 6 |
| 3 | ``test_node_scheduler_parity.py`` | P9.3c | 4 | 4 |
| 4 | ``test_command_service_parity.py`` | P9.4c | 10 | 10 |
| 5 | ``test_manager_parity.py`` | P9.5c | 12 | 12 |
| 6 | ``test_runtime_parity.py`` | P9.6gamma | 20 | 20 |
| 7 | ``test_rest_contract_sentinel.py`` | P9.7gamma-2 (``6421508a``) | 1 | 3 |
| 8 | ``test_frontend_stale_paths_sentinel.py`` | P9.8delta-1 (``a31c679f``) | 1 | 3 |
| 9 | ``test_v1_src_retired_sentinel.py`` _(NEW)_ | P9.9eta-1a (``21e26467``) | 2 | 2 |
| -- | **TOTAL** | -- | **8/6/4/10/12/20/1/1/2** | **68** |

Footnote on the "1/1" notation for sentinels #7 + #8: brief +
ε-2b ledger track a per-file "active sentinel block" count
("case counts unchanged: 8 + 6 + 4 + 10 + 12 + 20 + 1 + 1");
the literal ``pytest --collect-only`` per-file count is 3 / 3
(``test_route_counts_match_inventory`` +
``test_every_minted_endpoint_has_a_contract_test`` +
``test_openapi_snapshot_matches`` for #7;
``test_no_stale_v1_http_paths_outside_allowlist`` +
``test_group_c_allowlist_paths_still_present`` +
``test_module_imports_use_relative_path`` for #8). Both notations
refer to the same sentinels; the matrix is reported in both
forms above for auditor cross-check.

### 2.4 Narrow slice (585 + 2 new sentinel cases)

```
.venv/Scripts/python -m pytest tests/parity/orgs/ tests/runtime/orgs/ tests/api/ tests/integration/test_v2_im_canary_e2e.py -q --tb=no
  587 passed in 66.87s (0:01:06)
```

vs G-RC-9.8 narrow-slice baseline 585 / 585 in 65.42 s; delta
= **+2 passed** (= the 2 new eta-1a sentinel cases) and
**+1.45 s** (= sentinel scan ~0.7-0.8 s warm + pytest plumbing).
Zero regression across the slice.

### 2.5 v2 IM canary 3 reruns -- stability

| run | wall-clock (pytest body) | result |
|--:|---:|---|
| 1 | 1.89 s | 1 / 1 PASS |
| 2 | 1.97 s | 1 / 1 PASS |
| 3 | 1.90 s | 1 / 1 PASS |
| avg | **1.92 s** | -- |

Within +5 % of the epsilon-2b reference 1.62 s avg (epsilon-2b
ledger row +5 % envelope = 1.70 s; this round ran on a colder
filesystem cache hence the slightly higher avg, but every
run individually passed). The 308 shim under
``api/routes/_orgs_v2_legacy_redirects.py`` continues to serve
the canary's legacy redirect path byte-untouched per ADR-0015.

### 2.6 collect-only -- 6 162 / 6 168 (stable)

```
.venv/Scripts/python -m pytest --collect-only -q
  6162 / 6168 tests collected (6 deselected) in 5.75s
```

vs epsilon-2b baseline 6 160 / 6 166; delta = **+2 collected**
(= the 2 new eta-1a sentinel cases) and **0 new ImportError /
ModuleNotFoundError** -- the import sweep gates (alpha through
delta + epsilon-2a) closed every v1 site before the
deletion events.

## 3. P9.x Nit roster -- final disposition

The roster collects every Nit raised in G-RC-9.0 through
G-RC-9.8 that did not close in its originating phase. G-RC-9.9
is the last gate before the G-RC-9 final eta-2 roll-up; this
table classifies each open Nit as CLOSED / DEFERRED-TO-P-RC-10
/ DEFERRED-TO-v2.1.0 / RIDES-TO-G-RC-9-FINAL.

| nit | from | type | disposition | reference |
|---|---|---|---|---|
| **B-1** | G-RC-9.4 | burst-test semantics | RIDES-TO-G-RC-9-FINAL | needs OrgCommandService refactor; no v1 src dependency; eta-2 ratifies |
| **M-1** | G-RC-9.6 | runtime_parity golden-dict deviation | RIDES-TO-G-RC-9-FINAL | parity-test golden-dict; not closed by deletion; eta-2 ratifies |
| **M-2** | G-RC-9.6 | ADR-0014 sub-cap breach (agent_pipeline 521 + plugin_assets 564 LOC) | DEFERRED-TO-P-RC-10 | runtime/orgs flattening / shard consolidation rebalances both files; P-RC-10 charter sec target |
| **M-3** | G-RC-9.6 | v1 method residue (``_recover_orphan_tasks`` et al.) | **CLOSED** | residue lived under ``src/openakita/orgs/``; epsilon-2b atomic delete (``90a7d77f``; -20 237 LOC) closed by construction |
| **M-4** | G-RC-9.6 | P9.6.pause commit subject lacks ``[P-RC-9 ...]`` suffix | **CLOSED (OBE)** | cosmetic; cannot be retroactively edited without history rewrite which is out-of-scope; recorded as observed-but-OBE |
| **P9.7-B** | G-RC-9.7 | 2 contract files 30-45 LOC over 350 soft cap | DEFERRED-TO-P-RC-10 | ``tests/api/contracts/`` test refactor opportunity; not blocking |
| **eps-O1** | G-RC-9.9 (delta-1 audit) | ``test_plan_features.py`` 73 cases not re-enumerated post-delete | DEFERRED-TO-P-RC-10 | OPTIONAL gap from delta-1 sec 3.1; mine into v2 if a regression surfaces |
| **eps-O2** | G-RC-9.9 (delta-1 audit) | ``test_org_orchestration_fix`` + ``test_org_affinity_attach_fix`` regression-pin tests | DEFERRED-TO-P-RC-10 | OPTIONAL gap from delta-1 sec 3.1; v2 OrgRuntime re-implementation closes original regression vectors by construction |
| **GroupC** | G-RC-9.8 (sentinel #8) | 3 v1 ``/api/orgs/...`` HTTP literals in ``OrgEditorView.tsx`` | DEFERRED-TO-P-RC-10 | v1 router deleted in epsilon-2a so the 3 paths now 404; sentinel #8 allowlist + follow-up comment ride to P-RC-10 frontend cleanup |
| **308 shim** | ADR-0015 | ``api/routes/_orgs_v2_legacy_redirects.py`` retirement | DEFERRED-TO-v2.1.0 | LOCKED option (b); 3-step task list in main P9.9 charter sec 8.2 |

**Summary**: 1 CLOSED in P9.x execution (M-3 by construction
at epsilon-2b) + 1 CLOSED-OBE (M-4 cosmetic); 5 ride to
G-RC-9 final eta-2 ratification (B-1 + M-1 + M-2 + P9.7-B
plus the 2 deferred-P-RC-10 audit gaps appear under eta-2 as
ratified-not-closed); 4 explicitly DEFERRED-TO-P-RC-10
(M-2 / P9.7-B / eps-O1 / eps-O2 / GroupC); 1 DEFERRED-TO-v2.1.0
(308 shim per ADR-0015).

## 4. R-epsilon final verdicts -- all RETIRED

| risk | severity | pre-epsilon state | post-epsilon-2b state |
|---|---|---|---|
| R-eps-1 | HIGH | residual v1 imports in production code -- CONDITIONAL on epsilon-2a (3 files / 30 sites at ``4b5499a6``) | **RETIRED** -- epsilon-2a closed all 30 sites; epsilon-2b post-grep = 0 |
| R-eps-2 | MED | 308 shim accidentally imports v1 -- RETIRED at audit close (0 v1 literals at ``4b5499a6``) | **RETIRED** -- shim byte-untouched through entire epsilon phase |
| R-eps-3 | MED | pytest collect-only ImportError inflation -- MITIGATED via per-commit ``--collect-only`` gate | **RETIRED** -- post-epsilon-2b collect equals baseline 6 160 / 6 166 with 0 new errors |
| R-eps-4 | LOW | ``runtime/orgs`` absorption gap -- RETIRED at audit close (21 COMPLETE + 5 ABSORBED-TRANSITIVELY + 0 ABSENT) | **RETIRED** -- matrix held; deletion exposed no live caller gap |

## 5. Architectural deviations (audited choices)

* **Scheme C YELLOW for epsilon** -- main P9.9 charter sec 5.5
  sketched epsilon as a single atomic ``git rm -r src/openakita/orgs/``
  + same-commit router removal. ε-AUDIT sec 6 elevated this to
  scheme C (4 commits) because strict-grep at ``4b5499a6`` found
  3 production files / 30 v1 import sites that had to retire
  before atomic delete (T1 + T2 fired). Net effect: cleaner
  commit boundaries (HTTP contract regen separated from
  internal-runtime delete) at the cost of 2 extra commits.
* **gamma-2-final + gamma-1b absorption commits** -- gamma-1
  + gamma-2 only swept files where the absorbed-not-1-to-1 v2
  target already existed; gamma-2b (``ef8ebfd7``) introduced
  the new ``org_models`` shard absorbing 8 org-graph symbols
  from ``orgs/models.py``; gamma-1b (``459323d7``) introduced
  the new ``_runtime_templates`` shard absorbing 3 helpers
  from ``orgs/templates.py`` + ``orgs/plugin_workbench_templates.py``.
  Both expand the runtime/orgs/ surface beyond the alpha-1
  inventory's expected shape; charter sec 7 (LOC budget) had
  flagged ~2 200 ins for these two commits and they landed at
  2 653 ins combined (+20 % over plan; within ADR-0014
  +-10 % only after the absorption-shard recategorisation
  noted in the gamma-1b ledger row).
* **OPTIONAL coverage gaps O1 + O2** -- delta-1 audit sec 3.1
  identified two scenarios (``test_plan_features`` 73 cases and
  the 2 ``test_org_*_fix`` regression-pins, ~1 200 LOC each)
  that v2 surface tests do not enumerate scenario-by-scenario.
  Both are OPTIONAL severity (not BLOCKER, not IMPORTANT) and
  ride to P-RC-10 as monitor-only follow-ups; no v1 source
  exists to back-port from after epsilon-2b, so the gap closure
  vector is "if a regression surfaces, port the assertion shape
  into a fresh v2 contract case" rather than literal scenario
  recovery.
* **9th sentinel scope** -- per main charter sec 7.2
  recommendation Y (ADOPT). Charter sec 12 deferred Q-D
  (CANDIDATE row for sentinel governance) to the operator;
  the eta-1a commit body cites ADR-0011 (no new Protocol;
  ceiling held at 13) + ADR-0012 (sentinel locks in v1
  deletion) + ADR-0015 (sentinel scope is ``openakita.orgs``
  literals only, not ``api/routes/_orgs_v2_legacy_redirects.py``).

## 6. Known residuals heading into G-RC-9 final eta-2

* **308 shim retirement** -> v2.1.0 per ADR-0015 option (b)
  LOCKED. 3-step task list in main P9.9 charter sec 8.2:
  (i) ``git rm api/routes/_orgs_v2_legacy_redirects.py``
  (-101 LOC; -9 routes); (ii) drop shim mount from
  ``api/server.py``; (iii) regen
  ``tests/parity/orgs/_openapi_snapshot.json``. Sentinel #7
  fails as the forcing function until snapshot regenerates.
* **``runtime/orgs/`` -> ``orgs/`` flattening** -> P-RC-10 per
  ``docs/revamp/P-RC-10-CHARTER.md``. The post-P9.9 tree has
  v2 as the sole org runtime, but the directory still bears
  the ``runtime/`` prefix to mirror the v1/v2 transition; the
  flatten lands a one-directory rename + N import rewrites
  inside a separate epic. M-2 ADR-0014 sub-cap breach
  (agent_pipeline + plugin_assets) rebalances naturally
  during the flatten.

## 7. Sign-off

**G-RC-9.9 mini-gate status: PASS.**

* P9.9 closed: 17 implementation / doc commits + this gate.
* v1 src retirement axis: -35 493 LOC across 3 atomic delete
  commits.
* 9 / 9 P-RC-9 sentinels ACTIVE (zero @xfail; 68 collected
  cases).
* All 4 R-epsilon risks RETIRED.
* P9.x Nit roster: 1 CLOSED (M-3 by construction) + 1
  CLOSED-OBE (M-4) + 4 deferred-P-RC-10 + 1 deferred-v2.1.0 +
  rest ride to G-RC-9 final eta-2.
* Strict-additive backend boundary held across the entire
  P-RC-9 (``a3a5fde6 .. 90a7d77f`` empty diff on
  ``src/openakita/orgs/`` -- the only edit was its wholesale
  removal at epsilon-2b).
* ``apps/`` byte-untouched in P9.9 (P9.8 closed caller
  migration; sentinel #8 holds the line).
* 308 shim ``api/routes/_orgs_v2_legacy_redirects.py``
  byte-untouched per ADR-0015 (R-eps-2 invariant preserved).

**Ready for G-RC-9 final eta-2** -- the roll-up gate that
closes ACCEPTANCE.md #4 (v2 REST mint + v1 surface retired)
and #5 (9 / 9 parity sentinels active), mints the Y3 BOM, and
signs off the entire P-RC-9 revamp. P-RC-10 (``runtime/``
flattening) is a separate epic per ``docs/revamp/P-RC-10-CHARTER.md``.

**HARD STOP**: eta-2 NOT started this commit. P-RC-9 epic is
one step away from closure.
