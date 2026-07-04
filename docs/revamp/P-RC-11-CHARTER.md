# P-RC-11 Charter -- absorb the 60 G-RC-10 carry-over failures

**Status: PLANNED.** Charter ratification commit; docs-only.
P-RC-11 is the **carry-over absorption epic** that lifts the
post-P-RC-10 full-suite baseline from **6026 passed / 60
carry-overs** (per ``docs/revamp/gates/G-RC-10.md`` section 5.2
+ section 6) to **>= 6080 passed / <= 6 residual** before the
``revamp/v3-orgs -> main`` merge ships per
``docs/revamp/MERGE_TO_MAIN_v2.md``.

**Branch**: ``revamp/v3-orgs``. **HEAD at authorship**:
``bf791dec``. **Parent epic**: P-RC-10 (CLOSED at G-RC-10
PROVISIONAL; sealed once the operator runs the merge per
P10.7b's ``MERGE_TO_MAIN_v2.md``). **Promoted from**: the
"after v2.1.0 ships" slot per archived
``P-RC-10-CHARTER.md.archived`` section 0 + P-RC-10 charter
section 7 ("P-RC-11 candidate -- deferred Category B/C
runtime/* triage from archived charter; opens after v2.1.0
ships"); user has elected to **execute P-RC-11 BEFORE the
merge-to-main lands** so v2.0.0 ships with a cleaner test
baseline.

> **Scope re-shape vs the archived "P-RC-11 candidate"**:
> the archived charter scoped P-RC-11 to a wider
> ``runtime/{llm,io,context,desktop,guardrail,state_graph,
> nodes,templates,backends}/`` Category B/C triage. **This
> charter narrows P-RC-11 to a single mission: absorb the 60
> G-RC-10 section 6 carry-overs.** The wider Category B/C
> triage rolls forward to a future P-RC-12 candidate (not
> committed here).

## 0. Executive summary

P-RC-10 closed every in-epic axis at G-RC-10 PROVISIONAL but
left a ledger of **60 pre-existing failures** as carry-overs
(55 failed + 5 errors in the full-suite, plus 2 collection
errors ignored via ``--ignore`` per G-RC-10 section 5.2).
Each carry-over was verified at G-RC-10 to be **NOT
introduced by P-RC-10** (chain-walk on every affected file's
``git log -1`` timestamp predates ``52f8709a`` the P10.0a
charter expand). P-RC-11 (i) restores or retires each
carry-over until the full suite reads >= 6080 passed
(99.9 %+) **OR** the residual is documented as locked-out
(308 shim tests xfail-pinned with ADR-0015 reference); (ii)
preserves the v2.0.0 release narrative -- **zero source
edits to the 308 redirect shim**
(``api/routes/_orgs_v2_legacy_redirects.py``); (iii) ships
its own roll-up gate ``docs/revamp/gates/G-RC-11.md`` so
the operator's pre-merge ``MERGE_TO_MAIN_v2.md`` section 3
checklist row 1 ("narrow slice + full suite green") signs
without the "carry-over" footnote that haunted G-RC-10.

## 1. Epic goals + non-goals

### 1.1 Goals (in scope)

* **(i) Clear G-RC-10 section 6 carry-over ledger.** 60
  cases / 7 clusters (see section 2 + the recon doc
  ``docs/revamp/P-RC-11-RECON.md``). Each cluster gets one
  sub-phase commit. Clearance = test passes OR test is
  retired with explicit ADR/ledger reference (not silent
  ``@xfail``).
* **(ii) Lift baseline from 6026 to >= 6080.** With the 17
  Cluster A (``tool_categories``) + 22 Cluster B
  (``state_graph/guards/*``) + 4 Cluster D
  (static-grep) + 2 Cluster E (telegram) + 5 Cluster F
  (misc) + 3 + 2 Cluster G (collection-stage) cases coming
  back online, the 60-case carry budget compresses to <= 6
  (the 3 Cluster C 308-shim tests stay xfailed and are
  scored as "scheduled-out"). Net delta to the baseline
  count: **+54 passed / +0 failed / +3 xfail / +3 retired
  or absorbed**.
* **(iii) Zero P-RC-11-introduced regressions.** The
  charter's strict-additive boundary (mirror P-RC-10
  section 0 hard rule): every commit re-runs the narrow
  slice ``pytest tests/parity/orgs/ tests/api/contracts/
  tests/runtime/orgs/ -q`` (459 baseline) before the
  cluster-specific test added by that commit; sentinels
  #1..#9 stay green; OpenAPI byte-stable; v2 IM canary
  delta within +-5 % of the 1.92 s baseline.

### 1.2 Non-goals (explicit out of scope)

* **308 shim retirement** -- LOCKED to v2.1.0 per
  ``docs/adr/0015-308-shim-retirement-governance.md``
  option (b); ``api/routes/_orgs_v2_legacy_redirects.py``
  stays byte-untouched; sentinel #7 (OpenAPI snapshot)
  remains the forcing function. The 3 Cluster C
  ``test_p97_alpha2_smoke`` failures get ``@pytest.mark.
  xfail(reason="308 shim retirement locked for v2.1.0;
  see ADR-0015")`` -- 5-LOC test edit, no production-code
  edit.
* **Wider runtime/* triage** -- the archived
  ``P-RC-10-CHARTER.md.archived`` Category B/C
  ``runtime/{llm,io,context,desktop,guardrail,state_graph,
  nodes,templates,backends}/`` triage that the original
  P-RC-11-candidate scoped is **deferred to a future
  P-RC-12 candidate**, post v2.1.0.
* **No agent / api / channels package re-layout.** Cluster B
  fix touches **import order only** (lazy-load shim in
  ``agent/__init__.py`` or ``core/errors.py``); zero
  package boundary changes.
* **No ADR edits.** P-RC-11 references ADR-0015 (Cluster
  C xfail), ADR-0011 (subsystem decomposition; informational),
  ADR-0014 (per-shard cap; informational); all three stay
  byte-untouched. Any new architectural decision the epic
  surfaces gets parked into a P-RC-11 mini-charter, not an
  ADR.

## 2. Sub-phase breakdown (P11.0 .. P11.7 -- 8 cluster commits + 2 planning + 1 gate = ~11)

Mirrors the P-RC-10 cadence (P10.0a charter -> P10.0b recon
-> P10.1..P10.6 work -> P10.7 final gate). One cluster per
sub-phase; cluster ordering by **dependency + value**
(section 4.1 risk register).

* **P11.0a -- Charter (this commit).** Drafts this charter
  + opens ``docs/revamp/PROGRESS_LEDGER_P11.md``. Docs-only.
  Zero source / test / sentinel touch.
* **P11.0b -- Reconnaissance.** Drafts
  ``docs/revamp/P-RC-11-RECON.md`` -- one section per
  cluster with: failing test enumeration / verified root
  cause / affected source files / fix strategy / LOC
  estimate / inter-cluster dependencies. Docs-only.
* **P11.1 -- Cluster A: restore ``openakita.orgs.tool_categories``.**
  Re-instates the deleted module (P9.9 epsilon-2b
  ``90a7d77f``; 149 LOC) as
  ``src/openakita/orgs/_runtime_tool_categories.py``
  (private shard naming follows the P10.5a M-2 split
  convention) + a 5-LOC public re-export
  ``src/openakita/orgs/tool_categories.py``. Restores the
  17 ``tests/unit/test_org_setup_tool.py`` cases + the 1
  ``TestGetResources::test_returns_tool_categories`` that
  G-RC-10 mis-bucketed under Cluster F. Net **~+155 LOC**.
* **P11.2 -- Cluster B + G: break the
  ``core/errors`` circular import.** The cycle (verified
  at this charter's authorship by direct import probe;
  see recon section 2) is
  ``core.errors -> agent.__init__ -> agent.brain ->
  core._brain_legacy -> llm.client -> core.errors`` --
  closed by ``agent/__init__.py:15`` eagerly importing
  ``Brain``. Fix: convert ``agent/__init__.py`` to PEP-562
  ``__getattr__`` lazy loader (or move the
  ``UserCancelledError`` shim out of
  ``core/errors`` -> ``agent.errors`` re-export by
  inlining one ``class UserCancelledError`` definition in
  ``core/errors.py`` and turning the existing import into
  a delayed ``__getattr__``). Restores the **22 Cluster B
  ``tests/runtime/state_graph/guards/`` cases + 3 Cluster
  G ``test_tool_filters`` errors + 2 Cluster G
  ``test_action_claim_*`` collection errors = 27 cases
  total**. Net **~+15 / -5 = +10 LOC**.
* **P11.3 -- Cluster C: xfail the 3 ``test_p97_alpha2_smoke``
  308-shim tests.** Adds ``@pytest.mark.xfail(reason="308
  shim retirement locked for v2.1.0 -- see ADR-0015 option
  (b); test re-enables when ``api/routes/
  _orgs_v2_legacy_redirects.py`` retires")`` to the 3
  affected tests. **No source edit** -- the 308 shim
  itself stays byte-untouched per non-goal in section
  1.2. Net **~+5 LOC** (three 1-line decorators + a
  module-level ADR-0015 comment).
* **P11.4 -- Cluster D: fix the 4 ``test_policy_v2_*``
  static-grep tests.** Three of the four read
  ``src/openakita/core/agent.py``, which was renamed to
  ``_agent_legacy.py`` in commit ``32c29c54`` (long before
  P-RC-10). One test
  (``test_policy_v2_c8b3::test_agent_cleanup_migrated``)
  + two tests
  (``test_policy_v2_c8b5::test_agent_py_no_v1_is_trust_mode_call``,
  ``::test_check_trust_mode_skip_is_pure_v2``) repoint to
  ``_agent_legacy.py``; the fourth
  (``test_policy_v2_c13::test_tool_executor_security_confirm_marker_has_no_c13_fields``)
  is collateral damage of Cluster B and self-clears once
  P11.2 lands. Net **~+10 / -10 = +0 LOC** (path
  string-edit only).
* **P11.5 -- Cluster E: gate the 2 telegram smoke
  tests.** ``tests/legacy/test_telegram_simple.py`` +
  ``tests/test_telegram_simple.py`` already have a
  module-level ``pytest.skip(...)`` if
  ``TELEGRAM_BOT_TOKEN`` is missing; the env-var presence
  but **invalid-value** path still raises ``InvalidToken``.
  Fix: tighten the skip predicate -- skip if token
  missing **or** matches a placeholder pattern
  (``"<...>"``, empty after strip, or fewer than the
  Telegram bot-token minimum 35 chars). Net **~+10 LOC**.
* **P11.6 -- Cluster F: misc legacy cleanup.** 5 cases
  rolled up from G-RC-10 section 5.2 row "5 cases misc
  legacy debt unit failures" (heterogeneous;
  ``test_c17_*``, ``test_c23_*``, ``test_memory_manager``,
  remainder of ``TestGetResources`` after Cluster A
  clears the tool_categories one). Approached
  case-by-case at the recon doc (section 6); each is a
  small fix or an explicit retire. Net **~+30 / -10 = +20
  LOC**.
* **P11.7a -- G-RC-11 final gate.** Authors
  ``docs/revamp/gates/G-RC-11.md`` rolling up P11.0..P11.6:
  cluster-by-cluster clearance evidence, full-suite
  before/after counts, narrow slice + canary parity, the
  3 xfail-pinned 308-shim cases scored as
  "ADR-0015-locked-out" (not regression), sentinel matrix
  unchanged, OpenAPI byte-stable. Verdict: PASS once
  every cluster commit lands and full-suite reads >=
  6080 passed / <= 6 residual / <= 5 xfail. Docs-only.
  Mini-gate also closes any nit-roster the cluster
  commits defer.

### 2.1 Sub-phase LOC envelope

| sub-phase | est ins | est del | net |
|---|--:|--:|--:|
| P11.0a (charter; this commit) | ~+390 | 0 | +390 |
| P11.0b (recon) | ~+490 | 0 | +490 |
| P11.1 (Cluster A) | ~+155 | 0 | +155 |
| P11.2 (Cluster B + G) | ~+15 | ~-5 | +10 |
| P11.3 (Cluster C xfail) | ~+5 | 0 | +5 |
| P11.4 (Cluster D) | ~+10 | ~-10 | 0 |
| P11.5 (Cluster E) | ~+10 | 0 | +10 |
| P11.6 (Cluster F) | ~+30 | ~-10 | +20 |
| P11.7a (G-RC-11 gate) | ~+250 | 0 | +250 |
| **P-RC-11 total** | **~+1 355** | **~-25** | **~+1 330** |

Order of magnitude similar to P-RC-10 (~+1 343 net), with
the bulk in docs (charter + recon + gate = ~+1 130) and
~+200 in source/test fixes.

### 2.2 Sub-phase commit count

8-11 commits over ~3-5 days: P11.0a + P11.0b (planning, 2),
P11.1 .. P11.6 (cluster fixes, 6), P11.7a (gate, 1), with
~2 buffer commits for split-by-cluster fix-up if any
cluster exceeds 200 LOC.

## 3. Acceptance criteria

| # | criterion | how verified |
|--:|---|---|
| 1 | All 7 carry-over clusters CLOSED with commit refs in ``PROGRESS_LEDGER_P11.md`` | per-row ledger entries P11.1..P11.6 |
| 2 | Full-suite (excl. ``tests/e2e``) >= 6080 passed | ``pytest tests/ -q --tb=no --ignore=tests/e2e`` at G-RC-11 |
| 3 | Residual failures <= 6 (including the 3 xfailed 308-shim cases) | same pytest run; tally vs G-RC-10 baseline (60 -> <= 6) |
| 4 | Narrow slice still 459 / 459 = 100 % | ``pytest tests/parity/orgs/ tests/api/contracts/ tests/runtime/orgs/ -q`` at every commit boundary |
| 5 | Sentinels #1..#9 ACTIVE; OpenAPI byte-stable; 308 shim file byte-untouched | ``pytest tests/parity/orgs/ -q`` + ``git log -- api/routes/_orgs_v2_legacy_redirects.py`` shows no P-RC-11 hash |
| 6 | v2 IM canary 3x within +-5 % of 1.92 s baseline | ``pytest tests/integration/test_v2_im_canary_e2e.py`` x3 at G-RC-11 |
| 7 | G-RC-11 final mini-gate signs PASS | ``docs/revamp/gates/G-RC-11.md`` verdict |

7 rows; mirrors G-RC-10 section 2's PROVISIONAL row pattern
(rows 9-11 there were merge-gated, here all 7 are PASS-able
in-epic).

## 4. Risk register

* **R-11-1 (MED) -- Cluster B import-order fix breaks an
  import chain elsewhere.** ``agent/__init__.py`` lazy
  loader changes the moment ``Brain`` first resolves; some
  caller may rely on eager import. **Mitigation**: P11.2
  lands in a dedicated commit; pre-commit dry-run = full
  pytest collect-only; post-commit narrow slice + canary
  3x; revert is a single ``git revert`` if any sentinel
  fails.
* **R-11-2 (LOW) -- Cluster A tool_categories recovery
  picks the wrong shard naming.** Two viable options: (a)
  re-instate at the original ``openakita.orgs.
  tool_categories`` path verbatim; (b) ``_runtime_
  tool_categories`` private shard + a 5-LOC public
  re-export shim (matches the P10.5a M-2 split
  convention). **Decision**: option (b); recon doc
  section 1 pins this and the import sites get one
  ``re.sub`` rewrite each. **Mitigation**: 4 known
  callers (``agents/factory.py:370``, ``orgs/
  _runtime_templates.py:1634`` (comment only), ``tools/
  handlers/org_setup.py:129/440/731``) get one targeted
  edit each; post-commit static grep
  ``git grep tool_categories`` zero outside the new
  shard.
* **R-11-3 (LOW) -- Cluster C xfail mis-scoring.**
  Marking a test ``@pytest.mark.xfail`` with ``strict=
  False`` could hide a regression if the 308 shim
  unexpectedly starts working. **Mitigation**: use
  ``strict=True`` so an unexpected pass FAILS the suite
  -- forces an explicit ledger entry the day v2.1.0 lands
  and the 3 tests need un-xfailing. ADR-0015 stays the
  source of truth.
* **R-11-4 (LOW) -- Cluster F heterogeneous.** Five
  different test files, each with a distinct legacy
  cause. **Mitigation**: recon doc section 6 enumerates
  one root-cause line per file; if any one of the 5 needs
  >50 LOC, it splits into its own P11.6.x sub-commit
  rather than blocking the cluster commit.
* **R-11-5 (LOW) -- pre-merge timing pressure.** P-RC-11
  is now executing **before** the
  ``MERGE_TO_MAIN_v2.md`` operator session lands; if any
  P11.x commit slips, the merge waits. **Mitigation**:
  prioritisation table in section 4.1 below; clusters C
  + D + E are <= 30 LOC each and can land in one session;
  cluster A + B are the high-value high-LOC pair and ship
  first; cluster F can split or descope without blocking
  the gate.

### 4.1 Cluster prioritisation (highest value first)

| order | cluster | tests cleared | LOC | rationale |
|--:|---|--:|--:|---|
| 1 | A (tool_categories) | 17 + 1 | ~+155 | largest single restoration; clean root-cause; unblocks orgs/agent factory codepaths |
| 2 | B + G (circular import) | 22 + 3 + 2 = 27 | ~+10 | second-largest count; one-line lazy-loader fix; unblocks any future ``core/agent.brain`` test that touches ``_reasoning_engine_legacy`` |
| 3 | D (static-grep) | 4 (3 real + 1 collateral) | ~0 | trivial path string-edits; prerequisite for any future ``core/_*_legacy.py`` rename |
| 4 | C (xfail 308) | 3 | ~+5 | smallest commit; documents the ADR-0015 lock-out cleanly |
| 5 | E (telegram) | 2 | ~+10 | env-hygiene; safe |
| 6 | F (misc legacy) | 5 | ~+20 | last; heterogeneous; can descope |

Total cleared: 17 + 1 + 27 + 4 + 3 + 2 + 5 = **59 of the 60
carry-overs**; the 1 not-cleared is the residual budget for
any P-RC-11-introduced flake.

## 5. Cross-references

* ``docs/revamp/gates/G-RC-10.md`` -- parent gate; section
  5.2 enumerates the 60-case carry-over distribution that
  this charter absorbs; section 6 hands clusters A / B / D
  / E / F to "P-RC-11 candidate" and locks cluster C to
  v2.1.0.
* ``docs/revamp/P-RC-10-CHARTER.md`` -- shape template
  (sections 0 / 2 / 5 here mirror its P-RC-10 equivalents;
  content differs).
* ``docs/revamp/P-RC-10-CHARTER.md.archived`` -- prior
  P-RC-11-candidate slot ("after v2.1.0 ships"); this
  charter formally **promotes** P-RC-11 ahead of the
  merge per the user's directive captured in the
  ``MERGE_TO_MAIN_v2.md`` pre-merge checklist.
* ``docs/revamp/MERGE_TO_MAIN_v2.md`` -- the merge charter
  authored at P10.7b; section 3 row 1 ("narrow slice +
  full suite green") signs cleaner once P-RC-11 closes.
* ``docs/adr/0015-308-shim-retirement-governance.md`` --
  the LOCKED option (b) v2.1.0 retirement charter that
  Cluster C points its ``xfail`` reason string at.
* ``docs/adr/0011-org-subsystem-decomposition.md`` --
  informational; Cluster A's new
  ``_runtime_tool_categories.py`` shard slots into the
  6-subsystem layout per ADR-0011 without contract change.
* ``docs/adr/0014-orgruntime-budget-revision.md`` --
  informational; new shard ~149 LOC sits well under the
  ADR-0014 per-shard soft cap.
* ``docs/revamp/P-RC-11-RECON.md`` -- companion recon
  doc to be authored at P11.0b; one section per cluster.
* ``docs/revamp/PROGRESS_LEDGER_P11.md`` -- new ledger
  for this epic (P-RC-10 used PROGRESS_LEDGER_P10.md;
  P-RC-11 starts a fresh file in the same per-epic
  pattern as PROGRESS_LEDGER_P9.md / _P10.md).

---

**Charter ratification commit log**: P-RC-11 P11.0a --
this commit. Docs-only. Zero touch on ``src/``,
``tests/``, ``apps/``, ``scripts/``, ADRs, gate docs,
ACCEPTANCE.md, the 308 shim
(``api/routes/_orgs_v2_legacy_redirects.py``),
``MERGE_TO_MAIN_v2.md``, P-RC-10-CHARTER, P-RC-10-RECON,
G-RC-10, or any sentinel file. Pre-approves the section
2 sub-phase plan + section 4.1 prioritisation; awaits
operator green-light before P11.0b recon doc opens.
