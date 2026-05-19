# P-RC-9 Execution Plan -- ``src/openakita/orgs/`` integral migration

> **Branch:** ``revamp/v3-orgs`` (forked from ``v2.0.0-rc2`` at
> ``594d5cb1``). Do NOT push; do NOT delete ``revamp/v2``; the
> ``v2.0.0-rc1`` / ``v2.0.0-rc2`` tags remain authoritative for
> any operator who needs the last stable v2 release while
> P-RC-9 is in flight.
>
> **Recon:** ``docs/revamp/P-RC-9-RECON.md`` (P9.0b + P9.0b2).
> Every number in this plan traces back to a section of the recon.
>
> **Layout:** this plan is grown across **three commits**
> (P9.0c -> sections 0..3, P9.0d -> sections 4..5, P9.0e ->
> sections 6..8) because the full document exceeds the 380-LOC
> commit_guard cap (N12, G-RC-5 audit clarification). Future
> readers should treat the three commits as one logical document.

## 0. North star + scope (what do we actually want)

### 0.1 The two-sentence summary

After P-RC-9 closes, ``src/openakita/orgs/`` no longer exists in
``revamp/v3-orgs``. Every behaviour it owned today is served by
``src/openakita/runtime/orgs/`` and a small set of preserved
leaf modules, every REST endpoint at ``/api/orgs/...`` has a
1:1 ``/api/v2/orgs/...`` peer (with the v1 surface either
deleted or shimmed for one release per the Q-B decision), and
ACCEPTANCE.md criteria 2 (wall-clock cancel) and 5 (UI default
port) are upgraded from Pass-with-caveat / Partial to Pass.

### 0.2 Explicit out of scope

* **No behaviour changes.** Every v2 subsystem must reproduce the
  v1 contract byte-for-byte where observable; deltas are limited
  to internal structure (dependency injection instead of
  back-references, factory-based singletons instead of
  ``OrgRuntime.get_*()`` accessors). The parity harness gates this.
* **No REST contract changes.** v2 endpoints are 1:1 with v1 --
  same path (with ``/v2`` prefix), same verb, same query/body
  schema, same response shape. Where the v1 endpoint accepts a
  free-form ``dict`` body, the v2 endpoint accepts the same.
  Schema tightening (Pydantic v2 model migrations, field
  deprecation, etc.) is deferred to a follow-on plan.
* **No feature additions.** P-RC-9 does not ship new endpoints,
  new schedule types, new tool handlers, new template kinds, or
  new IM verbs. Anything not in the v1 surface at ``v2.0.0-rc2``
  is out of scope and must be opened as a separate plan.
* **No frontend rewrites** beyond the default-port flip needed
  to close ACCEPTANCE.md #5 (UI ships its setup-center default to
  the v2 API). The full setup-center UI rewrite for v2 is a
  separate effort.
* **No migration of the 30+ existing v2 subsystems shipped in
  P-RC-0..P-RC-8.** The ``runtime/`` and ``agent/`` packages are
  considered done; P-RC-9 only adds the 6 charter subsystems
  alongside them.

### 0.3 What "done" looks like

* ``git ls-files src/openakita/orgs/ | wc -l`` -> **0** (after
  P9.9).
* ``git grep -nE 'from openakita\.orgs' -- src/openakita/`` ->
  **0** (after P9.8).
* ``git grep -nE 'from openakita\.orgs' -- tests/`` -> **0**
  except for any tests intentionally kept in ``tests/parity/orgs/``
  during a deprecation window (see Q-B).
* All 80 missing v2 REST endpoints land in P9.7 with
  ``test_orgs_v2_full.py`` coverage matching the v1 endpoint
  test counts (target: per-endpoint at least 1 happy-path case
  + 1 error-path case).
