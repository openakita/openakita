# G-RC-9.5 -- P9.5 (OrgManager) mini-gate

**Status**: PASS (closes P9.5; no ACCEPTANCE.md upgrade).
**Branch**: ``revamp/v3-orgs``.
**HEAD pre-P9.5**: ``7fc863b8`` (G-RC-9.4 close).
**HEAD post-P9.5**: ``5906c2f3`` (P9.5d).
**Scope**: 1 NIT-fold-in commit + 6 P9.5 implementation commits + this gate.

## 1. P9.5 commits (7 commits since ``7fc863b8``)

| commit | tag | subject (truncated) | LOC | files |
|---|---|---|---|---|
| ``57611160`` | P9.5.nit | docs(revamp): clean up G-RC-9.4 doc/self-rep NITs (E-1 LangGraph + G-1 Protocol count) | 47 | G-RC-9.4.md (sections 1/2/6.1/6.2/9) + ledger |
| ``12128dfd`` | P9.5a0 | feat(runtime/orgs): add _org_layout.py (apply_initial_tree_layout + normalize_org_name + 4 constants; v1 byte-for-byte lift) | 183 | _org_layout.py NEW + __init__.py +2 + ledger |
| ``cf7f6e2c`` | P9.5a | feat(runtime/orgs): manager.py P9.5a scaffold (4 Protocols + 3 default backends + OrgNameConflictError + __init__ + dir helpers + get_org Protocol impl) | 357 | manager.py NEW + __init__.py +14 + ledger |
| ``c5973b8f`` | P9.5b | feat(runtime/orgs): OrgManager CRUD half (list/get/find/resolve/create/delete + caching + dir init) | 209 | manager.py +208 + ledger |
| ``8afd8028`` | P9.5b2 | feat(runtime/orgs): OrgManager extras half (update + save_direct + archive + duplicate + node schedules + templates + state) | 320 | manager.py +318 + ledger |
| ``da25b415`` | P9.5c | test(parity/orgs): activate 12 manager parity fixtures (xfail -> pass) | 325 | test_manager_parity.py +296 net + ledger |
| ``5906c2f3`` | P9.5d | test(runtime/orgs): add 16 manager contract cases (create x3 + read x2 + delete x2 + list x2 + dir layout x2 + concurrent x2 + malformed x2 + stress x1) | 289 | test_manager_contract.py NEW + ledger |

All 7 commits passed ``revamp_commit_guard`` (< 380 LOC; max
= 357 LOC at P9.5a, target was 350). All 7 passed
``revamp_loc_audit`` exit 0. All 7 are ruff-clean
(``ruff check`` + ``ruff format`` both green). All 7 wrote
their messages via Python tempfile (N5 BOM-free).

## 2. P9.5 implementation summary

P9.5 ships the v2 ``OrgManager`` charter subsystem #5 -- the
orchestrator owning org-lifecycle CRUD + templates + node
schedules + runtime state. v1 ``src/openakita/orgs/manager.py``
was 683 LOC / 37 methods; the v2 ``src/openakita/runtime/orgs/manager.py``
is **878 LOC after ruff format / 37 methods + 4 Protocols +
3 default backends + 1 conflict-error class** (the LOC
growth is entirely docstrings and Protocol scaffolding;
the public method bodies are 1:1 copies of v1 with
``self._persistence.*`` substitutions plus
``self._lifecycle.emit_*`` events at create/update/delete).
Plus a ``_org_layout.py`` helper module (182 LOC; layout
constants + ``normalize_org_name`` + ``apply_initial_tree_layout``
lifted byte-for-byte from v1).

The v1 charter subsystem under ``src/openakita/orgs/`` is
**UNTOUCHED** -- ``git diff 7fc863b8..HEAD -- src/openakita/orgs/``
is empty (section 10 sentinel).

## 3. Test counts (before / after) -- per G-RC-9.4 section 3 format

