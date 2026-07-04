# P-RC-9 P9.8 Charter -- caller migration (planning round)

**Status: PLANNED, NOT EXECUTED.** Planning charter for P9.8,
the eighth phase of P-RC-9 (per ``docs/revamp/P-RC-9-PLAN.md``
section 4 P9.8). P9.8 swaps HTTP callers (frontend +
strict-additive supporting tests) from the v1
``/api/orgs/...`` surface to the v2 ``/api/v2/orgs/...`` mint
+ ``/api/v2/orgs-spec/...`` Group A relocation shipped in
P9.7 (G-RC-9.7 PASS at HEAD ``4b8a9ad8``).

**Branch**: ``revamp/v3-orgs``.
**HEAD at authorship**: ``4b8a9ad8`` (G-RC-9.7 + P9.7.nit-a +
P9.7.nit-b closed; 83 / 83 v2 mint endpoints active; 9 / 9
Group A relocated; 9 / 9 308 shims live; 7 / 7 sentinels
ACTIVE; main gate 6 853 passed / 14 failed (12 carry-over +
2 P-RC-9 root-caused-and-closed)).
**Scope**: planning artifacts only. ``git diff
4b8a9ad8..HEAD -- src/openakita/ tests/ apps/`` is empty bytes
both before and after this commit.

## 1. Scope summary -- what P9.8 does and does NOT do

### 1.1 P9.8 DOES

* **Frontend HTTP path swap**: rewrite every
  ``/api/orgs/...`` literal in ``apps/setup-center/src/`` to
  the equivalent ``/api/v2/orgs/...`` mint path (60 hits
  across 12 files; section 4).
* **Group A relocation rewire** in the already-v2 frontend
  layer: 10 hits in ``apps/setup-center/src/api/orgs.ts``
  (8) + ``api/v2Stream.ts`` (2) currently call
  ``/api/v2/orgs[/...]``; per D-1 R3 LOCKED
  (``P-RC-9-P9.7-DECISIONS.md``) these endpoints relocated
  to ``/api/v2/orgs-spec[/...]`` in P9.7a-2a. The 308 shim
  hides this from callers, but P9.8 rewires the literals to
  the canonical ``/api/v2/orgs-spec/...`` target so the
  v2.1.0 shim retirement (P9.9 or later) is a no-op.
* **Test fixture / mock catch-up**: ~3 frontend test
  references in ``apps/setup-center/src/api/__tests__/`` and
  ``components/__tests__/`` follow the swap.
* **Caller inventory catalogue** (alpha-1 deliverable):
  docs-only file listing every swap site by file:line, with
  the v1->v2 path mapping per cluster.
* **Optional 8th sentinel** (section 7): activate a
  collection-time grep sentinel asserting **0 stale**
  ``/api/orgs/`` references in ``apps/setup-center/src/``.

### 1.2 P9.8 does NOT

* **Touch ``src/openakita/api/routes/orgs.py``** (v1
  endpoint file; 2 533 LOC, 89 endpoints). Stays mounted
  through P9.8 so out-of-tree callers (debug scripts,
  legacy bookmarks, third-party operators on an older
  ``dist-web``) keep working. **P9.9** deletes or 410-Gone-
  shims it per Q-B ACCEPTED (b).
* **Touch ``src/openakita/orgs/``** (v1 subsystem package,
  ~18 000 LOC). Strict-additive boundary; whole namespace
  BYTE-LEVEL untouched. P9.9 deletes it wholesale.
* **Rewrite ``from openakita.orgs.X`` Python imports**.
  PLAN section 4 P9.8 (vintage) scoped this here (~86 src
  + 216 test sites). **Updated stance**: import rewrite
  **deferred to P9.9** because it must land alongside the
  v1 deletion; doing it earlier produces a half-state
  where both v1 and v2 symbols co-exist under two names.
  Ledger header at G-RC-9.7 close already anticipated
  this split (P9.8 = HTTP callers; P9.9 = Python imports
  + physical deletion).
