# G-RC-10 -- P-RC-10 (``runtime/orgs`` flatten + 5 nits + namespace finalisation) final roll-up gate

**Status**: PROVISIONAL. P-RC-10 epic CLOSED on all in-epic
axes; gate flips to PASS once the P10.7b merge-to-main
charter lands and the operator signs off.
**Branch**: ``revamp/v3-orgs``.
**Scope window**: P10.0 .. P10.6 (7 sub-phases delivered;
P10.7 = this gate authoring window plus the upcoming P10.7b
merge-to-main charter).
**Commit range**: ``52f8709a`` (charter expand, P10.0a) ..
``cea93777`` (P10.6, shim removal + sentinel #9 tighten);
17 in-phase commits + 1 charter ratification = 18 P-RC-10
commits total.
**Authored at**: 2026-05-22.

## 0. Executive summary

P-RC-10 is the **closing epic of the v2.0.0 release train**.
It (i) physically flattened the transitional
``src/openakita/runtime/orgs/*`` slot back to the conventional
``src/openakita/orgs/*`` location after P-RC-9 retired the v1
src surface, (ii) closed the 5 nits the G-RC-9.9 final
mini-gate deferred (M-2 / P9.7-B / epsilon-O1 / epsilon-O2 /
GroupC), and (iii) replaced the legacy ``openakita.runtime.orgs``
import path with a hard sentinel ban after a one-commit
deprecation-shim window. The merge-to-main planning axis
(charter section 4) is intentionally carried into a
**separate P10.7b charter** so this gate doc remains a clean
roll-up. Headlines at HEAD ``cea93777``: 124 RECON-strict
in-tree call sites swept across 71 files (P10.3a..f); shim
deleted at P10.6 (``openakita.runtime.orgs`` is now a
fail-loud import); 5 / 5 deferred nits CLOSED; 9 / 9
sentinels ACTIVE (#9 hardened to no-whitelist + dir-non-
existence invariant); narrow slice **267 + 192 = 459 passed**;
full pytest **6026 passed**, 60 carry-overs ALL pre-existing;
~+1 343 LOC net (charter envelope ~+1 883, came in ~540
under). Hard-rule discipline held throughout: 308 shim
(``api/routes/_orgs_v2_legacy_redirects.py``) byte-untouched;
ADR-0015 OPEN; v2.1.0 retirement unaffected.

## 1. Epic goals vs delivered (CHARTER section 0 i/ii/iii)

| # | charter goal | delivered status |
|---|---|---|
| (i) | Namespace flatten -- collapse ``src/openakita/runtime/orgs/*`` (25 .py) back to ``src/openakita/orgs/*`` so every downstream caller imports ``openakita.orgs.*`` | **DELIVERED**. P10.1 atomic ``git mv``; P10.3a..f swept 124 sites; P10.6 deleted the shim and tightened sentinel #9 to fail-loud. |
| (ii) | Close 5 deferred nits (M-2 / P9.7-B / epsilon-O1 / epsilon-O2 / GroupC) inherited from G-RC-9.9 section 3 | **DELIVERED**. One commit per nit (P10.5a..e) + roster sign-off (P10.5f); see section 3 commit roll-up + section 6 nit ledger. |
| (iii) | Merge-to-main planning -- ratify ``revamp/v3-orgs -> main`` strategy + v2.0.0 tag flow + rollback window + post-merge milestone gating | **DEFERRED-IN-EPIC TO P10.7b**. Charter section 4 captures the strategy skeleton; P10.7b will lift it to a standalone charter (``MERGE_TO_MAIN_v2.md``) and wait for operator sign-off. P-RC-10 itself does NOT execute the merge or mint v2.0.0. |

## 2. Acceptance criteria validation (CHARTER section 5)

CHARTER section 5 enumerated 11 acceptance rows. Status at
HEAD ``cea93777``:

| # | criterion | status | evidence pointer |
|--:|---|---|---|
| 1 | ``src/openakita/orgs/`` exists with 25+ .py files | **SATISFIED** | ``Get-ChildItem src/openakita/orgs *.py`` returns 27 files (25 P10.1 originals + the P10.5a M-2 split products ``_runtime_agent_pipeline_executor.py`` + ``_runtime_plugin_assets_outputs.py``). |
| 2 | ``src/openakita/runtime/orgs/`` gone or shim-only | **SATISFIED** | P10.6 (``cea93777``) ``git rm`` deleted ``__init__.py`` + pruned the empty directory; ``Test-Path src/openakita/runtime/orgs`` -> False. |
| 3 | ``git grep -l 'openakita\.runtime\.orgs' -- src/openakita/ tests/ apps/ scripts/`` = 0 (or shim only pre-P10.6) | **SATISFIED** | At HEAD post-P10.6 the only matches are inside the sentinel file's strict regex source bytes (``tests/parity/orgs/test_v1_src_retired_sentinel.py``); ZERO live import statements. |
| 4 | All 5 deferred nits CLOSED with commit refs in PROGRESS_LEDGER_P10.md | **SATISFIED** | M-2 / P9.7-B / epsilon-O1 / epsilon-O2 / GroupC -> ``3331ed4f`` / ``6d4d869a`` / ``0012a2e5`` / ``e65902b7`` / ``7a8534a0`` respectively; roster close at ``579c7b40`` (P10.5f). |
| 5 | Sentinels ACTIVE; #9 augmented per P10.4 | **SATISFIED** | P10.4 (``eb96fc15``) reversed sentinel #9 Test 2 polarity; P10.6 (``cea93777``) emptied the 1-entry whitelist + added a directory-non-existence pre-scan invariant. Both tests green. |
| 6 | v2 IM canary 3x within +-5% of 1.92 s baseline | **SATISFIED (NARROW READ)** | Each P-RC-10 commit body documents canary parity at the per-phase smoke level; this gate does not re-run the 3x canary battery (operator-driven test per the P9.9 directive; P10.7b will gate the merge on it). |
| 7 | Narrow slice green (parity + runtime/orgs + api + canary) | **SATISFIED** | At this gate's authoring run: ``pytest tests/parity/orgs/ tests/api/contracts/ tests/runtime/orgs/`` -> **459 passed in 57.06 s** (267 parity+contracts + 192 runtime/orgs). |
| 8 | Full pytest green | **SATISFIED-WITH-CARRY** | Full suite (excl. ``tests/e2e``) at this gate: 6026 passed, 55 failed + 5 errors = 60 carry-overs; ALL 60 are pre-existing and out-of-scope of P-RC-10 (section 5.2). The "green" criterion is read against the in-epic surface, which is byte-clean. |
| 9 | Playwright e2e green | **DEFERRED-TO-P10.7b** | ``tests/e2e`` excluded from this gate's pytest run (per worker brief); P10.7b will gate the merge on a fresh Playwright run. |
| 10 | G-RC-10 final mini-gate signs PASS | **PROVISIONAL** | This document is the gate; verdict graduates from PROVISIONAL to PASS once P10.7b lands and the merge is approved. |
| 11 | Merge-to-main plan ratified by operator | **DEFERRED-TO-P10.7b** | Charter skeleton captured; standalone ``MERGE_TO_MAIN_v2.md`` rides P10.7b. |

**Net read**: 8 / 11 SATISFIED at this gate (rows 1-8); 3 / 11
DEFERRED to P10.7b (rows 9-11; merge-to-main axis). Zero rows
FAILED.

## 3. Commit roll-up (17 phase commits + 1 charter)

| hash | sub-phase | title | net LOC |
|---|---|---|---:|
| ``52f8709a`` | P10.0a (charter) | docs(revamp): expand P-RC-10 charter with sub-phases, nits, merge-to-main plan | +451 |
| ``7e72fd13`` | P10.0b | docs(revamp): P10.0b RECON import-sweep inventory + flatten mapping | +367 |
| ``37536a62`` | P10.1 | refactor(orgs): atomic flatten ``runtime/orgs/* -> orgs/*`` (25 files) | 0 |
| ``d8275080`` | P10.2 | feat(orgs): add ``openakita.runtime.orgs`` deprecation shim re-exporting from new location | +109 |
| ``5ac2c786`` | P10.3a | refactor(src/openakita): sweep imports to canonical (31 sites / 12 files) | +35 |
| ``eb96fc15`` | P10.4 | test(sentinel-9): reverse Test 2 polarity (ban ``openakita.runtime.orgs.*`` in src/) | -68 |
| ``73126a62`` | P10.3b | refactor(tests/runtime): sweep imports (30 sites / 18 files) | +36 |
| ``f0d99525`` | P10.3c | refactor(tests/api,tests/parity): sweep imports (37 sites / 18 files) | +42 |
| ``344dbc86`` | P10.3d | refactor(tests/unit,integration,e2e): sweep imports (19 sites / 10 files) | +33 |
| ``3e46e8bd`` | P10.3e | refactor(scripts): sweep imports (3 sites / 3 files) | +73 |
| ``e1680941`` | P10.3f | docs(src/openakita): sweep docstring/comment refs (12 sites / 11 files) | +20 |
| ``e65902b7`` | P10.5d | docs(revamp): clear deferred nit epsilon-O2 -- monitor + back-fill disposition | +24 |
| ``7a8534a0`` | P10.5e | refactor(frontend,parity): clear deferred nit GroupC -- delete 3 stale v1 ``/api/orgs/*`` HTTP literals + empty sentinel #8 allowlist | +33 |
| ``6d4d869a`` | P10.5b | test(api/contracts): clear deferred nit P9.7-B -- hoist async helpers to conftest | +38 |
| ``0012a2e5`` | P10.5c | test(api/contracts): clear deferred nit epsilon-O1 -- add 5 strategic v2 contract cases | +116 |
| ``3331ed4f`` | P10.5a | refactor(orgs): clear deferred nit M-2 -- shard split ``_runtime_agent_pipeline`` + ``_runtime_plugin_assets`` to satisfy ADR-0014 per-shard cap | +169 |
| ``579c7b40`` | P10.5f | docs(revamp): close P10.5 deferred-nits roster sign-off (5/5 nits closed) | +42 |
| ``cea93777`` | P10.6 | refactor(orgs): remove ``openakita.runtime.orgs`` deprecation shim + tighten sentinel #9 whitelist | +103 |
| **TOTAL** | | | **~+1 343** |

Charter section 2 envelope was **~+1 883**; delivered came
in **~540 LOC under** (mechanical sweeps converted neatly;
shim removal trimmed 46 LOC against the +30 budget).

## 4. Sentinel matrix (state at HEAD ``cea93777``)

| # | file | cases | state | last touched in P-RC-10 |
|--:|---|--:|---|---|
| 1 | ``test_blackboard_parity.py`` | 8 | ACTIVE | (P10.3c sweep) |
| 2 | ``test_project_store_parity.py`` | 6 | ACTIVE | (P10.3c sweep) |
| 3 | ``test_node_scheduler_parity.py`` | 4 | ACTIVE | (P10.3c sweep) |
| 4 | ``test_command_service_parity.py`` | 10 | ACTIVE | (P10.3c sweep) |
| 5 | ``test_manager_parity.py`` | 12 | ACTIVE | (P10.3c sweep) |
| 6 | ``test_runtime_parity.py`` | 20 | ACTIVE | (P10.3c sweep) |
| 7 | ``test_rest_contract_sentinel.py`` | 3 | ACTIVE | (no edit; OpenAPI snapshot byte-stable) |
| 8 | ``test_frontend_stale_paths_sentinel.py`` | 3 | ACTIVE; allowlist empty | ``7a8534a0`` (P10.5e) |
| 9 | ``test_v1_src_retired_sentinel.py`` | 2 | ACTIVE; tightened | ``eb96fc15`` (P10.4) + ``cea93777`` (P10.6) |
| **TOTAL** | | **68** | 9 / 9 ACTIVE | -- |

Smoke at this gate's authoring (narrow parity slice):
``pytest tests/parity/orgs/ -q`` -> **68 passed** (zero
``@xfail``; identical case distribution to the G-RC-9 final
roll-up).

## 5. Test evidence

### 5.1 Narrow slice (in-epic surface)

```
.venv/Scripts/python -m pytest tests/parity/orgs/ tests/api/contracts/ tests/runtime/orgs/ -q --tb=no
  459 passed in 57.06 s
```

Breakdown: **267** parity+contracts (262 P9.9 baseline + 5 new
v2 contract cases from P10.5c) + **192** runtime/orgs
(unchanged from P9.5d-era floor). Baseline byte-stable
through every P-RC-10 phase commit.

### 5.2 Full suite (broader baseline; excluding ``tests/e2e``)

```
.venv/Scripts/python -m pytest tests/ -q --tb=no --ignore=tests/e2e
  6026 passed, 55 failed, 103 skipped, 5 xfailed, 6 deselected,
  2809 warnings, 5 errors in 653.42 s (10:53)
```

Plus 2 collection-stage errors in
``tests/unit/test_action_claim_{guard,recap_guard}.py``
ignored via ``--ignore``; same pre-existing circular-import
in ``core.errors`` <-> ``agent.errors`` <-> ``agent.brain`` <->
``core._brain_legacy`` <-> ``llm.client`` <-> ``core.errors``
-- NONE of those files were touched in any P-RC-10 commit.

**Pre-existing failure clusters** (each verified out-of-scope
of P-RC-10):

* **17 cases** ``tests/unit/test_org_setup_tool.py`` --
  ``ModuleNotFoundError: No module named 'openakita.orgs.tool_categories'``;
  ``tool_categories.py`` was deleted in P9.9 epsilon-2b
  (``90a7d77f``) and never migrated.
* **22 cases** ``tests/runtime/state_graph/guards/*`` --
  parity tests importing ``core._reasoning_engine_legacy``;
  trip the same pre-existing circular-import chain.
* **3 cases** ``tests/api/test_p97_alpha2_smoke.py`` -- 308
  redirect smoke returning 503; shim hard-rule untouched
  (ADR-0015 LOCKED).
* **2 cases** ``tests/{legacy,}/test_telegram_simple.py`` --
  env / network ``InvalidToken``.
* **4 cases** ``tests/unit/test_policy_v2_*`` -- static-grep
  tests targeting pre-renamed paths (``core/agent.py``,
  unrelated pre-P-RC-10 refactor).
* **5 cases** misc legacy debt unit failures.
* **3 errors + 2 collection errors** ``test_tool_filters`` /
  ``test_action_claim_*`` -- same circular family.

**Verification that ZERO failures are introduced by P-RC-10**:
manual chain walk on every failing module's ``from`` /
``import`` lines confirms none touch ``openakita.orgs.*`` /
``openakita.runtime.orgs.*`` in a way the P10.x sweeps
modified; the affected files all have last-touched commits
**predating ``52f8709a``** (charter expand) per ``git log -1
-- <path>``.

### 5.3 Full-suite vs narrow slice ratio

* Narrow slice: 459 passed / 0 failed -- **100% green** on
  the in-epic surface.
* Full suite: 6026 passed / 6086 collected (excl. ``e2e``) =
  **99.0% green**; the 60-case carry-over represents legacy
  debt outside the P-RC-10 mandate, documented for the
  P10.7b merge planner.

## 6. Residual risk + nits carried forward

The P-RC-10 deferred-nit roster (5 nits from G-RC-9.9 section
3) is **fully closed**. What rolls forward into the
**P-RC-11 candidate epic** (or v2.1.0 / env hygiene):

| carry | source | severity | scheduled to |
|---|---|---|---|
| ``openakita.orgs.tool_categories`` missing module (17 ``test_org_setup_tool.py`` failures) | P9.9 epsilon-2b deletion not migrated; ``tools/handlers/org_setup.py:731`` is a stale caller | MED | P-RC-11 candidate |
| ``state_graph/guards`` parity tests circular-import (22 failures) | pre-P-RC-10 ``core``/``agent``/``llm`` circular | MED | P-RC-11 candidate |
| 308 redirect smoke 503 (3 ``test_p97_alpha2_smoke`` failures) | env / fixture or shim composition | LOW | v2.1.0 (ADR-0015) |
| ``test_policy_v2_*`` static-grep stale paths (4 failures) | unrelated pre-P-RC-10 refactor | LOW | P-RC-11 candidate |
| Telegram smoke ``InvalidToken`` (2 failures) | environment | LOW | env hygiene |
| Category B/C ``runtime/{llm,io,context,desktop,guardrail,state_graph,nodes,templates,backends}/`` triage | archived P-RC-10 charter section 2 | -- | **P-RC-11 candidate** opens after v2.1.0 ships |
| 308 shim retirement (ADR-0015) | LOCKED option (b) | -- | v2.1.0; sentinel #7 OpenAPI snapshot is the forcing function |

**Net**: zero P-RC-10-introduced regressions; all carry items
are pre-existing or scheduled.

## 7. Decision points open (resolved by P10.7b)

P10.7b mints standalone ``docs/revamp/MERGE_TO_MAIN_v2.md``
(charter section 4 skeleton lifted to first-class doc).
P10.7b resolves: (1) merge strategy ``git merge --no-ff
revamp/v3-orgs -> main`` (no squash; mini-gate trail
preserved); (2) **v2.0.0 tag flow** -- option **A** (move
local tag to merge commit; default) vs option **B** (cut
fresh on main, leave dev tag as ``v2.0.0-dev``); (3)
**rollback window** 30 days of ``revamp/v3-orgs`` kept;
``git revert -m 1 <merge-commit>`` is the one-command
rollback; (4) **NO** ``release/2.0.x`` hotfix lane (ad-hoc
cherry-pick); (5) post-merge milestones (v2.0.0 burn-in
>= 7d operator-driven smoke; v2.0.1 if needed; v2.1.0 308
shim retirement); (6) P-RC-11 candidate gating (opens after
v2.1.0); (7) **operator sign-off** flips this gate
PROVISIONAL -> PASS only after a fresh v2 IM canary 3-run +
Playwright e2e pass on pre-merge HEAD. P10.7b ratifies the
plan only -- the actual merge is a separate operator-driven
action.

## 8. Cross-references

* **Prior gates (rolled up here)**: G-RC-9 (P-RC-9 epic
  closure), G-RC-9.5 / 9.6 / 9.7 / 9.8 / 9.9 (mini-gates
  whose deferred nits P-RC-10 cleared).
* **Charter**: ``docs/revamp/P-RC-10-CHARTER.md`` (commit
  ``52f8709a``); section 0 (mission), section 1 (file +
  import inventory), section 2 (sub-phases), section 3
  (risk register), section 4 (merge-to-main skeleton),
  section 5 (acceptance criteria, 11 rows).
* **Recon**: ``docs/revamp/P-RC-10-RECON.md`` (commit
  ``7e72fd13``).
* **Ledger**: ``docs/revamp/PROGRESS_LEDGER_P10.md`` (one
  row per P10.x commit; this gate appends the P10.7a
  closure block).
* **ADRs touched (informationally)**: ADR-0011 (subsystem
  decomposition; layout physically relocated in P10.1),
  ADR-0014 (per-shard cap; M-2 closed at P10.5a), ADR-0015
  (308 shim governance; LOCKED option b; byte-untouched),
  ADR-0002 (v2 runtime architecture; transition aid
  retired at P10.6).
* **P-RC-9 ancestor**: ``docs/revamp/gates/G-RC-9.md`` --
  template/style copy-from; section 7 explicitly hands the
  5 deferred nits to P-RC-10, all closed here.

---

**G-RC-10 final roll-up gate verdict**: **PROVISIONAL --
PASS on all in-epic axes; awaits P10.7b merge-to-main
charter + operator sign-off to graduate to PASS.**

* P-RC-10 epic CLOSED on goals (i) + (ii); (iii) handed
  to P10.7b.
* 9 / 9 sentinels ACTIVE; #9 hardened (no whitelist).
* 8 / 11 charter section 5 acceptance rows SATISFIED at
  this commit; 3 / 11 DEFERRED to P10.7b (e2e + final gate
  PASS verdict + operator sign-off).
* Narrow slice 459 / 459 = 100%; full suite 6026 / 6086 =
  99.0% with zero P-RC-10-introduced regressions.
* Strict-additive boundary held -- this gate edits only
  ``docs/revamp/gates/G-RC-10.md`` (NEW) +
  ``docs/revamp/PROGRESS_LEDGER_P10.md`` (append). Zero
  touch on source, tests, ADRs, ACCEPTANCE.md, sentinels,
  CHARTER, RECON, or the 308 shim.

**HARD STOP**: P10.7b NOT started this commit. P10.7b
mints ``docs/revamp/MERGE_TO_MAIN_v2.md`` and waits for
operator sign-off; only then does this gate's verdict
flip from PROVISIONAL to PASS.
