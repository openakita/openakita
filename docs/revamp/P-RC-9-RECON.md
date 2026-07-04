# P-RC-9 Recon -- ``src/openakita/orgs/`` integral migration

> **Status:** read-only analysis. NOT a plan. Plan lives in
> ``docs/revamp/P-RC-9-PLAN.md`` (P9.0c+).
>
> **Captured at:** branch ``revamp/v3-orgs`` HEAD (immediate
> descendant of ``v2.0.0-rc2`` tag at ``594d5cb1``). All numbers
> are reproducible from the commands quoted inline; re-running
> them on the same HEAD must yield identical figures.
>
> **Audience:** the future agents who execute P9.1..P9.10, the
> reviewer who signs G-RC-9 at the end, and any operator who wants
> to understand why the migration takes ~30-50 commits across
> ~4-6 weeks instead of one mechanical ``git rm``.
>
> **Layout:** this file is grown in two commits to stay within the
> 380-LOC commit_guard cap -- P9.0b ships sections 0/1a/1b (the
> file inventory + subsystem scope), and P9.0b2 appends sections
> 1c/1d/1e/1f and the appendices (caller catalog, REST surface,
> test surface, existing v2 surface, pytest baseline).

## 0. One-glance summary

* Legacy package size: **26 files, 18 213 LOC** (``wc -l
  src/openakita/orgs/*.py`` -> sum).
* v1 REST surface: **89 endpoints, 2 145 LOC** in
  ``src/openakita/api/routes/orgs.py``
  (``git grep -cE '^@router\.' -- src/openakita/api/routes/orgs.py``).
* v2 REST surface today: **9 endpoints** in ``orgs_v2.py`` (8) +
  ``orgs_v2_stream.py`` (1). ``80 endpoints / 89.9%`` of the v1
  surface has no v2 equivalent yet.
* Production caller sites (``git grep -nE 'from openakita\.orgs'
  -- src/openakita/`` | ``wc -l``): **86** across 13 unique files;
  of those 13, **6 are inside orgs/ itself** (intra-package) and
  **7 are external** (api, channels, core legacy).
* Test caller sites: **216** across **48 files under tests/orgs/**
  plus a handful in ``tests/integration/`` and ``tests/unit/``.
* v2 ``runtime/orgs/`` surface today: **3 files, 412 LOC** --
  storage-only (``JsonOrgStore``, ``SqliteOrgStore``, factory).
  Zero of the 6 charter subsystems is implemented.
* Baseline pytest on ``revamp/v3-orgs`` HEAD:
  ``1123 passed, 1 skipped, 5 xfailed`` (tests/runtime + tests/agent
  + tests/api + tests/parity + tests/unit/test_plugins). Plus the
  integration trio ``test_v2_im_canary_e2e + test_v2_im_cancel +
  test_entrypoints``: ``8 passed``. LOC audit: ``exit 0``.

These numbers anchor the gate criteria in ``P-RC-9-PLAN.md`` and
should be re-measured at every phase boundary.

## 1a. ``orgs/`` tree audit

Captured via::

    Get-ChildItem src/openakita/orgs/ -File
    git grep -nE '^(class|def|async def) '   -- src/openakita/orgs/
    git grep -cE '    def |    async def '  -- src/openakita/orgs/<file>

| file | LOC | role | owns | rewrite-target (subsystem / phase) |
|---|---:|---|---|---|
| ``models.py`` | 908 | dataclasses + enums | 21 types: ``Organization``, ``OrgNode``, ``OrgEdge``, ``OrgMessage``, ``OrgMemoryEntry``, ``NodeSchedule``, ``InboxMessage``, ``OrgProject``, ``ProjectTask``, ... | preserved as ``runtime/orgs/types.py`` (P9.5/P9.8) -- names kept |
| ``manager.py`` | 588 | business logic | ``OrgManager`` (36 methods) + ``OrgNameConflictError`` | **OrgManager** (P9.5) |
| ``runtime.py`` | 5 734 | runtime god-class | ``OrgRuntime``: 100 sync + 44 async = **144 methods**; ``_CachedAgent`` LRU; ``get_runtime()`` singleton | **OrgRuntime** (P9.6) -- biggest |
| ``command_service.py`` | 873 | command verb dispatcher | ``OrgCommandService`` (29 methods) + ``OrgCommandRequest`` / ``ForwardTarget`` / ``OrgOutputScope`` + module singleton | **OrgCommandService** (P9.4) |
| ``blackboard.py`` | 344 | three-tier memory | ``OrgBlackboard`` (19 methods); org/dept/node scopes | **OrgBlackboard** (P9.1) -- easiest |
| ``project_store.py`` | 247 | project persistence | ``ProjectStore`` (21 methods) over per-org ``projects.json`` | **ProjectStore** (P9.2) |
| ``node_scheduler.py`` | 180 | cron / interval / once | ``OrgNodeScheduler`` (3 sync + 7 async); back-ref into runtime | **NodeScheduler** (P9.3) |
| ``tool_handler.py`` | 3 183 | 33 ``org_*`` tool handlers | ``OrgToolHandler`` (66 methods) | folded into OrgRuntime delegation (P9.6) |
| ``messenger.py`` | 552 | inter-node msg queue | ``OrgMessenger`` + ``DeadlockDetector``; per-node async mailbox | folded into ``runtime.messenger.Messenger`` (P9.6) |
| ``event_store.py`` | 361 | append-only event JSONL | ``OrgEventStore``: per-day files + filelock | folded into ``runtime/orgs/event_store.py`` (P9.6) |
| ``event_router.py`` | 104 | scope-filter for external surfaces | helper functions | folded into OrgCommandService (P9.4) |
| ``failure_diagnoser.py`` | 462 | ReAct trace -> human summary | 10 pure functions | preserved as ``runtime/orgs/failure_diagnoser.py`` (P9.6) |
| ``heartbeat.py`` | 394 | standup / weekly report scheduler | ``OrgHeartbeat`` | folded into OrgRuntime (P9.6) |
| ``identity.py`` | 425 | 4-level identity inheritance + MCP overlay | ``OrgIdentity``, ``IdentityProfile`` | preserved as ``runtime/orgs/identity.py`` (P9.5/P9.6) |
| ``inbox.py`` | 265 | per-user unified inbox | ``OrgInbox`` | folded into OrgRuntime (P9.6) |
| ``notifier.py`` | 164 | IM push + approval regex parser | ``OrgNotifier`` | folded into OrgRuntime (P9.6) |
| ``plugin_assets.py`` | 137 | pure helpers for plugin asset pipeline | 3 sync + 1 async function | preserved as ``runtime/orgs/plugin_assets.py`` |
| ``plugin_workbench_templates.py`` | 225 | discover plugin workbench presets | 7 functions | folded into OrgManager (P9.5) |
| ``policies.py`` | 277 | per-org markdown policy CRUD + search | ``OrgPolicies`` | preserved as ``runtime/orgs/policies.py`` |
| ``reporter.py`` | 189 | morning / weekly report generator | ``OrgReporter`` | folded into OrgRuntime (P9.6) |
| ``scaler.py`` | 351 | dynamic clone / recruit / dismiss | ``OrgScaler``, ``ScalingRequest`` | folded into OrgRuntime (P9.6) |
| ``tools.py`` | 700 | tool **definitions** (33 ``org_*`` schemas) | ``ORG_NODE_TOOLS`` list of dicts | preserved verbatim as ``runtime/orgs/tool_definitions.py`` |
| ``tool_categories.py`` | 149 | category -> tool-name expansion + presets | 5 functions | preserved as ``runtime/orgs/tool_categories.py`` |
| ``command_tracker.py`` | 123 | per-user-command async lifecycle state | ``UserCommandTracker`` | folded into OrgCommandService (P9.4) |
| ``templates.py`` | 1 234 | 3 prebuilt org template dicts | ``STARTUP_COMPANY``, ``CONTENT_TEAM``, ``CUSTOMER_SERVICE`` + install helpers | preserved as ``runtime/templates/builtin/`` extensions where missing |
| ``__init__.py`` | 44 | re-exports | re-exports 18 symbols | **delete** in P9.9 |

**Total:** 26 files, 18 213 LOC. The three giants are
``runtime.py`` (31.5%), ``tool_handler.py`` (17.5%), and
``templates.py`` (6.8%); together they are 55.8% of the package.

## 1b. The 6 missing v2 subsystems -- precise scope

For each charter subsystem: legacy owner, public surface, LOC
budget derived from extracted symbol count, dependency arrows.

### 1. OrgBlackboard (P9.1) -- LOC budget: 350

* **Legacy owner:** ``orgs/blackboard.py`` (344 LOC, 19 methods).
  Runtime composes via ``OrgRuntime._blackboards``.
* **Public surface (8 critical methods):** ``read_org``,
  ``read_department``, ``read_node``, ``write_org``,
  ``write_department``, ``write_node``, ``get_*_summary``,
  ``query``. Two REST endpoints depend on it
  (``GET/POST /api/orgs/{id}/memory`` lines 1011-1093).
* **Storage:** per-scope JSONL files under
  ``<org_dir>/memory/<scope>/<key>.jsonl`` with size-cap eviction.
* **Dependencies:** ``models`` only (leaf in the DAG).
* **Why first:** no back-references, smallest LOC budget, smallest
  test surface.

### 2. ProjectStore (P9.2) -- LOC budget: 300

* **Legacy owner:** ``orgs/project_store.py`` (247 LOC, 21 methods).
* **Public surface (10 critical methods):** ``list_projects``,
  ``create_project``, ``update_project``, ``delete_project``,
  ``add_task``, ``update_task``, ``delete_task``,
  ``find_task_by_chain``, ``get_task_tree``, ``recalc_progress``.
* **Storage:** one ``projects.json`` per org dir with mtime-watch
  reload + ``threading.RLock``.
* **REST callers:** 12 endpoints (lines 2084-2412).
* **Dependencies:** ``models`` only; composes with
  ``runtime/orgs/sqlite_store.py`` for the SQLite backend.
* **Why second:** leaf module behaviour-wise; biggest delta from
  P9.1 is the parent-child task tree invariants.

### 3. NodeScheduler (P9.3) -- LOC budget: 250

* **Legacy owner:** ``orgs/node_scheduler.py`` (180 LOC, 10
  methods, 7 ``async``).
* **Public surface (5 critical methods):** ``start_for_org``,
  ``stop_for_org``, ``stop_all``, ``reload_node_schedules``,
  ``trigger_once``.
* **Schedule kinds:** ``CRON`` (croniter), ``INTERVAL``, ``ONCE``.
* **REST callers:** 5 endpoints (lines 483-521 + 1677).
* **Dependencies:** ``models``, **and** a back-reference to
  ``OrgRuntime`` for ``send_command``. In v2 the back-reference is
  replaced by a ``CommandDispatcher`` callable injection.
* **DAG resolution:** NodeScheduler ships first with a stub
  ``CommandDispatcher`` Protocol; OrgCommandService in P9.4 then
  satisfies it naturally without a circular import.

### 4. OrgCommandService (P9.4) -- LOC budget: 700

* **Legacy owner:** ``orgs/command_service.py`` (873 LOC, 29
  methods on ``OrgCommandService`` + dataclasses).
* **Public surface (12 critical methods):** ``submit``,
  ``get_status``, ``cancel``, ``subscribe_summary``,
  ``unsubscribe_summary``, ``publish_summary``,
  ``find_command_for_event``, ``mark_delivered``,
  ``bridge_session_chat_id``, plus module singleton
  ``get_command_service()`` / ``set_command_service()``.
* **Verbs handled today:** ``/start``, ``/cancel``, ``/status``,
  ``/resume``, ``/broadcast``, IM verb mapping shared with
  ``channels/gateway.py`` (5 import sites).
* **REST callers:** 3 endpoints.
* **Dependencies:** ``models``, OrgBlackboard (status posting),
  ProjectStore (task<->command linking); injects an
  ``OrgRuntimeProtocol`` for ``send_command`` plumbing.
* **Cancel semantics:** **closes ACCEPTANCE.md #2 caveat** by
  asserting a wall-clock budget on the IM-cancel -> checkpoint
  pipeline (ADR-0013; P9.4 gate criteria).

### 5. OrgManager (P9.5) -- LOC budget: 600

* **Legacy owner:** ``orgs/manager.py`` (588 LOC, 36 methods).
* **Public surface (12 critical methods):** ``create``, ``get``,
  ``update``, ``delete``, ``list_orgs``, ``find_by_name``,
  ``resolve_id_by_name_or_id``, ``duplicate``, ``archive``,
  ``unarchive``, ``save_as_template``, ``create_from_template``
  + dir-layout helpers (``_org_dir``, ``get_org_dir``).
* **REST callers:** ~25 endpoints (CRUD + duplicate + archive +
  templates + import/export + schedule CRUD + identity GET/PUT +
  MCP GET/PUT).
* **Dependencies:** ``models``, ProjectStore (project bootstrap),
  OrgCommandService (import path).
* **Storage:** filesystem layout
  ``<data_dir>/orgs/<org_id>/{org.json,state.json,nodes/<id>/...}``.

### 6. OrgRuntime (P9.6) -- LOC budget: 1 200 (split across files)

* **Legacy owner:** ``orgs/runtime.py`` (5 734 LOC, 144 methods on
  one class). Absorbs the responsibilities P9.1-P9.5 cleaved out.
* **Public surface (~30 critical methods):** lifecycle
  (``start``, ``shutdown``, ``start_org``, ``stop_org``,
  ``delete_org``, ``reset_org``, ``pause_org``, ``resume_org``);
  command (``send_command``, ``cancel_node_task``,
  ``cancel_user_command``); chain bookkeeping
  (``get_current_chain_id``, ``set_current_chain_id``,
  ``is_chain_closed``); accessors (``get_org``, ``get_messenger``,
  ``get_blackboard``, ``get_event_store``, ``get_project_store``,
  ``get_inbox``, ``get_scaler``, ``get_heartbeat``,
  ``get_scheduler``, ``get_notifier``, ``get_reporter``,
  ``get_policies``); tool dispatch (``handle_org_tool``); node
  status (``set_node_status``, ``evict_node_agent``).
* **REST callers:** ~30 endpoints (lifecycle + freeze + standup +
  broadcast + im-reply + events + stats + activity).
* **Why last:** composes every other subsystem; the depth-first
  DAG must be drained before this lands so OrgRuntime is a thin
  shell, not a re-implementation of every leaf.
* **Companion files** absorbed in P9.6 (each its own commit
  sequence): ``tool_handler.py`` (3 183 LOC), ``messenger.py``
  (552 LOC), ``event_store.py`` (361 LOC), ``heartbeat.py``,
  ``inbox.py``, ``notifier.py``, ``scaler.py``, ``reporter.py``,
  ``failure_diagnoser.py``, ``plugin_assets.py``.

### Dependency DAG (topological order)

```
  models.py (preserved as types only; no v2 rewrite needed)
       |
       +---------------------------------------------+
       |                |                 |          |
  OrgBlackboard    ProjectStore     NodeScheduler  (leaves preserved
  [P9.1]           [P9.2]           [P9.3]          verbatim)
       |                |                 |
       +------+---------+                 |
              |                           |
              v                           |
       OrgCommandService <----------------+
       [P9.4]
              |
              +----------+
                         |
                         v
                   OrgManager <-----------------+
                   [P9.5]                       |
                         |                      |
                         v                      |
                   OrgRuntime <-----------------+
                   [P9.6]
                         |
                         v
              api/routes/orgs_v2_full.py [P9.7]
                         |
                         v
              Caller migration [P9.8] -> git rm legacy [P9.9]
                         |
                         v
              G-RC-9 + v2.0.0-rc3 + ACCEPTANCE upgrades [P9.10]
```

**Topological order:** P9.1 -> P9.2 -> P9.3 -> P9.4 -> P9.5 ->
P9.6 -> P9.7 -> P9.8 -> P9.9 -> P9.10.

## 1c. Caller catalog (86 src sites, 216 test sites)

Captured via::

    git grep -nE 'from openakita\.orgs' -- src/openakita/ > recon/callers_src.txt
    git grep -nE 'from openakita\.orgs' -- tests/        > recon/callers_tests.txt

### Production callers by submodule (86 total)

| submodule imported | sites | top external consumers |
|---|---:|---|
| ``.models`` | 32 | ``api/routes/orgs.py`` (most), ``api/routes/chat.py`` (1), intra-orgs (8) |
| ``.project_store`` | 26 | almost all intra-orgs + ``api/routes/orgs.py`` (lines 2071, 2080) |
| ``.command_service`` | 8 | ``api/routes/orgs.py`` (22, 1287), ``channels/gateway.py`` (5 sites) |
| ``.manager`` | 5 | ``api/routes/orgs.py`` (3 sites for OrgNameConflictError), ``api/server.py`` (2 wiring sites) |
| ``.tool_categories`` | 3 | ``api/routes/orgs.py`` (174, 903), intra-orgs (1) |
| ``.runtime`` | 2 | ``api/server.py`` (wiring), ``core/_reasoning_engine_legacy.py:7920`` (lazy import) |
| ``.blackboard`` | 2 | ``api/routes/orgs.py`` (1018, 1045) |
| ``.event_router`` | 1 | ``api/routes/orgs.py`` |
| ``.plugin_workbench_templates`` | 1 | ``api/routes/orgs.py`` (234) |
| ``.templates`` | 1 | ``api/server.py`` |

**Unique src files importing orgs (13):** ``api/routes/orgs.py``
(24 imports), ``api/server.py`` (4), ``channels/gateway.py`` (5),
``api/routes/chat.py`` (1), ``core/_reasoning_engine_legacy.py``
(1), plus 8 intra-``orgs/`` files (which all disappear in P9.9
anyway).

**External callers that must migrate (P9.8 scope):** 5 files --
``api/routes/orgs.py`` (becomes v2-REST file in P9.7),
``api/server.py``, ``channels/gateway.py``, ``api/routes/chat.py``,
``core/_reasoning_engine_legacy.py`` (the runtime import is lazy
and will fall through to v2 once OrgRuntime moves).

**Migration order priority** (fewest callers first):
1. ``.event_router`` (1 site) -- bundled into P9.4 OrgCommandService.
2. ``.plugin_workbench_templates`` (1 site) -- bundled into P9.5.
3. ``.runtime`` (2 sites) -- migrated when P9.6 lands.
4. ``.blackboard`` (2 sites) -- migrated in P9.1 itself.
5. ``.tool_categories`` (3 sites) -- preserved verbatim under
   ``runtime/orgs/tool_categories.py``; 1-line import rewrites.
6. ``.manager`` (5 sites) -- migrated in P9.5.
7. ``.command_service`` (8 sites) -- migrated in P9.4.
8. ``.project_store`` (26 sites) -- migrated in P9.2.
9. ``.models`` (32 sites) -- migrated last (P9.8) because the
   models are duck-typing-stable across v1/v2 and a single bulk
   rewrite is safe once every other surface is on v2.

### Test callers (216 sites across 48 files under tests/orgs/)

Top test-file importers (count of orgs imports per file):

* ``test_plan_features.py`` (44), ``test_external_tools.py`` (9),
  ``test_execution_robustness.py`` (9),
  ``test_org_affinity_attach_fix.py`` (8),
  ``test_reference_resolution.py`` (8), ``test_tool_handler.py``
  (7), ``test_transparency_autonomy.py`` (6),
  ``test_file_delivery_pipeline.py`` (5), ``test_api.py`` (5),
  ``test_org_orchestration_fix.py`` (5), ``conftest.py`` (5),
  ``test_tool_inflight.py`` (5), ``test_llm_integration.py`` (4),
  ``test_external_tools_e2e.py`` (4),
  ``test_org_delegate_self_misjudge_repro.py`` (4).
* Cross-cutting integration tests:
  ``tests/integration/test_gateway_org_control.py`` (5),
  ``tests/unit/test_org_setup_tool.py`` (3),
  ``tests/unit/test_remaining_qa_fixes.py`` (2),
  ``tests/orgs/test_prompt_api_e2e.py`` (2).

**Decision (defaults for Q-B):** ``tests/orgs/`` is **deleted** in
P9.9 as a coherent block (the v2 subsystems get their own test
suite under ``tests/runtime/orgs/`` and ``tests/parity/orgs/``).
Tests that exercise behaviour the v2 surface still owns are
re-pointed; tests that exercise legacy implementation detail are
dropped.

## 1d. REST surface

Captured via ``git grep -nE '^@router\.' -- src/openakita/api/routes/orgs.py``.

* **v1 ``orgs.py``:** 89 endpoints, 2 145 LOC. HTTP verb mix:
  39 POST, 36 GET, 7 PUT, 7 DELETE.
* **v2 ``orgs_v2.py``:** 8 endpoints (template list/get/instantiate
  + CRUD list/create/get/patch/delete).
* **v2 ``orgs_v2_stream.py``:** 1 endpoint
  (``GET /api/v2/orgs/{id}/stream`` SSE).
* **Delta:** 89 - 9 = **80 endpoints with no v2 equivalent**. Each
  is a 1:1 migration unit in P9.7. They cluster into 12 groups:
  1. Avatars / presets (3)
  2. Templates / import / export (6)
  3. Org CRUD (5; partially covered by v2)
  4. Schedules (5)
  5. Node identity + MCP (4)
  6. Lifecycle (start/stop/pause/resume/reset, 5)
  7. Commands (3)
  8. Broadcast / im-reply (2)
  9. Node freeze / online state (5)
  10. Memory + policies + inbox (16)
  11. Scaling (5)
  12. Reports / status / activity / events / stats / tasks (20)

P9.7 ships **all 80** as ``/api/v2/orgs/...`` endpoints with
identical request/response shapes, then P9.9 removes v1 endpoints
(or leaves a 1-release deprecation shim per Q-B in the plan).

## 1e. Test surface

* ``tests/orgs/*.py`` -- **48 files**. Most exercise a single
  legacy submodule (e.g. ``test_blackboard.py``, ``test_messenger.py``).
* ``tests/integration/test_gateway_org_control.py`` -- IM-side
  command verb integration; will move alongside P9.4 + P9.8.
* ``tests/integration/test_v2_im_canary_e2e.py`` +
  ``test_v2_im_cancel.py`` -- already v2-only; will gain wall-clock
  budget assertions in P9.4 per ADR-0013.
* ``tests/unit/test_org_setup_tool.py``,
  ``test_remaining_qa_fixes.py`` -- migrate during P9.8.

**Parity harness design (Phase 5 of plan):** new
``tests/parity/orgs/`` directory mirroring ``tests/parity/``
pattern, one runner pair per subsystem (v1 from
``openakita.orgs``, v2 from ``openakita.runtime.orgs``), fixtures
= recorded JSON inputs + expected outputs. Skeleton lands at
P9.0i; activation per subsystem at P9.1..P9.6.

## 1f. v2 ``runtime/orgs/`` existing surface

| file | LOC | exports |
|---|---:|---|
| ``__init__.py`` | 20 | ``JsonOrgStore``, ``OrgNotFound``, ``SqliteOrgStore``, ``get_default_store``, ``reset_default_store`` |
| ``store.py`` | 204 | ``JsonOrgStore`` (CRUD + factory + singleton); also ``OrgNotFound``, ``_build_store``, ``get_default_store``, ``reset_default_store`` |
| ``sqlite_store.py`` | 188 | ``SqliteOrgStore`` (same contract as JsonOrgStore, multi-process safe) |

**Total existing v2 surface:** 3 files, 412 LOC -- storage-only,
duck-typed contract verified by
``tests/runtime/orgs/test_store_contract.py`` (18 cases).

What the existing surface does **not** include and what P9.1-P9.6
must add: every behaviour above the persistence layer -- runtime,
manager, command service, blackboard (legacy is a three-tier
memory system separate from the v2 store), project store, node
scheduler.

The factory pattern (``get_default_store()`` /
``reset_default_store()``) is a good template for the other 6
subsystems: each will expose a similar process-wide singleton +
test-friendly reset helper so callers don't have to plumb
instances through.

## Appendix A -- baseline pytest counts (reproducible)

* ``.venv/Scripts/python.exe -m pytest tests/runtime tests/agent
  tests/api tests/parity tests/unit/test_plugins -q --tb=no`` ->
  ``1123 passed, 1 skipped, 5 xfailed in 10.44s``.
* ``.venv/Scripts/python.exe -m pytest
  tests/integration/test_v2_im_canary_e2e.py
  tests/integration/test_v2_im_cancel.py
  tests/integration/test_entrypoints.py -q --tb=no`` ->
  ``8 passed in 6.81s``.
* ``.venv/Scripts/python.exe scripts/revamp_loc_audit.py`` ->
  ``exit 0`` (all 15 tracked files within budget).

## Appendix B -- captured files (working tree only, not checked in)

* ``recon/callers_src.txt`` (86 lines)
* ``recon/callers_tests.txt`` (216 lines)
* ``recon/orgs_symbols.txt`` (99 top-level symbols across 26 files)
* ``recon/runtime_public_methods.txt`` (38 public method names
  on ``OrgRuntime``)

The ``recon/`` directory is intentionally not committed (analysis
artefact); the numeric summaries above are authoritative.