* ``scripts/revamp_loc_audit.py`` reports the 4 ``orgs/*``
  baseline rows at 0 LOC (after P9.9 the files do not exist;
  the audit script keeps the rows but reads 0 from disk and
  passes because ``current <= baseline`` trivially).
* ``pytest tests/runtime tests/agent tests/api tests/parity
  tests/unit/test_plugins`` -> baseline ``1123 + N`` (N = new
  v2 subsystem tests minus deleted ``tests/orgs/*`` tests; net
  delta projected positive because the 18 contract tests
  per-subsystem + the 6 parity suites add to roughly 200+
  cases while the deleted tests/orgs/ losses are roughly
  comparable -- exact number locked at G-RC-9.1 first sub-gate).
* ``v2.0.0-rc3`` tag cut locally with G-RC-9 sign-off.

## 1. Current truth (cite recon, not memory)

Every figure below is reproducible by the command in parentheses;
re-running on ``revamp/v3-orgs`` HEAD must yield identical output.

* **Branch state:** ``revamp/v3-orgs`` is at ``75aebde2`` after
  P9.0a/b/b2; before P-RC-9 work this branch is identical to
  ``revamp/v2`` HEAD ``594d5cb1`` (which is the ``v2.0.0-rc2``
  tag). ``git log --oneline revamp/v2..HEAD`` shows the P-RC-9
  commits only.
* **orgs/ package:** 26 files, 18 213 LOC
  (``Get-ChildItem src/openakita/orgs/ -File`` + sum). Top three
  giants: ``runtime.py`` (5 734 LOC, 31.5%), ``tool_handler.py``
  (3 183 LOC, 17.5%), ``templates.py`` (1 234 LOC, 6.8%) --
  combined 55.8% of the package.
* **v1 REST:** 89 endpoints, 2 145 LOC
  (``git grep -cE '^@router\.' -- src/openakita/api/routes/orgs.py``;
  ``wc -l`` on the same file). Verb split 39 POST / 36 GET / 7
  PUT / 7 DELETE.
* **v2 REST:** 9 endpoints split across ``orgs_v2.py`` (8) and
  ``orgs_v2_stream.py`` (1). Delta = 80 endpoints to add at P9.7.
* **Production callers:** 86 sites across 13 files; only 5 of
  the 13 are external to ``orgs/`` (api/routes/orgs.py,
  api/server.py, channels/gateway.py, api/routes/chat.py,
  core/_reasoning_engine_legacy.py). See recon ?1c table.
* **Test callers:** 216 sites across 48 ``tests/orgs/*.py``
  files + a handful of cross-cutting integration/unit tests.
* **Existing v2 ``runtime/orgs/``:** 3 files, 412 LOC
  (``__init__.py`` re-exports + ``store.py`` JsonOrgStore +
  ``sqlite_store.py``). Storage-only; zero of the 6 charter
  subsystems exists.
* **Baseline pytest:** 1123 passed / 1 skipped / 5 xfailed in
  ~10s; plus the 8-case v2 IM integration trio. LOC audit exits 0.

## 2. Risks and mitigations

The top 10 risks, ranked by ``probability x blast radius``. Each
has an owner phase and a concrete mitigation that the gate
criteria for that phase must verify.

### R1 -- 4-6 week timeline drift (probability HIGH, blast LARGE)

The charter projects 4-6 weeks of work across 30-50 commits.
Real history (P-RC-4 through P-RC-7) shows the rewrite cadence is
~5-10 commits per day under one engineer, and that estimate
holds *only* when each phase has a tight LOC budget and a hard
mini-gate. Drift comes from scope creep ("while I am in this
file I will also fix X") and from skipping the mini-gate
("just one more commit before I write the gate doc").

**Mitigation (P9.0 + every phase):** every phase has its own
``G-RC-9.x.md`` mini-gate that must be written before the next
phase opens. The gate doc takes ~30 minutes; it forces the
executor to re-read the plan section, re-count tests, and
re-state the next phase's entry conditions. The continuation
plan (P-RC-0..P-RC-8) used this and finished in roughly the
projected calendar; we copy the pattern verbatim.

### R2 -- caller deep-dependency on v1 internal types (HIGH, LARGE)

86 production callers and 216 test callers import from the legacy
``openakita.orgs`` package. The 32 ``.models`` imports and the
26 ``.project_store`` imports are the riskiest because callers
often build instances inline (``OrgProject(...)``,
``NodeSchedule(...)``) rather than going through a factory.
Renaming the module path breaks those instantiations even when
the data is identical.

**Mitigation (P9.1-P9.6 + P9.8):** v2 subsystems re-export the
v1 type names verbatim. Where the v1 dataclass has fields the
v2 implementation does not need, the v2 type still defines them
(with sensible defaults) so caller construction sites do not
have to change. The parity harness asserts ``OrgProject(...).
to_dict() == OrgProjectV2(...).to_dict()`` for the same inputs.
At P9.8 the import path rewrite is mechanical: one bulk sed pass
from ``openakita.orgs.X`` to ``openakita.runtime.orgs.X`` with a
test-suite green check after each batch.

### R3 -- cancel + checkpoint regression (MEDIUM, LARGE)

ACCEPTANCE.md #2 (Pass-with-caveat from P8.7-doc-fix) says the
IM-cancel-to-checkpoint pipeline finishes within 2 s but the
2 s figure is documentary (asyncio fixture default), not
measured. P-RC-9 reshapes the cancel path (cancel verb moves
from legacy ``channels/gateway.py`` -> v2 OrgCommandService).
A regression that pushes the pipeline above 2 s will silently
ship until a user notices.

**Mitigation (P9.4 + ADR-0013):** P9.4 ships a wall-clock budget
test that uses ``time.perf_counter()`` around the IM-cancel ->
checkpoint pipeline and asserts ``< 2.0 s`` on a CI-baseline
machine. The test is added to the main ``tests/runtime/`` set so
every commit on ``revamp/v3-orgs`` runs it. ADR-0013 records the
SLA contract.

### R4 -- SQLite data loss during migration (LOW, FATAL)

P9.1-P9.6 do not migrate operator data per se (the v2 stores
already exist as JsonOrgStore + SqliteOrgStore from P-RC-3), but
P9.5 (OrgManager) and P9.6 (OrgRuntime) touch the per-org dir
layout (``<data_dir>/orgs/<org_id>/...``) where the legacy
``manager.py`` keeps ``org.json`` + ``state.json`` + node
schedules. A botched layout migration can lose user state.

**Mitigation (P9.5 + P9.6):** ``scripts/backup_orgs_data.py``
runs before any layout change and archives the entire
``data/orgs/`` tree to ``data/orgs.legacy_p9/`` (mirror of the
pattern P-RC-7 used for ``data/orgs.legacy/``). The migration
script is **idempotent** and **dry-run-default** -- it prints
the file moves it would make and exits 0 unless ``--apply`` is
passed. ``docs/revamp/rollback.md`` is extended with a "P-RC-9
data restore" SOP.

### R5 -- circular imports between subsystems (MEDIUM, MEDIUM)

OrgManager <-> ProjectStore <-> OrgCommandService <-> OrgRuntime
all reference each other in v1 via direct imports + back-refs.
If we naively port that, the import graph cycles.

**Mitigation (P9.1-P9.6):** every subsystem exposes a small
``Protocol`` for its public surface (e.g.
``OrgManagerProtocol``, ``CommandDispatcherProtocol``).
Cross-subsystem references are typed via the Protocol, not the
concrete class, and instances are injected at construction time
(not imported). The DAG in recon ?1b is acyclic; honour it.

### R6 -- REST contract drift (MEDIUM, MEDIUM)

The 80 endpoints P9.7 must mint each have specific query-string
parsing, response-shape quirks, and error-code mapping rules.
"Same shape" can silently regress when the v2 implementation
uses a different dict ordering or omits a field that v1 always
sets to ``None``.

**Mitigation (P9.7):** every v2 endpoint ships with a contract
test that records the v1 response shape (golden JSON file under
``tests/api/golden/orgs_v1/<endpoint>.json``) and asserts the
v2 response matches. The golden files are captured before P9.7
work starts (a recon sub-step in P9.7). The 1:1 contract is then
machine-enforced.

### R7 -- ``tool_handler.py`` (3 183 LOC, 66 methods) folding into OrgRuntime (MEDIUM, MEDIUM)

The legacy tool_handler is a single class with 66 methods, each
implementing one of 33 ``org_*`` tools. Folding it into
OrgRuntime risks exploding OrgRuntime past the 1 200 LOC budget.

**Mitigation (P9.6):** ``tool_handler.py`` becomes its own
file ``runtime/orgs/tool_handler.py`` with the OrgToolHandler
class preserved verbatim (P-RC-4/5/6 pattern: copy then refactor
in-place). OrgRuntime gets a single ``handle_org_tool()``
delegate method that calls into the handler. The handler does not
count against OrgRuntime's LOC budget; it tracks against the
``orgs/tool_handler.py`` baseline (currently 3 474; once moved
to ``runtime/orgs/tool_handler.py`` the baseline transfers).

### R8 -- ``models.py`` field-level divergence (LOW, MEDIUM)

The 21 dataclasses in ``orgs/models.py`` are the canonical
shape for serialized data on disk. If P9.5 or P9.6 introduces
a v2 dataclass with one extra field, every existing
``data/orgs/<id>/projects.json`` becomes "stale" and
deserialization may fail.

**Mitigation (P9.5):** v2 types are **field-equivalent** to v1.
The migration is a rename, not a shape change. JSON round-trip
test cases are added at P9.5 (round-trip 100 sample blobs).

### R9 -- ``tests/orgs/`` deletion regret (LOW, LARGE)

48 test files, 216 import sites. If P9.9 mechanically deletes
them and a behavioural regression slips in (a test that proved
some invariant the v2 subsystem also relies on), nobody finds
out until a user hits it.

**Mitigation (P9.8 + P9.9):** before deletion, every legacy test
that exercises a behaviour the v2 surface still owns is
**migrated** (re-pointed) to the v2 module path. Only tests that
exercise legacy implementation detail (e.g. private
``_CachedAgent`` LRU eviction) are dropped. The migration audit
is the first sub-phase of P9.9 (``P9.9a-audit``).

### R10 -- frontend default-port flip breakage (LOW, MEDIUM)

ACCEPTANCE.md #5 Partial -> Pass requires the setup-center UI
to ship with the v2 default port. The Tauri / Vite build
artifact is what end users get; a typo in the env var or the
build config silently ships a broken default.

**Mitigation (P9.7):** P9.7 includes a build-artifact test that
loads ``dist-web/index.html``, parses the embedded
``BUILD_INFO`` JSON, and asserts the v2 port is present. The
test runs in CI on every commit, not just at release.

## 3. Drift-prevention discipline

This phase inherits every guardrail the continuation plan
P-RC-0..P-RC-8 used. The mechanics:

### 3.1 commit_guard (380 WARN / 400 REJECT)

``.venv/Scripts/python.exe scripts/revamp_commit_guard.py
--staged`` MUST run before every ``git commit --trailer "Co-authored-by: Cursor <cursoragent@cursor.com>"``. The script
counts hand-written LOC across the staged diff
(``git diff --cached --numstat``), skips auto-generated files
(``package-lock.json``, ``*.lock``, ``*.svg``,
``docs/revamp/*.json`` baselines), warns at 380, rejects at
400. Source of truth: ``WARN_THRESHOLD = 380``,
``REJECT_THRESHOLD = 400`` in the script (P-RC-2 T1 + P-RC-5
N12 clarification).

### 3.2 LOC audit gate

``.venv/Scripts/python.exe scripts/revamp_loc_audit.py`` MUST
exit 0 before every commit. The script reads
``docs/revamp/LOC_BASELINE.json`` and compares against current
LOC for the 15 tracked files. P-RC-9 extends the baseline:

* When a legacy ``orgs/*.py`` file is moved to
  ``runtime/orgs/*.py`` (P-RC-4..7 pattern: ``git mv X
  _X_legacy``), the legacy path baseline is preserved at its
  current LOC (so it can only shrink) and the new path gets a
  fresh baseline equal to the moved LOC.
* When a legacy file is deleted (P9.9), its baseline row is
  removed in the same commit.
* The four ``orgs/*`` rows currently tracked (``runtime.py``
  6355, ``tool_handler.py`` 3474, ``templates.py`` 1266,
  ``messenger.py`` 651) drop to 0 as each phase lands.

### 3.3 Sentinel / facade detection per v2 subsystem

``tests/parity/test_no_facade.py`` already scans
``agent/{core,reasoning,brain,tools,context}.py`` for the
"only re-export from openakita.core.X" anti-pattern. P-RC-9
extends the scan to ``runtime/orgs/{blackboard,project_store,
node_scheduler,command_service,manager,runtime}.py`` so each
new v2 subsystem is verified to have a real implementation,
not a thin shim around the legacy module.

### 3.4 Ledger row per commit (N3)

Every commit on ``revamp/v3-orgs`` MUST append a row to
``docs/revamp/PROGRESS_LEDGER_P9.md`` **in the same commit**.
``git add docs/revamp/PROGRESS_LEDGER_P9.md`` before
``git commit --trailer "Co-authored-by: Cursor <cursoragent@cursor.com>"``. No "the next commit will backfill the hash"
loophole.

### 3.5 Commit message format (N5 + continuation plan ?0.4)

* English conventional commit title, <= 72 chars.
* Blank line.
* Why paragraph (2-3 sentences explaining the motivation, NOT
  the what -- the diff shows the what).
* ADR refs + plan section refs.
* ``Files:`` footer listing each touched path with a
  one-phrase note.
* Delivered via Python tempfile (``Path("commit_msg.tmp").
  write_text(msg, encoding="utf-8")`` then ``git commit --trailer "Co-authored-by: Cursor <cursoragent@cursor.com>" -F
  commit_msg.tmp``). NEVER PowerShell ``Out-File -Encoding
  utf8`` (which prepends a UTF-8 BOM and corrupts the subject
  line; N5).
* No ``--amend`` after a commit has lived on the branch for
  > 1 hour (continuation plan ?0.4).

### 3.6 Pause every 5 commits

Every 5 commits, the executor MUST stop and re-read:

1. The latest section of this plan.
2. ``docs/revamp/PROGRESS_LEDGER_P9.md`` (all rows so far).
3. ``docs/revamp/P-RC-9-RECON.md`` for the relevant subsystem.
4. The active ADR (0011 / 0012 / 0013).

Then write a 1-line ``# still-aligned check at commit N`` note
to the ledger. P-RC-5 found this was the single most effective
drift-prevention tool.

## 4. Phase decomposition (P9.0..P9.10)

Each phase has: id + title, estimated commits, LOC budget,
deliverables (precise paths + function signatures where pinnable),
gate criteria (specific test names + count targets), rework-risk
score. Every phase ends with its own ``G-RC-9.x.md`` mini-gate
that must be written before the next phase opens.

### P9.0 -- Baseline (THIS RUN)

* Commits: 10 (P9.0a/b/b2/c/d/e/f/g/h/i/z, ~110 LOC each).
* Budget: branch + ledger + recon + plan + 3 ADRs + parity
  skeleton + mini-gate. NO orgs/ source touched.
* Deliverables: ``PROGRESS_LEDGER_P9.md``, ``P-RC-9-RECON.md``,
  ``P-RC-9-PLAN.md``, ``ADR-0011/0012/0013``,
  ``tests/parity/orgs/`` skeleton, ``G-RC-9.0.md``.
* Gate criteria: pytest baseline unchanged at 1123/1/5 (plus N
  new xfail placeholders from the parity skeleton); LOC audit
  exit 0; ruff clean over ``tests/parity/orgs``.
* Rework risk: LOW. Documents only; can be edited in P9.1
  without invalidating any code.

### P9.1 -- OrgBlackboard

* Commits: 4-5. ``feat(runtime/orgs): scaffold OrgBlackboard
  module`` -> ``feat: implement 8 public methods on
  OrgBlackboard`` -> ``test(runtime/orgs): contract suite (12
  cases)`` -> ``test(parity/orgs): activate blackboard parity (8
  fixtures)`` -> ``docs: G-RC-9.1 mini-gate``.
* LOC budget: 350 in ``src/openakita/runtime/orgs/blackboard.py``
  + 200 in tests.
* Deliverables: ``runtime/orgs/blackboard.py`` exporting
  ``OrgBlackboard`` (constructor: ``(org_dir: Path, org_id: str)``;
  methods ``read_org``, ``read_department``, ``read_node``,
  ``write_org``, ``write_department``, ``write_node``,
  ``get_org_summary``, ``get_dept_summary``,
  ``get_node_summary``, ``query``, ``delete_entry``,
  ``clear``). ``tests/runtime/orgs/test_blackboard.py``
  (12 cases). ``tests/parity/orgs/test_blackboard_parity.py``
  (8 fixtures activated, was xfail in P9.0i).
* Gate criteria: tests/runtime grows by +12; tests/parity by +8
  (-8 xfail); ``tests/orgs/test_blackboard.py`` still green
  (legacy path untouched).
* Rework risk: LOW. No back-references; the only risk is
  storage-shape divergence which the parity suite catches.

### P9.2 -- ProjectStore

* Commits: 5-6. Same shape as P9.1 + the parent-child task tree
  invariants (``get_task_tree``, ``recalc_progress``,
  ``get_ancestors``).
* LOC budget: 300 + 250 tests.
* Deliverables: ``runtime/orgs/project_store.py`` (21 methods +
  factory ``get_project_store(org_dir) -> ProjectStore`` +
  ``reset_project_stores()``). 18 contract test cases (mirror
  ``tests/runtime/orgs/test_store_contract.py`` pattern).
* Gate criteria: tests/runtime +18, tests/parity +6 (-6 xfail);
  the 12 v1 REST project endpoints still green; storage
  round-trip test against 50 sample blobs.
* Rework risk: LOW-MEDIUM. The mtime-watch reload requires
  filesystem-level testing; CI must run on a real filesystem,
  not tmpfs.

### P9.3 -- NodeScheduler

* Commits: 4-5.
* LOC budget: 250 + 200 tests.
* Deliverables: ``runtime/orgs/node_scheduler.py`` (10 methods
  including ``start_for_org``, ``stop_for_org``, ``stop_all``,
  ``reload_node_schedules``, ``trigger_once``;
  ``CommandDispatcher`` Protocol injected at construction).
  ``tests/runtime/orgs/test_node_scheduler.py`` (10 cases
  covering CRON, INTERVAL, ONCE schedule kinds + cancel
  semantics + reload).
* Gate criteria: tests/runtime +10, tests/parity +4 (-4 xfail);
  5 v1 REST schedule endpoints still green.
* Rework risk: MEDIUM. Croniter expressions and the
  next-fire-time math must round-trip identically.

### P9.4 -- OrgCommandService

* Commits: 6-8.
* LOC budget: 700 + 400 tests + 100 wall-clock budget tests
  (closes ACCEPTANCE.md #2 caveat).
* Deliverables: ``runtime/orgs/command_service.py`` (12 public
  methods + dataclasses ``OrgCommandRequest``, ``ForwardTarget``,
  ``OrgOutputScope``; module singleton ``get_command_service()``).
  ``runtime/orgs/command_tracker.py`` (folded UserCommandTracker).
  ``runtime/orgs/event_router.py`` (folded).
  ``tests/runtime/orgs/test_command_service.py`` (20 cases).
  ``tests/runtime/orgs/test_cancel_wall_clock_budget.py``
  (asserts < 2 s on IM-cancel -> checkpoint pipeline; closes
  ADR-0013 SLA).
* Gate criteria: tests/runtime +20 + wall-clock test green;
  tests/parity +10 (-10 xfail); 3 v1 REST command endpoints
  still green; ``test_v2_im_cancel.py`` gains a perf_counter
  assertion (no fixture change).
* Rework risk: MEDIUM-HIGH. Verb dispatch + IM gateway
  integration touch the most caller code; the 5 import sites in
  ``channels/gateway.py`` must keep working through the v2
  module path during P9.4.

### P9.5 -- OrgManager

* Commits: 6-8.
* LOC budget: 600 + 400 tests.
* Deliverables: ``runtime/orgs/manager.py`` (12 public methods +
  ``OrgNameConflictError``; constructor
  ``(data_dir: Path)``; factory ``get_org_manager() -> OrgManager``).
  ``runtime/orgs/identity.py`` (preserved). 
  ``runtime/orgs/plugin_workbench_templates.py`` (preserved).
  ``tests/runtime/orgs/test_manager.py`` (24 cases) +
  ``test_identity.py`` (8 cases) + ``test_plugin_workbench.py`` (4).
* Gate criteria: tests/runtime +36, tests/parity +12 (-12 xfail);
  the ~25 v1 REST manager endpoints still green; JSON round-trip
  test against 100 sample blobs (closes R8).
* Rework risk: MEDIUM. Dir-layout mismatches between legacy and
  v2 will surface as test failures; the migration script must
  be dry-run-default + idempotent (R4 mitigation).

### P9.6 -- OrgRuntime (the big one)

* Commits: 10-15.
* LOC budget: 1 200 + 600 tests + the folded subsystems
  (``tool_handler.py`` 3 183, ``messenger.py`` 552,
  ``event_store.py`` 361, ``heartbeat.py`` 394, ``inbox.py``
  265, ``notifier.py`` 164, ``scaler.py`` 351, ``reporter.py``
  189, ``failure_diagnoser.py`` 462, ``plugin_assets.py`` 137 =
  6 058 LOC moved + slimmed).
* Deliverables: ``runtime/orgs/runtime.py`` (30 public methods +
  thin delegate to handler/messenger/scheduler).
  ``runtime/orgs/tool_handler.py`` (66 methods preserved).
  9 other folded modules under ``runtime/orgs/``.
  ``tests/runtime/orgs/test_runtime.py`` (40 cases).
  ``tests/runtime/orgs/test_tool_handler.py`` (preserved from
  legacy ``tests/orgs/test_tool_handler.py``, re-pointed).
* Gate criteria: tests/runtime +40 minimum; tests/parity +20
  (-20 xfail); the ~30 v1 REST runtime endpoints still green
  through legacy path (P9.7 flips them to v2); the cancel
  wall-clock test from P9.4 still under 2 s; LOC audit
  baselines for the 10 absorbed legacy files drop to 0 (since
  the rewritten v2 paths get fresh budgets).
* Rework risk: HIGH. This is the phase most likely to slip
  calendar. Mitigation: every sub-commit lands one absorbed
  legacy module at a time and runs the full pytest suite.

### P9.7 -- REST v2 full (the 80 missing endpoints) + UI port flip

* Commits: 8-12.
* LOC budget: ~1 800 (the 80 endpoints + dependent helpers).
* Deliverables: ``api/routes/orgs_v2_full.py`` (or split across
  several route files keyed to the 12 functional groups from
  recon ?1d). Each endpoint has a v1 golden JSON capture under
  ``tests/api/golden/orgs_v1/<endpoint>.json``. Frontend
  ``apps/setup-center/src/config.ts`` default-port flip to v2.
  Build artifact test ``tests/integration/test_frontend_v2_default.py``.
* Gate criteria: tests/api +80 (one per endpoint, contract
  test against golden JSON); ``BUILD_INFO.api_default ==
  '/api/v2'`` in dist-web; ACCEPTANCE.md criterion 5 flipped
  Partial -> Pass.
* Rework risk: MEDIUM. Golden-file contract tests catch the
  shape regressions but won't catch behavioural divergence
  (e.g. v2 endpoint runs a different code path that happens to
  produce the same response for the recorded inputs but
  diverges for unrecorded ones). Parity suite must cover the
  behaviour delta.

### P9.8 -- Caller migration (86 src + 216 tests)

* Commits: 8-12. One commit per logical batch, never more than
  20 import sites at once.
* LOC budget: ~400 per batch (each import rewrite + the test
  green check). Migration is mechanical sed-style.
* Deliverables: every ``from openakita.orgs.X`` in src/ and
  tests/ becomes ``from openakita.runtime.orgs.X``. Order:
  event_router (1) -> plugin_workbench_templates (1) ->
  runtime (2) -> blackboard (2) -> tool_categories (3) ->
  manager (5) -> command_service (8) -> project_store (26) ->
  models (32). Test file migrations track src migration order.
* Gate criteria: after each batch, full pytest still green;
  ``git grep -nE 'from openakita\.orgs' -- src/openakita/``
  monotonically decreasing.
* Rework risk: LOW. Mechanical; test suite catches any miss.

### P9.9 -- Legacy delete (the ``git rm`` phase)

* Commits: 3-4. ``refactor: git rm -r src/openakita/orgs/``;
  ``refactor: git rm src/openakita/api/routes/orgs.py`` (or
  shim per Q-B); ``refactor: git rm -r tests/orgs/`` (with a
  per-file audit listing each deletion);
  ``docs: drop orgs/* baselines from LOC audit``.
* LOC budget: large negative (deletions don't count against
  commit_guard; the LOC audit script ignores deletions).
* Deliverables: clean tree. ``git ls-files src/openakita/orgs/``
  -> empty. ``git ls-files tests/orgs/`` -> empty.
  ``LOC_BASELINE.json`` ``orgs/*`` rows removed.
* Gate criteria: full pytest still green; no import errors;
  the 80 v2 REST endpoints still respond (or the v1 shims
  still respond, per Q-B).
* Rework risk: HIGH if any caller was missed in P9.8. The
  rework cost is "add the migration back, re-run pytest"; not
  fatal but expensive in time.

### P9.10 -- G-RC-9 final gate + v2.0.0-rc3 + ACCEPTANCE upgrades

* Commits: 4-5. ``docs(revamp): write G-RC-9.md final gate``;
  ``docs(revamp): upgrade ACCEPTANCE #2 to Pass``;
  ``docs(revamp): upgrade ACCEPTANCE #5 to Pass``;
  ``docs(revamp): write RELEASE_v2.md v2.0.0-rc3 section``;
  ``chore(release): tag v2.0.0-rc3`` (annotated).
* LOC budget: ~300 across all commits.
* Deliverables: ``docs/revamp/gates/G-RC-9.md`` final review;
  ACCEPTANCE.md updated; RELEASE_v2.md updated;
  ``v2.0.0-rc3`` annotated tag (local; not pushed).
* Gate criteria: full pytest green; every mini-gate G-RC-9.x
  reviewed; ledger complete; user-signoff on G-RC-9 before
  tag is cut.
* Rework risk: LOW. Documents only.

### Per-phase mini-gate template

Each ``G-RC-9.x.md`` mini-gate is ~100 LOC and contains:

1. Status banner (written / signed).
2. Commit list with hashes.
3. Test count delta (before vs after).
4. LOC audit table excerpt.
5. Audit nits to carry forward (if any).
6. Entry conditions for the next phase.

The full ``G-RC-9.md`` at P9.10 is the canonical 300+ LOC
review that signs off the whole phase, in the format
``G-RC-0.md .. G-RC-8.md`` established.

## 5. Parity harness design

The harness is the safety net that proves "v2 behaves like v1".
P9.0i ships the skeleton (placeholders); each subsequent phase
activates the fixtures for its subsystem.

### 5.1 Layout

```
tests/parity/orgs/
  __init__.py              -- package marker
  conftest.py              -- shared fixtures (tmp_org_dir, sample blobs)
  README.md                -- parity contract per subsystem
  test_blackboard_parity.py    -- 8 fixtures (P9.1)
  test_project_store_parity.py -- 6 fixtures (P9.2)
  test_node_scheduler_parity.py -- 4 fixtures (P9.3)
  test_command_service_parity.py -- 10 fixtures (P9.4)
  test_manager_parity.py       -- 12 fixtures (P9.5)
  test_runtime_parity.py       -- 20 fixtures (P9.6)
```

Each test file follows the ``tests/parity/runners.py`` /
``tests/parity/harness.py`` pattern from P-RC-0..P-RC-7: a
fixture is a ``ParityCase`` (id + kind + inputs + ignore set),
the runner pair (``_blackboard_v1``, ``_blackboard_v2``)
produces a ``ParityResult``, and ``assert_parity`` asserts
equality modulo the ignore set.

### 5.2 Subsystem-specific contract notes

* **OrgBlackboard:** assert read/write round-trip against the
  same backing dir (use ``tmp_path`` to ensure isolation);
  ignore ``created_at`` (deterministic-via-freezegun in v2).
* **ProjectStore:** assert task-tree integrity after a
  multi-task insert + recalc_progress; ignore in-memory IDs
  (ULID prefix differs across runs; assert structural
  equality).
* **NodeScheduler:** assert next-fire-time computed by both
  paths is within 1 ms of each other (croniter is shared so
  this should be exact, but the assertion is the safety net).
* **OrgCommandService:** assert verb dispatch produces the
  same ``OrgCommandRequest.to_dict()`` on both paths; assert
  the wall-clock budget test in P9.4 gate criteria.
* **OrgManager:** assert ``create() -> dict ->
  Organization.to_dict()`` round-trip; assert dir layout is
  identical for ``data/orgs/<id>/``.
* **OrgRuntime:** the hardest. Use recorded fixtures from
  ``tests/orgs/test_runtime.py`` + ``test_org_orchestration_fix.py``
  + ``test_runtime_deadlock_watchdog.py``; assert state graph
  + checkpoint sequence equality.

### 5.3 Wall-clock budget tests (closes ACCEPTANCE.md #2 caveat)

P9.4 adds ``tests/runtime/test_cancel_wall_clock_budget.py``
with three cases:

1. ``test_im_cancel_to_checkpoint_under_2s``: simulated IM
   cancel verb on a running supervisor; assert
   ``perf_counter()`` delta < 2.0 s.
2. ``test_resume_after_cancel_under_3s``: after cancel + new
   IM message, assert resume picks up from checkpoint within
   3 s.
3. ``test_cancel_under_high_message_burst``: 10 concurrent
   commands in-flight, cancel one; assert that one cancels
   within 2 s and the other 9 remain unaffected.

ADR-0013 codifies these SLAs as the v2 cancel contract.

### 5.4 Cross-subsystem integration tests

P9.6 ships ``tests/runtime/orgs/test_integration_happy_path.py``
that wires OrgManager + OrgRuntime + OrgBlackboard +
OrgCommandService and runs a 3-node org through a simulated
IM command (no real LLM; ``MockBrain`` from existing
``tests/runtime/test_supervisor.py``). 5 cases covering
start/dispatch/cancel/resume/stop.

### 5.5 Migration smoke

P9.9 ships ``tests/integration/test_orgs_migration_smoke.py``:

1. Start with a JsonOrgStore at ``data/orgs_v2.json`` populated
   from a captured legacy state.
2. Run the migration script ``scripts/migrate_orgs_p9.py``
   (P9.5 deliverable).
3. Assert the 18 contract tests
   (``tests/runtime/orgs/test_store_contract.py``) still pass
   against the migrated SQLite backend.
4. Assert the 80 P9.7 v2 REST endpoints all respond with the
   expected golden JSON shape.

## 6. ADR additions

Three ADRs land in P9.0f/g/h with ``Status: Proposed``. They flip
to ``Status: Accepted`` at G-RC-9 (P9.10) after the
implementation has shipped, matching the P-RC-0..P-RC-8 pattern
where ADR-0001..0010 stayed Proposed until G-RC-8 signed off on
real implementations.

### ADR-0011 -- org subsystem decomposition (Why 6 subsystems instead of monolith)

* **Decision:** split the v1 ``orgs/`` package into 6 named v2
  subsystems (OrgBlackboard / ProjectStore / NodeScheduler /
  OrgCommandService / OrgManager / OrgRuntime) plus a handful of
  preserved leaf modules (failure_diagnoser, plugin_assets,
  policies, tool_categories, tool_definitions, identity).
* **Alternative considered:** keep ``OrgRuntime`` as one class
  with composition (the v1 shape). Rejected because the legacy
  144-method class is the structural reason the file is 5 734
  LOC, and the dependency back-references that compose it
  (heartbeat / inbox / notifier / scaler / scheduler / reporter
  all hold runtime references) make every subsystem untestable
  in isolation.
* **Trade-off accepted:** 6 small subsystems vs 1 big one means
  the dependency DAG must be honoured at construction time
  (injection, not back-reference). This is more upfront wiring
  but exactly what P-RC-2/3 did for ``runtime.supervisor`` and
  the wins (Protocol-typed cross-subsystem boundaries, fully
  isolated unit tests, easier reasoning) are proven.

### ADR-0012 -- ``orgs/`` deletion strategy (rename-shim-delete vs direct delete)

* **Decision:** **direct delete** for source files at P9.9 (no
  ``git mv X _X_legacy`` interim step like P-RC-4..6 used for
  core/). Justification: the v2 ``runtime/orgs/`` package gets
  the new code under a different path, so there is no shim
  question for the source itself. The v1 REST endpoints at
  ``/api/orgs/...`` get a configurable deprecation strategy
  per Q-B (default = keep v1 endpoints as 410-Gone-with-helpful-
  message shims for v2.0.x; hard-delete in v2.1.0).
* **Alternative considered:** rename ``orgs/X.py`` to
  ``orgs/_X_legacy.py`` and keep them in tree for a release.
  Rejected because nothing imports the v1 paths after P9.8 is
  complete (mechanical migration ensures this); keeping them
  in-tree only adds maintenance cost.
* **Consequence:** the P9.9 deletion is a single ``git rm -r
  src/openakita/orgs/`` + ``git rm -r tests/orgs/`` operation,
  not a multi-commit rename-then-delete pattern. The audit
  trail is the deletion commit message which lists every file
  removed.

### ADR-0013 -- wall-clock SLA tests for cancel/checkpoint

* **Decision:** the v2 IM-cancel pipeline ships with a
  ``perf_counter()`` wall-clock budget assertion that pins the
  contract: from IM-cancel-verb receipt to a written cancelled
  checkpoint, < 2.0 s; from a follow-up IM message to resumed
  supervisor, < 3.0 s. These tests live in
  ``tests/runtime/test_cancel_wall_clock_budget.py`` and run in
  every CI pass.
* **Alternative considered:** keep the structural assertion from
  ACCEPTANCE.md #2 (Pass-with-caveat) and rely on the asyncio
  fixture default to keep the wall clock small. Rejected because
  the asyncio default is implementation-dependent and silently
  changes when fixtures get reused across tests.
* **Closes:** ACCEPTANCE.md #2 caveat (P-RC-8 P8.7-doc-fix
  recorded the deferral).
* **Implementation phase:** P9.4 (OrgCommandService) lands the
  3 wall-clock tests; P9.6 (OrgRuntime) verifies they still
  pass after the runtime shell rewrite.

## 7. User decision points

Three open decisions need an answer before P9.1 work starts.
The defaults below are the recommendation; the user may
override any of them at G-RC-9.0 review.

### Q-A -- Should ``runtime/orgs/`` keep the ``runtime`` prefix, or rename to ``orgs_v2`` after migration?

* **Options:**
  * (a) Keep as ``src/openakita/runtime/orgs/`` indefinitely
    (the path the existing 3 storage-only files already use).
  * (b) Rename to ``src/openakita/orgs_v2/`` after P9.9 so the
    v2 surface has a clean top-level package.
  * (c) Rename to ``src/openakita/orgs/`` after P9.9 (reclaim
    the v1 path now that v1 is gone).
* **Default:** (a). Justification: every other v2 surface
  (``runtime/supervisor``, ``runtime/templates``,
  ``runtime/state_graph``, ``runtime/nodes``) lives under
  ``runtime/``; consistency wins. Path renames cost commits and
  caller churn for zero behavioural benefit. If a top-level
  ``orgs/`` path is wanted for ergonomics, do it as a separate
  plan after v2.0.0 ships.

### Q-B -- v1 REST endpoint deletion strategy

* **Options:**
  * (a) **Hard-delete** v1 endpoints at P9.9 alongside the
    source deletion. Any client still hitting ``/api/orgs/...``
    gets a 404.
  * (b) **Deprecation shim for 1 release:** v1 endpoints
    respond with HTTP 410 Gone + a body pointing at the v2
    equivalent (``"/api/v2/orgs/{...}"``). Shim is removed in
    the next minor release (v2.1.0).
  * (c) **Full passthrough:** v1 endpoints proxy to v2
    handlers (1:1 mapping inside the same FastAPI process).
    Slowest to remove but most caller-friendly.
* **Default:** (b). Justification: matches the P-RC-7 shim
  pattern for core/agent.py (gone in v2.0.0-rc1, fully
  deleted in v2.0.0-rc2 endgame). One release of 410-Gone
  responses gives operators time to migrate clients without
  carrying real passthrough code.

### Q-C -- Acceptable timeline

* **Options:**
  * (a) **2 weeks aggressive:** parallel multi-engineer
    sprint; mini-gates condensed; mandatory daily standup.
    Charter estimates this is doable only with 2-3 engineers.
  * (b) **4 weeks normal:** one engineer full-time, 5-10
    commits/day; mini-gates per phase as designed.
  * (c) **6 weeks conservative:** allows time for unanticipated
    REST contract edge cases and the OrgRuntime fold complexity
    to slip 1-2 weeks.
* **Default:** (b) 4 weeks normal. Justification: matches the
  charter's projection and the P-RC-4..P-RC-7 cadence (those
  four phases shipped in roughly 4 weeks of effort). The 4-week
  plan has slack at P9.6 (the OrgRuntime fold) and P9.7 (the
  80-endpoint REST mint); if those phases slip the calendar
  becomes 5-6 weeks naturally.

## 8. Acceptance upgrades after P-RC-9 closes

ACCEPTANCE.md (P-RC-8 P8.3 ``709767b3``) currently rates the
5 criteria as:

1. AIGC single-run no-duplicate -- **Pass**
2. IM cancel + checkpoint < 2 s -- **Pass-with-caveat**
3. Resume after cancel -- **Pass**
4. happyhorse-video single WorkbenchNode -- **Pass**
5. All built-in templates + 1-click new org -- **Partial**

After P-RC-9 closes, criteria 2 and 5 upgrade to Pass:

### Criterion 2: Pass-with-caveat -> Pass

* **Trigger:** P9.4 lands the three wall-clock budget tests
  per ADR-0013 (IM-cancel <2s, resume <3s, 10-concurrent
  burst). The pytest assertion is structural (perf_counter on
  the test machine, not documentary).
* **Evidence:** add a citation in ACCEPTANCE.md criterion 2 to
  ``tests/runtime/test_cancel_wall_clock_budget.py`` test ids
  + the P9.4 commit hash + ADR-0013.

### Criterion 5: Partial -> Pass

* **Trigger:** P9.7 lands the 80 missing v2 REST endpoints +
  the UI default-port flip to v2. The setup-center dist-web
  artifact loads and successfully creates an org from any
  built-in template via the v2 REST API.
* **Evidence:** add a citation in ACCEPTANCE.md criterion 5 to
  the P9.7 commit hashes + the build-artifact test
  (``test_frontend_v2_default``) + the per-endpoint contract
  tests under ``tests/api/golden/orgs_v1/``.

Both upgrades are documented in the same commit
(``docs(revamp): upgrade ACCEPTANCE #2 #5 to Pass``) at P9.10
as part of the G-RC-9 review.

### Out of scope: criteria 1, 3, 4

These remain Pass with the existing evidence chain; P-RC-9 does
not touch the supervisor decision table, the checkpoint resume
contract, or the WorkbenchNode plugin manifest path. If any of
the three regresses during P-RC-9, the phase's mini-gate
catches it.

---

End of plan. Next decision (operator): review this document,
answer Q-A / Q-B / Q-C, and approve P9.1 launch -- or request
revisions.