* **Migrate ``tests/orgs/*`` v1-path-asserting tests**
  (56 v1 hits across 5 files: ``test_api.py``,
  ``test_prompt_api_e2e.py``, ``test_transparency_autonomy.py``,
  ``test_org_status_snapshot.py``,
  ``test_runtime_deadlock_watchdog.py``). They assert the
  v1 endpoint contract directly and are deleted as a block
  by P9.9. Migrating to v2 paths would invent coverage that
  ``tests/api/contracts/`` (184 cases) already provides.
* **Touch ``src/openakita/channels/``**. Recon (section
  4): **0 HTTP callers** under ``channels/``; every
  reference to OrgRuntime / OrgCommandService is an in-
  process import (5 sites in ``gateway.py``). The IM
  gateway is an in-process subsystem consumer; its imports
  ride with the P9.9 sweep.

## 2. Risk analysis (P9.8-specific; NOT carry-over from P9.6/P9.7)

* **R1 -- Frontend deployment lag (HIGH, certain)**. The
  Tauri / Vite ``dist-web`` artefact is bundled at build
  time; a user upgrading the backend but running an older
  ``dist-web`` (cached browser, sideloaded desktop shell)
  keeps hitting ``/api/orgs/...``. **Mitigation**: v1
  endpoints stay mounted through P9.8 + the v2.0.x window
  (Q-B ACCEPTED (b)); P9.9 hard-deletes v1 only after a
  documented bake-in. Operator can roll back frontend
  bundle if needed.
* **R2 -- TypeScript / Pydantic schema drift (MEDIUM)**.
  Frontend defines ad-hoc ``OrgWire`` /
  ``TemplateNodeWire`` / ``V2StreamEvent`` interfaces in
  ``api/orgs.ts`` + ``v2Stream.ts`` that pre-date P9.7's
  ``schemas/orgs_v2/`` Pydantic shapes. **Mitigation**:
  alpha-1 documents the TS-vs-Pydantic field diff per
  cluster. No OpenAPI codegen mandated this round; the
  308 shim is schema-transparent (body verbatim), so v1
  hits in ``OrgEditorView.tsx`` etc. only need path swaps
  (shape mismatches were already a v1 problem and don't
  regress).
* **R3 -- IM gateway blast radius (LOW, confirmed)**.
  Section 4 recon: **0 HTTP callers** in ``channels/``;
  ``gateway.py`` reaches OrgCommandService / OrgRuntime
  **in-process** (5 ``from openakita.orgs.command_service``
  imports). P9.8 records the no-op beta verdict; import
  rewrite defers to P9.9.
* **R4 -- SSE behaviour drift (LOW)**. ``v2Stream.ts``
  already targets ``/api/v2/orgs/{id}/stream``; P9.8
  rewires to ``/api/v2/orgs-spec/{id}/stream``. Endpoint
  body byte-identical (same ``orgs_v2_stream.router``
  under a different prefix as of P9.7a-2a); 308 preserves
  SSE upgrade headers per RFC 7538. The mocked-EventSource
  test (``api/__tests__/v2Stream.test.ts``) gets a path-
  string update in the same commit.
* **R5 -- Scatter hits in views/ + components/ (MEDIUM)**.
  60 v1 hits across 12 files (top: ``OrgEditorView.tsx``
  20, ``OrgProjectBoard.tsx`` 11, ``OrgChatPanel.tsx`` 7,
  ``OrgMonitorPanel.tsx`` 5, ``OrgInboxSidebar.tsx`` 4).
  Each is a literal template-string; **no centralised URL
  builder** (D-2). Mitigation: gamma-* commits batched by
  file / component cluster, each <= 350 LOC.
* **R6 -- Stale references in committed snapshot (LOW)**.
  ``tests/parity/orgs/_openapi_snapshot.json`` carries 76
  v2 path keys (P9.7 sentinel anchor); NOT a caller; not
  in scope of P9.8 swap. Recorded here to pre-empt false
  positives during 8th-sentinel implementation.

## 3. Phase breakdown (alpha / beta / gamma / delta)

### P9.8alpha -- caller inventory (1 commit, docs only)

**alpha-1** (~200-250 LOC): write
``docs/revamp/P-RC-9-P9.8-CALLER-INVENTORY.md`` -- per
file:line table mapping every ``/api/orgs/...`` /
``/api/v2/orgs/...`` hit to its v2 target path, method,
and cluster (orgs / nodes / dispatch / state / ops /
projects / spec). Plus the 3-file test catch-up list.
Mirrors P9.7a-1 ENDPOINT-INVENTORY. No source / test
edits this turn.

### P9.8beta -- backend caller confirmation (0-1 commits)

**beta-1** (OPTIONAL, ~50 LOC, docs only): write
``docs/revamp/P-RC-9-P9.8-CHANNELS-RECON.md`` -- 5-line
table of the 5 ``from openakita.orgs.command_service``
imports in ``gateway.py`` + explicit no-op verdict +
P9.9 follow-up pointer. May be inlined into alpha-1
if alpha-1 stays <= 320 LOC.

### P9.8gamma -- frontend caller swap (3-4 commits)

One commit per file-cluster, sized under 350 LOC each:

| commit | files | v1 hits | est LOC |
|---|---|--:|--:|
| gamma-1 | ``apps/setup-center/src/api/orgs.ts`` + ``api/v2Stream.ts`` + ``api/__tests__/v2Stream.test.ts`` (Group A -> ``-spec``) | 10 +  1 test | ~80 |
| gamma-2 | ``views/OrgEditorView.tsx`` (largest single file) | 20 | ~80-120 |
| gamma-3 | ``components/OrgProjectBoard.tsx`` + ``OrgChatPanel.tsx`` | 18 | ~70 |
| gamma-4 | ``components/{OrgMonitorPanel,OrgInboxSidebar,OrgBlackboardPanel,OrgDashboard,TemplatePickerDrawer,WorkbenchNodePicker}.tsx`` + ``views/{ChatView,PixelOfficeView}.tsx`` + 2 test mocks | 19 | ~80 |

Gamma total: **3-4 commits / ~250-350 LOC of frontend
deltas**. Each commit runs ``npm run lint`` +
``npm run test`` for the touched files; backend pytest
unchanged (P9.8 is frontend-additive).

### P9.8delta -- smoke + G-RC-9.8 gate (1-2 commits)

* **delta-1** (OPTIONAL, ~100 LOC, ``tests/`` additive
  only): add ``tests/parity/orgs/test_frontend_stale_paths_sentinel.py``
  -- the 8th sentinel (section 7). Skipped if section 7
  decision is "defer to P9.9".
* **delta-2** (gate; ~250-300 LOC): write
  ``docs/revamp/gates/G-RC-9.8.md`` mini-gate (mirrors
  G-RC-9.7 structure) + ledger close summary. Asserts:
  every section-4 caller site is on the v2 path; 7 (or 8)
  sentinels remain ACTIVE; ``apps/`` diff is the only
  source delta vs HEAD ``4b8a9ad8``; v1 routes still mount.

### Phase totals

**5-7 commits / ~750 LOC total** (~200 alpha + 50 beta +
350 gamma + 100 sentinel + 250 gate). Inside ADR-0014's
~10 % planning tolerance for a "small wiring" phase;
~1/4 of P9.7's 2 845 LOC; ~1/3 of G-RC-9.6's gamma turn.

## 4. Caller site inventory (rough; alpha-1 refines)

Measured 2026-05-20 on HEAD ``4b8a9ad8`` with ``git grep
-nE "/api/(v2/)?orgs"`` against the four target trees:

| tree | v1 hits | v2 hits | total | notes |
|---|--:|--:|--:|---|
| ``apps/setup-center/src/`` | **60** | **17** | 77 | 12 files (v1); 6 files (v2; 10 are Group A in orgs.ts + v2Stream.ts to retarget to ``-spec``) |
| ``src/openakita/channels/`` | **0** | **0** | 0 | no HTTP callers; in-process imports only (5 ``from openakita.orgs.command_service`` in gateway.py) |
| ``tests/`` | 56 | 463 | 519 | v1 hits in tests/orgs/* (v1-surface tests deleted by P9.9); v2 in tests/api/contracts/ + tests/integration/ already correct |
| ``docs/`` | -- | -- | 86 | docs references; out-of-scope for P9.8 swap; updated in narratives only when ledger / gate text adds new context |
| ``scripts/`` | 0 | 0 | 0 | -- |

### 4.1 Frontend v1 hits per file (60)

| file | hits |
|---|--:|
| ``views/OrgEditorView.tsx`` | 20 |
| ``components/OrgProjectBoard.tsx`` | 11 |
| ``components/OrgChatPanel.tsx`` | 7 |
| ``components/OrgMonitorPanel.tsx`` | 5 |
| ``components/OrgInboxSidebar.tsx`` | 4 |
| ``views/PixelOfficeView.tsx`` | 3 |
| ``components/OrgBlackboardPanel.tsx`` | 2 |
| ``views/ChatView.tsx`` | 2 |
| ``components/__tests__/TemplatePickerDrawer.test.tsx`` | 2 |
| ``components/WorkbenchNodePicker.tsx`` | 2 |
| ``components/TemplatePickerDrawer.tsx`` | 1 |
| ``components/OrgDashboard.tsx`` | 1 |

### 4.2 Frontend v2 hits per file (17; 10 retarget to ``-spec``)

| file | hits | action |
|---|--:|---|
| ``api/orgs.ts`` | 8 | retarget to ``-spec`` (Group A wrappers) |
| ``api/v2Stream.ts`` | 2 | retarget to ``-spec`` (SSE) |
| ``api/__tests__/v2Stream.test.ts`` | 1 | retarget to ``-spec`` (mock URL) |
| ``components/OrgChatPanel.tsx`` | 2 | already v2 mint; no change |
| ``components/TemplatePickerDrawer.tsx`` | 2 | already v2 mint (template list); no change |
| ``views/OrgEditorView.tsx`` | 2 | already v2 mint (createOrgV2 wrapper); no change |

## 5. Migration strategy per call-site type

* **Frontend TypeScript** (``apps/setup-center/src/``):
  literal-string find-and-replace per file, with code
  review for the 20-line ``OrgEditorView.tsx`` cluster.
  Path-only swap; request body / response shape NOT
  changed (v2 mint preserves v1 envelopes by D-3 +
  ``schemas/orgs_v2/`` Pydantic shapes). No TypeScript
  interface migrations required; existing ``OrgWire`` /
  ``TemplateNodeWire`` stays.
* **Group A wrappers** (``api/orgs.ts`` +
  ``api/v2Stream.ts``): replace ``apiUrl(apiBase, "api",
  "v2", "orgs", ...)`` with ``apiUrl(apiBase, "api", "v2",
  "orgs-spec", ...)``. SSE URL string literal in
  ``v2Stream.ts`` line 110 swaps ``/api/v2/orgs/`` ->
  ``/api/v2/orgs-spec/``. The 308 shim continues to redirect
  any other caller through v2.0.x.
* **IM gateway adapter**: **no HTTP calls to swap** (R3).
  P9.8beta-1 docs commit confirms the 0-callsite result;
  the 5 in-process imports stay v1-named through P9.8 and
  rewrite alongside v1 deletion in P9.9.
* **Tests in ``tests/orgs/*``**: NO migration. These assert
  v1 endpoint behaviour and are deleted by P9.9. Migrating
  to v2 paths would invent coverage that
  ``tests/api/contracts/`` (184 cases; P9.7gamma-1) already
  provides. P9.9a-audit audits each ``tests/orgs/*`` file
  to confirm its v2 equivalent exists before deletion.
* **Tests in ``apps/setup-center/src/.../__tests__/``**: 3
  hits get path-string updates with the production code in
  the same commit (gamma-1 + gamma-4).
* **canary** (``tests/integration/test_v2_im_canary_e2e.py``):
  ALREADY swapped in P9.7.nit-a (``652c8a71``); no
  follow-up needed. P9.8 verifies its 1-pass green state.
* **Backend Python** (``src/openakita/api/routes/orgs.py``
  v1 + ``src/openakita/orgs/`` v1 subsystem): BYTE-LEVEL
  UNTOUCHED through P9.8.

## 6. LOC budget

| phase | est LOC | basis |
|---|--:|---|
| alpha-1 inventory doc | ~250 | 60+17 hit table + cluster mapping + type-drift table |
| beta-1 channels recon (optional) | ~50 | 5-row table + verdict |
| gamma-1..4 frontend swap (3-4 commits) | ~250-350 | path-only edits across 12 prod + 3 test files |
| delta-1 8th sentinel (OPTIONAL) | ~100 | ``tests/parity/orgs/test_frontend_stale_paths_sentinel.py`` |
| delta-2 G-RC-9.8 gate + ledger close | ~280 | mirrors G-RC-9.7 scaled to lighter phase |
| **Total** | **~700-950** | -- |

Compare: P9.7 = 2 845 LOC / 17 commits. P9.8 = ~750 LOC /
5-7 commits; **per-commit average ~125-180 LOC**, well
under the 350 soft cap.

## 7. Sentinel strategy

**6 P-RC-9 parity sentinels (P9.1c-P9.6gamma) + 1 P9.7 REST
contract sentinel = 7 ACTIVE today**. P9.8 introduces 0
parity sentinels.

**8th sentinel decision: ADOPT in P9.8delta-1.** Pattern
mirrors the P9.7 REST contract sentinel (collection-time
grep; no test execution required):

```python
# tests/parity/orgs/test_frontend_stale_paths_sentinel.py
def test_frontend_has_zero_v1_orgs_paths() -> None:
    """No /api/orgs/... literal remains in apps/setup-center/src/.

    Asserts P9.8 caller migration completion. Allowed: paths under
    /api/v2/orgs/... (mint) and /api/v2/orgs-spec/... (Group A).
    """
    root = Path("apps/setup-center/src")
    stale: list[tuple[Path, int, str]] = []
    for file in root.rglob("*.ts*"):
        for n, line in enumerate(file.read_text("utf-8").splitlines(), 1):
            if re.search(r"/api/orgs(?!_v2|-spec|/v2|/[a-z])", line) and \
               "/api/v2/orgs" not in line:
                stale.append((file, n, line.strip()))
    assert not stale, f"v1 /api/orgs/ references remain: {stale}"
```

Rationale:
* P9.9 deletes v1 endpoints; without the sentinel, a stale
  frontend literal would silently 404 against v1's absence.
* Cheap (file scan; ~30 ms in collection); same blast
  radius as the existing REST contract sentinel.
* Rejecting reason ("scope creep into apps/") rebutted by
  the sentinel living entirely under ``tests/parity/orgs/``
  and reading ``apps/setup-center/src/`` read-only.

**Sentinels after G-RC-9.8 close: 8 / 8 ACTIVE** (6 parity
+ 1 REST contract + 1 frontend stale-path).

## 8. 308 shim retirement timing

* **During P9.8 alpha / beta / gamma / delta**: shim
  REMAINS ACTIVE. Source:
  ``api/routes/_orgs_v2_legacy_redirects.py`` (101 LOC;
  9 ``add_api_route(<old_path>, 308 ->
  /api/v2/orgs-spec/...)`` entries). Canary test exercises
  it (P9.7.nit-a) for the v2.0.x window.
* **After P9.8 close + bake-in**: P9.9 candidate. Two
  operator decisions for P9.9 charter:
  * (a) **Retire alongside v1 deletion** in P9.9. The 9
    shim routes go with ``git rm api/routes/orgs.py`` in
    the same commit (any legacy callers are equally dead
    by then).
  * (b) **Defer to v2.1.0**. Keeps the 308 redirect through
    v2.0.x per ADR-0012 Q-B ACCEPTED (b)'s 1-release-window
    discipline; P9.9 only deletes v1 source.
* **Recommendation**: **(b) -- defer to v2.1.0**. Aligns
  with the 1-release-window contract; otherwise the "one
  shim window" rule silently becomes "two shim windows"
  without explicit operator sign-off. Final decision logged
  at P9.9 charter authorship.

## 9. Gate criteria for G-RC-9.8

1. Every section-4 caller site is on a v2 path:
   ``apps/setup-center/src/`` ``/api/orgs/`` literal count
   = **0** (assertion enforced by 8th sentinel if adopted).
2. ``apps/setup-center/src/api/orgs.ts`` +
   ``api/v2Stream.ts`` use ``/api/v2/orgs-spec/...`` for
   Group A endpoints; ``-spec`` literal count >= **10**.
3. v1 endpoint file UNTOUCHED through all P9.8 commits:
   ``git diff 4b8a9ad8..HEAD --
   src/openakita/api/routes/orgs.py`` returns empty bytes.
4. v1 subsystem UNTOUCHED: ``git diff 4b8a9ad8..HEAD --
   src/openakita/orgs/`` returns empty bytes.
5. ``src/openakita/channels/`` UNTOUCHED (R3 + section 5):
   ``git diff 4b8a9ad8..HEAD -- src/openakita/channels/``
   returns empty bytes.
6. All 7 (or 8) sentinels remain ACTIVE; 7 parity slots
   stay at 0 xfail.
7. ``tests/integration/test_v2_im_canary_e2e.py`` stays
   green (regression check from P9.7.nit-a).
8. Main gate stays >= G-RC-9.7 baseline (6 853 passed; 14
   carry-over failures unchanged).
9. ADR-0011 / 0012 / 0013 / 0014 invariants held;
   ADR-0015 status (section 11) confirmed unchanged.
10. ``docs/revamp/gates/G-RC-9.8.md`` mini-gate lands in
    delta-2; ledger close summary appended to
    ``docs/revamp/PROGRESS_LEDGER_P9.md`` in the same commit.

ACCEPTANCE.md is **not** modified by G-RC-9.8 (#5 rides to
G-RC-9 final after P9.9 closes).

## 10. P9.9 preview (what P9.8 does NOT do; recap)

P9.9 is the **physical deletion + Python import sweep**:

* ``git rm -r src/openakita/orgs/`` (~18 000 LOC, 6 charter
  subsystems gone).
* ``api/routes/orgs.py`` -> 410-Gone shim per Q-B ACCEPTED
  (b), or hard ``git rm`` if P9.9 charter selects Q-B (a).
* Optional retirement of ``_orgs_v2_legacy_redirects.py``
  (9 308 shims; ~101 LOC) per section 8.
* Mechanical rewrite of every ``from openakita.orgs.X``
  in ``src/openakita/`` (90 sites; top: ``orgs/tool_handler.py``
  27, ``api/routes/orgs.py`` 24, ``orgs/runtime.py`` 14,
  ``channels/gateway.py`` 5, ``api/server.py`` 4) +
  ``tests/`` (228 sites). v1-internal imports delete with
  their parent files; external-to-v1 imports rewrite to
  ``from openakita.runtime.orgs.X`` (~5 batches; pytest
  green after each).
* ``git rm -r tests/orgs/`` (46 test files; ~13 000 LOC)
  after P9.9a-audit confirms v2 coverage equivalence.
* **LOC delta**: ~-31 000 source LOC (largest deletion in
  the P-RC-9 arc).
* **Gate**: G-RC-9.9 = final P-RC-9 closure (incorporates
  G-RC-9.8 + deletion sign-off).

## 11. Reference matrix (NIT-E-1 discipline)

Re-scan of ``d:\claw-research\repos`` (autogen / cortex /
crewAI / langgraph / MetaGPT / sint-protocol) +
``d:\claw-research\briefs`` (01-cortex .. 06-autogen) for
caller-migration / API versioning patterns. **All 12 inputs
rejected** explicitly per NIT-E-1: langgraph + brief 03
(LangGraph Server REST post-dates v2.0.0 train); cortex +
brief 01 (OData migration model vs FastAPI); sint-protocol
+ brief 02 (JSON-RPC over gRPC vs HTTP + JSON); crewAI +
brief 05 (CLI-only); MetaGPT + brief 04 (Flask project-
scoped); autogen + brief 06 (WebSocket-only). **Net
adoption for P9.8: NONE.** Design inputs come from the
in-tree P-RC-3 ``orgs_v2*.py`` patterns (Group A as caller-
side template already in ``orgs.ts`` + ``v2Stream.ts``),
the P9.7 308 shim infrastructure, and the P9.7 REST
contract sentinel (mirrored by the 8th sentinel; not
architecturally novel). External corpus offers nothing
superior for a literal-string path swap. NIT-E-1 satisfied
with all 12 items explicitly rejected.

## 12. Frontend coordination notes (D-2 follow-up)

P-RC-9 P9.7 D-2 found the frontend layout split:

* **Group A (already-v2)**: ``api/orgs.ts`` (8 wrappers) +
  ``api/v2Stream.ts`` (SSE). Calls ``/api/v2/orgs/...``
  today; P9.8gamma-1 retargets to ``/api/v2/orgs-spec/...``.
* **v1-path scattered**: 60 hits in ``views/`` +
  ``components/``. No centralised URL builder -- each call
  is a ``${apiBaseUrl}/api/orgs/...`` template literal.
  P9.8gamma-2..4 swaps file by file.
* **No ``config.ts`` exists** (D-2 recon; reconfirmed at
  HEAD ``4b8a9ad8``). ``httpApiBase()`` in ``providers.ts``
  is the prefix source. P9.8 does NOT introduce a
  ``config.ts`` (out of scope -- would invent a refactor
  on top of the swap). The PLAN section 4 R10 build-
  artefact assertion (``BUILD_INFO.api_default``) was
  banked at G-RC-9.7 and rides to G-RC-9 final.

**TypeScript type alignment**: ``schemas/orgs_v2/*.py``
Pydantic shapes (16; P9.7a-2b + P9.7gamma-3a) have no TS
equivalent today; the 60 v1 callers receive data as
``Record<string, unknown>``. P9.8 keeps the loose-typing
posture (path-only swap; no type churn). OpenAPI-to-TS
codegen is a P-RC-10 hygiene candidate, not a P9.8
obligation.

## 13. Cross-references + HARD STOP

PLAN section 4 P9.8 (charter row this expands; redefined
to HTTP-callers-only here, Python import sweep deferred
to P9.9); ``gates/G-RC-9.7.md`` (prior mini-gate; 7/7
sentinels); ``P-RC-9-P9.7-DECISIONS.md`` D-1 R3 LOCKED +
D-2; ``Q_DECISIONS.md`` Q-B ACCEPTED (b) 1-release shim;
ADR-0011 (no new Protocol; 8th sentinel is a grep, not
an abstraction), ADR-0012 (v1 deletion -> P9.9; 308 shim
per section 8), ADR-0013 (perf_counter SLA not exercised
by P9.8), ADR-0014 (LOC budget ~750 vs P9.7's 2 845;
inside tolerance).

**ADR-0015 status: NOT FILED this round.** P9.8 introduces
no architectural change; the 8th sentinel is another
instance of the established grep-sentinel pattern; caller
migration governance is mechanical literal-string swap.
Sections 7 + 11 satisfy NIT-E-1 + ADR-0011/0012 invariants
without a new ADR. P9.9 charter reassesses ADR-0015
eligibility if the import-sweep introduces a genuinely
new decision.

**HARD STOP**: planning round only. P9.8alpha-1 (caller
inventory doc) is NOT started in this commit. Next agent
run, on operator signal, opens P9.8alpha-1. Until then,
repository remains at HEAD ``4b8a9ad8`` plus this charter
+ ledger row; ``git diff 4b8a9ad8..HEAD -- src/openakita/
tests/ apps/`` continues to return empty bytes.

**P-RC-9 status after this commit**: 6 / 6 ADR-0011
subsystems complete + parity-validated (P9.1-P9.6); v2
REST mint complete (P9.7 + 7 / 7 sentinels ACTIVE); P9.8
charter LANDED; P9.8alpha-1 NOT started; P9.9 / P9.10
unscheduled.