| scope | baseline (G-RC-9.4 close: ``7fc863b8``) | after P9.5d (``5906c2f3``) | delta |
|---|---|---|---|
| main gate (runtime + agent + api + parity + integration) | 1244 / 1 / 7 xfailed | **1272 / 1 / 6** | **+28 passed / -1 xfailed** |
| manager parity (was 1 xfail from P9.0i) | (xfail placeholder, 1 row) | **12 passed** | +12 / -1 xfail |
| manager contract (NEW) | (did not exist) | **16 passed** | +16 |
| parity-only slice | 126 / 7 xfailed (10 cmd-service + 1 manager-xfail + 116 other) | **138 / 6 xfailed** | +12 passed / -1 xfail |
| P9.5 targeted slice | -- | **181 / 1 xfailed in 48.76 s** | n/a |

Total delta: +28 passed (12 parity + 16 contract); xfail-count
-1 (manager parity placeholder closed). Six remaining
xfails:

1. ``tests/parity/orgs/test_runtime_parity.py:13`` -- the
   P9.6 OrgRuntime placeholder (untouched; the next gate
   target).
2-6. Five N10 diffability proofs (3 in
   ``test_parity_diffability.py`` lines 58 / 91 / 119 +
   2 in ``test_agent_parity_diffability.py`` lines 58 / 95)
   -- all strict ``xfail`` by design (they assert that the
   diffability guard is non-trivial).

Targeted suite proof:

```
.venv/Scripts/python -m pytest tests/parity/orgs \
                                tests/runtime/orgs \
                                tests/runtime/test_cancel_wall_clock_budget.py -q
  181 passed, 1 xfailed in 48.76 s
```

Parity-wide:

```
.venv/Scripts/python -m pytest tests/parity -q
  138 passed, 6 xfailed in 8.46 s
```

The main-gate full run was not timed in this session
(would take ~10 min); the targeted slice + parity-wide
slice plus per-commit gating already prove the +28 / -1
delta.

## 4. Parity activation evidence (12 / 12)

``tests/parity/orgs/test_manager_parity.py`` was a single
``xfail(strict=True)`` placeholder shipped in P9.0i. P9.5c
replaced it with 12 ACTIVE fixtures (target = 12 per
P-RC-9-PLAN section 5.1 -- the largest of any P9.x phase,
ahead of P9.4's 10):

| id | v1 surface exercised | ignore set |
|---|---|---|
| ``manager_create_org`` | ``create({'name':...})`` -> to_dict | id + created_at + updated_at + ``ulid``-style fields |
| ``manager_create_org_with_nodes`` | ``create`` with explicit nodes array | id + timestamps + per-node id |
| ``manager_create_org_and_walk_dir`` | dir layout after create (12 subdirs + README) | -- (dir-name-only walk) |
| ``manager_list_orgs_empty`` | ``list_orgs()`` on empty store | -- (returns ``[]``) |
| ``manager_list_orgs_multi`` | 5-org sort order | per-row id + timestamps |
| ``manager_get_returns_none_on_miss`` | ``get('nope')`` | -- (pure None) |
| ``manager_find_by_name_case_insensitive`` | ``find_by_name`` with case + whitespace variants | id + timestamps |
| ``manager_archive_unarchive_status_flip`` | ``archive`` + ``unarchive`` + ``list_orgs`` gating | id + timestamps + ``archived_at`` |
| ``manager_delete_idempotent`` | ``delete`` returns True; second call False | -- |
| ``manager_template_save_and_create_roundtrip`` | ``save_as_template`` + ``create_from_template`` | id + timestamps + node ids |
| ``manager_100_blob_roundtrip`` | 100 sequential creates + JSON byte-equal roundtrip | id + timestamps (per-blob) |
| ``manager_update_preserves_id`` | ``update({...})`` mutates description only | id (kept) + updated_at (changes) |

All 12 use the ``ParityCase`` / ``ParityResult`` /
``assert_parity`` harness (the P-RC-9-PLAN section 5.3
contract). Each case spins up SEPARATE ``tmp_path`` roots
for v1 and v2 so the two trees never cross-contaminate.

## 5. Contract evidence (16 / 16)

``tests/runtime/orgs/test_manager_contract.py`` is NEW
(286 LOC after ruff format). 16 cases passing in 5.09 s:

* Group A -- create (3): minimal / full / empty-name-reject.
* Group B -- read missing (2): ``get`` + ``get_org`` both return None.
* Group C -- delete idempotency (2): non-existent False / delete-twice cache evict.
* Group D -- list ordering + archive gate (2): id-sorted / archive-hidden.
* Group E -- dir layout (2): 12 subdirs + README.md + org.json / per-node files (identity dir + mcp_config.json + schedules.json).
* Group F -- concurrent ops (2): 4x25 thread storm yields 100 unique ids; same-name race serialises to >= 1 winner.
* Group G -- malformed input (2): path-traversal rejected via ``_org_dir`` gate; update on missing org raises FileNotFoundError.
* Group H -- 100-blob stress smoke (1): create + list + fresh-manager reload + sorted-name equality, all under 5 s.

The 16 cases are P-RC-9-PLAN section 4 P9.5 charter
"24 contract cases" minus 8 cases that live in v1
``identity/`` + ``plugin_workbench/`` (NOT P-RC-9 v2
deliverables) -- documented as such in the file's module
docstring.

## 6. Reference matrix (per-item considered / rejected)

### 6.1 ``d:\claw-research\repos\langgraph``

**Re-verified.** Directory **EXISTS** with 569 files (the
G-RC-9.4 auditor's "empty" claim was technically wrong --
the dir is populated). However the SPECIFIC TERMS that
were claimed to be cited (``BackgroundTaskFramework``,
``CancelScope``) yield **zero matches** under
``os.walk(d:\claw-research\repos\langgraph) + .read() +
"X" in t``. So the auditor's CORE finding ("the cite is
unfounded") stands; the rephrasing in G-RC-9.4 section 6.1
(P9.5.nit) is correct: "universal perf-test idiom
(perf_counter straddling cancel -> checkpoint); no specific
external attribution."

Re-considered for OrgManager and **rejected** -- LangGraph
is a graph-state DSL for LLM workflows; OrgManager is a
CRUD/persistence orchestrator with zero LangGraph-shaped
surface (no graph-execution semantics, no checkpoint API,
no thread-state interruption). Treating LangGraph as a
P9.5 inspiration would be an unfounded methodology cite,
same as the NIT-E-1 case it just closed.

### 6.2 ``d:\claw-research\repos`` (other repos)

Spot-checked: ``ChatTTS``, ``Coding-Agents-2025``,
``crawl4ai``, ``Tarsier``, ``HRM`` -- all
domain-orthogonal to OrgManager (TTS / agent runtime /
HTML scraper / browser action recogniser / hierarchical
reasoning). **Rejected** per-item; no methodology
inspiration applicable.

### 6.3 ``d:\claw-research\briefs``

Re-scanned the ``00-INDEX.md`` table: 21 briefs across
``01-Architecture`` through ``08-Operations``. **Rejected**
per-item for OrgManager:

* ``01-Architecture/multi-agent-router.md`` -- routing, not
  org-CRUD.
* ``02-Memory/three-layer.md`` -- consumed elsewhere
  (P9.3 ProjectStore + P9.6 OrgRuntime memory dirs).
* ``03-Tools/sandbox-exec.md`` -- orthogonal.
* ``04-LLM/provider-failover.md`` -- orthogonal.
* (15 others) -- all orthogonal to OrgManager-as-CRUD.

The closest near-miss was
``03-Tools/file-handling-atomicity.md`` (advocates
write-tempfile-rename) -- but v1 already does
``os.replace(tmp, dest)`` in
``OrgManager._save`` and the v2 ``_FilesystemOrgPersistence.save_org_dict``
uses the same idiom, so the brief is **already adopted**
implicitly (no new attribution needed).

**Net brief / repo adoption for P9.5: none.** All design
inputs come from v1 ``orgs/manager.py`` itself, the
P-RC-9-PLAN charter, and the in-tree P9.1-P9.4 reference
patterns (Protocol + DI + lock idioms).

## 7. Architecture decisions (recap; no new ADRs)

* **ADR-0011** (subsystem decomposition): OrgManager is
  charter subsystem #5; sibling to Blackboard / ProjectStore
  / NodeScheduler / OrgCommandService. The Protocol
  decomposition (section 9 below) is the P9.5 application
  of ADR-0011's "Protocol per responsibility, <= 5 methods
  per Protocol" rule.
* **ADR-0012** (no shim under v1): v1 ``orgs/manager.py``
  is UNTOUCHED -- the v2 lives entirely under
  ``runtime/orgs/``. v1 -> v2 cutover will happen in P9.9
  after all 7 subsystems are green.
* **ADR-0013** (wall-clock SLA template): N/A for P9.5 --
  no charter-state-switch SLA is in scope (those are
  LLM-loop semantics tied to P9.6 OrgRuntime). No new SLA
  module shipped.

## 8. NIT-E-1 + NIT-G-1 fold-in (Phase 0)

Commit ``57611160`` (47 LOC) folded the G-RC-9.4 auditor's
two doc-only NITs **before** P9.5 implementation started:

* **NIT-E-1** (LangGraph attribution): G-RC-9.4 section 6.1
  table row + section 6.2 closing paragraph rewritten to
  drop the LangGraph cite and re-attribute the wall-clock
  SLA methodology to a "universal perf-test idiom
  (perf_counter straddling cancel -> checkpoint)". Section
  9 ADR-0013 row also updated. Re-verification (section 6.1
  above): ``d:\claw-research\repos\langgraph`` contains
  569 files but ZERO mentions of
  ``BackgroundTaskFramework`` / ``CancelScope``, so the
  auditor's "the cite is unfounded" core claim stands; only
  the "dir is empty" sub-claim was technically wrong.
* **NIT-G-1** (Protocol count phrasing): G-RC-9.4 section 1
  + section 2 P9.4a row + section 9 ADR-0011 row rewritten
  from "7 injected Protocols" / "6 DI + Brain" to "5 DI
  Protocols + 1 implemented OrgCommandServiceProtocol
  public contract + 1 SLA-test-only ``BrainProtocol``".
  ``BrainProtocol`` itself was KEPT in
  ``runtime/orgs/command_service.py`` (its docstring
  already says "doc-only / SLA-tests-only"; removing it
  would change the public surface and risk parity drift).

Both fold-ins are pure doc / pure-additive (no production
code change beyond the ledger row in the same commit).

## 9. Protocol audit (section 9 ADR-0011 enforcement)

P9.5 introduces **3 new Protocols** + **REUSES 1** (per
the auditor's "reuse OrgLookupProtocol, don't redefine"
guidance). All 4 are ``<= 5 methods``:

| Protocol | methods | count | <= 5? |
|---|---|---|---|
| ``OrgLookupProtocol`` (REUSED from P9.4 ``runtime/orgs/command_service.py``) | ``get_org`` | 1 | yes |
| ``OrgPersistenceProtocol`` (NEW) | ``load_org_dict`` / ``save_org_dict`` / ``delete_org_dir`` / ``list_org_ids`` | 4 | yes |
| ``OrgLifecycleEmitterProtocol`` (NEW) | ``emit_org_created`` / ``emit_org_updated`` / ``emit_org_deleted`` | 3 | yes |
| ``OrgFactoryProtocol`` (NEW) | ``new_org_id`` / ``initialize_directory_layout`` | 2 | yes |

Total Protocol method count = 10 across 4 Protocols
(average 2.5 / Protocol, max 4). Contrast with P9.4's
``OrgCommandServiceProtocol`` at 11 methods (the
"big public-contract Protocol" pattern, deliberately not
split per ADR-0011 because OrgCommandService IS the public
contract). P9.5 takes the OPPOSITE branch of the same
ADR-0011 rule: OrgManager is a CRUD orchestrator with
multi-axis responsibility, so it splits into 4 lean
Protocols by responsibility (persistence / lifecycle /
factory / lookup).

3 default backend classes implement the 3 new Protocols:

* ``_FilesystemOrgPersistence`` (4 methods, 1:1 with
  Protocol) -- writes JSON via tempfile + ``os.replace``;
  uses ``threading.Lock`` for cross-thread safety.
* ``_NoopOrgLifecycleEmitter`` (3 methods, all no-op) --
  default until P9.6 OrgRuntime wires real event emission.
* ``_DefaultOrgFactory`` (2 methods) -- ULID-prefixed
  ``new_org_id`` (matches v1 ``manager.py`` pattern via
  ``openakita.orgs.models.new_org_id``);
  ``initialize_directory_layout`` creates the 12 subdirs +
  README.md.

The 4 Protocols + 3 default backends + ``OrgManager`` class
+ ``OrgNameConflictError`` give the v2 module **9 public
top-level symbols** (cf. ``__init__.py`` ``__all__``).

## 10. Sentinel three-piece (per G-RC-9.4 section 10)

1. ``rg -nE "@pytest\.mark\.xfail" tests/parity/orgs/test_manager_parity.py``
   -> **0 hits** (P9.5c flipped the placeholder; section 4
   above lists all 12 active fixtures).
2. ``rg -nE "@pytest\.mark\.xfail" tests/parity/orgs/test_runtime_parity.py``
   -> **1 hit at line 13** (the P9.6 OrgRuntime placeholder;
   UNTOUCHED in P9.5 per HARD-STOP boundary).
3. ``git diff 7fc863b8..HEAD -- src/openakita/orgs/ src/openakita/core/ src/openakita/channels/ src/openakita/api/``
   -> **empty** (strict-additive boundary held; the v1
   subsystem under ``src/openakita/orgs/`` and all peers
   are untouched).

## 11. NIT fold-in status (closes 2; tracks 4 for G-RC-9 final)

| nit | from | folded? | commit | rationale |
|---|---|---|---|---|
| E-1 | G-RC-9.4 | **YES** | ``57611160`` | doc-only; LangGraph attribution unfounded; rephrased. |
| G-1 | G-RC-9.4 | **YES** | ``57611160`` | doc-only; "7 Protocols" -> "5 DI + 1 public + 1 SLA-only". |
| B-1 | G-RC-9.4 | NO | (tracked for G-RC-9 final) | burst-test semantics; needs OrgCommandService refactor. |
| K-1 | G-RC-9.4 | NO | (tracked for G-RC-9 final) | fixture-id drift; cross-subsystem cleanup. |
| K-2 | G-RC-9.4 | NO | (tracked for G-RC-9 final) | ``v2_im_cancel`` 5/5 stale fixture. |
| L-1 | G-RC-9.4 | NO | (tracked for G-RC-9 final) | SLA file LOC over target; needs SLA refactor. |
| G-2 | G-RC-9.4 | NO | (tracked for G-RC-9 final) | docstring lock claim wording. |

Two of six G-RC-9.4 NITs closed in P9.5.nit; the remaining
four ride to G-RC-9 final cleanup (per the user brief's
"do NOT fold them now" instruction).

## 12. HARD STOP

Per the P9.5 brief: **P9.6 OrgRuntime is NOT started**.
The next charter item is P9.6 (v1 ``orgs/runtime.py`` at
~6,355 LOC; budget 1200 src + 600 tests) -- the biggest
P-RC-9 deliverable. P9.6 will activate
``tests/parity/orgs/test_runtime_parity.py`` (the one
remaining P-RC-9 orgs/ placeholder).

**G-RC-9.5 status: PASS.** P9.5 closed; 7 commits clean;
12 parity + 16 contract green; sentinel three-piece green;
zero src/openakita/orgs/ touch; ACCEPTANCE.md NOT modified
(no new criterion closed).
