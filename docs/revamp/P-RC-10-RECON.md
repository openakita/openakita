# P-RC-10 P10.0b RECON -- ``runtime/orgs/`` import-sweep inventory + flatten mapping (HEAD ``52f8709a``)

**Status: docs-only inventory.** Per-file:line catalogue of
every ``from openakita.runtime.orgs.X`` /
``import openakita.runtime.orgs.X`` import remaining at
HEAD ``52f8709a2e0eb1f0460834c76309ba37bb2783c3`` (branch
``revamp/v3-orgs``; P-RC-10 P10.0a charter ratification
LANDED + 5 deferred nits + namespace flatten queued),
mapped to its post-flatten ``openakita.orgs.X`` target,
the P10.x sub-phase (P10.1 atomic ``git mv`` / P10.2
shim / P10.3 sweep / P10.4 sentinel) and per-site
rewrite classification (M / PT / N). Mirrors P-RC-9
P9.9-alpha-1 ``IMPORT-SWEEP-INVENTORY`` format
(``docs/revamp/P-RC-9-P9.9-IMPORT-SWEEP-INVENTORY.md``)
and is the **scope authority** for the P10.1..P10.4
contiguous block.

Strict-additive docs boundary: ``git diff 52f8709a..HEAD
-- src/openakita/ tests/ apps/ scripts/ identity/``
returns empty bytes (this commit only touches
``docs/revamp/P-RC-10-RECON.md`` (new) +
``docs/revamp/PROGRESS_LEDGER_P10.md`` (append)).

## 0. Measurement summary

All counts MEASURED on 2026-05-21 against HEAD
``52f8709a`` (branch ``revamp/v3-orgs``).

### 0.1 Recon regex pinning

* **Strict import regex** (scope authority):
  ``^(\s*)(from|import)\s+openakita\.runtime\.orgs`` --
  line-anchored on ``from`` or ``import``; matches
  top-level and indented (deferred-import) sites.
* **String-literal regex** (PT detection):
  ``[\"']openakita\.runtime\.orgs`` -- covers
  ``unittest.mock.patch(...)`` + settings-string callers.
* **Documentation regex** (citation count only, NO
  rewrite in P10.3): ``openakita\.runtime\.orgs`` over
  ``docs/`` + root ``*.md``.

Recon outputs preserved under ``tmp_p10/_p10b_*_grep.txt``
(uncommitted; reproducible in <1 s).

### 0.2 Aggregate counts (STRICT)

| bucket | files | sites |
|---|--:|--:|
| ``src/openakita/`` (incl. 2 self-refs)  | 14 | 35 |
| ``tests/`` (all sub-trees)              | 46 | 86 |
| ``scripts/``                            | 3  | 3  |
| ``apps/``                               | 0  | 0  |
| ``identity/``                           | 0  | 0  |
| **TOTAL (strict imports)**              | **63** | **124** |
| string-literal (PT bucket)              | 0  | 0  |
| docs/citations (no rewrite)             | 13 | 104 |

**Charter section 1.2 comparison** (loose grep at HEAD
``acc7241a``): 71 files / 157 occurrences. The
33-occurrence shrink is the loose-vs-strict delta
(loose includes 33 in-tree docs hits + a few
comment/docstring lines the strict regex rejects); the
8-file shrink is partly hygiene-epic churn between
``acc7241a`` and ``52f8709a`` and partly strict rejection
of README/MD lines.

### 0.3 Classification distribution (124 strict sites)

| class | sites | description |
|---|--:|---|
| **M** (mechanical) | **122** | pure ``from openakita.runtime.orgs.X import Y`` -> ``from openakita.orgs.X import Y`` prefix swap; no surrounding context needed |
| **PT** (patched-target) | **0** | no ``unittest.mock.patch("openakita.runtime.orgs.X.Y")`` string literals anywhere in src/tests/apps/scripts (string-literal grep returns zero matches) |
| **N** (non-trivial) | **2** | both in ``tests/parity/orgs/README.md`` markdown code blocks (lines 58, 63); regex applies cleanly but in docs-like context inside ``tests/`` |

**Implication**: zero dynamic-import / settings-string /
constructed-from-variable callers. Sweep is overwhelmingly
mechanical; sentinel #9 augment (P10.4) reuses the same
strict regex as the v1 sentinel without FP-mitigation logic.

## 1. 25-file flatten mapping

All 25 files relocate **1:1** from
``src/openakita/runtime/orgs/`` to
``src/openakita/orgs/`` at P10.1 via a single
``git mv src/openakita/runtime/orgs src/openakita/orgs``.
No shard split, no content re-layout (M-2 sub-cap
rebalance rides P10.5a). Public exports column lists the
names re-exported by the current ``__init__.py`` (each
``from .X import (...)`` group; see
``tmp_p10/_p10b_init_full.txt`` for the verbatim block).

| # | Source | Target | LOC | Public exports (via ``__init__.py``) |
|--:|---|---|--:|---|
| 1  | ``runtime/orgs/__init__.py`` | ``orgs/__init__.py`` | 436 | (umbrella; re-exports all 21 sibling modules) |
| 2  | ``runtime/orgs/_org_layout.py`` | ``orgs/_org_layout.py`` | 148 | (private; no re-export) |
| 3  | ``runtime/orgs/_runtime_agent_pipeline.py`` | ``orgs/_runtime_agent_pipeline.py`` | 435 | ORG_STATE_ACTIVE, ORG_STATE_PAUSED, AgentBuilderProtocol, AgentCache, AgentPipelineExecutor, AgentSpec, ProfileResolver |
| 4  | ``runtime/orgs/_runtime_dispatch.py`` | ``orgs/_runtime_dispatch.py`` | 275 | TRACKER_{CANCELLED,DEADLOCK_STOPPED,FINALIZED,RUNNING}, CommandDispatchManager |
| 5  | ``runtime/orgs/_runtime_event_bus.py`` | ``orgs/_runtime_event_bus.py`` | 119 | InMemoryEventBus, WebSocketEventBus, get_default_event_bus |
| 6  | ``runtime/orgs/_runtime_event_store.py`` | ``orgs/_runtime_event_store.py`` | 98  | (private; no top-level re-export) |
| 7  | ``runtime/orgs/_runtime_lifecycle.py`` | ``orgs/_runtime_lifecycle.py`` | 195 | STATE_{ACTIVE,CREATED,DELETED,PAUSED,STOPPED}, IllegalOrgTransition, OrgLifecycleManager |
| 8  | ``runtime/orgs/_runtime_node_lifecycle.py`` | ``orgs/_runtime_node_lifecycle.py`` | 274 | STATUS_{BUSY,ERROR,IDLE,STOPPED}, NodeMessageRouter, NodeStatusController, format_incoming_message, is_stop_intent |
| 9  | ``runtime/orgs/_runtime_plugin_assets.py`` | ``orgs/_runtime_plugin_assets.py`` | 476 | FileOutput, FileOutputRegistry, PluginAsset, PluginAssetRecorder, SynthesizedDelivery, TaskDeliverySynthesizer, ToolHandlerBridge, collect_tool_stats_from_trace, ext_for_url, extract_accepted_chain_ids, is_plugin_tool, plugin_id_for_tool, react_trace_has_tool, safe_asset_filename |
| 10 | ``runtime/orgs/_runtime_templates.py`` | ``orgs/_runtime_templates.py`` | 1572 | build_workbench_templates, ensure_builtin_templates, list_avatar_presets (**self-ref: 1 abs import @ L1333** -> P10.1) |
| 11 | ``runtime/orgs/_runtime_watchdog.py`` | ``orgs/_runtime_watchdog.py`` | 181 | CommandWatchdog, IdleProbeLoop |
| 12 | ``runtime/orgs/_slug.py`` | ``orgs/_slug.py`` | 66  | slugify_template_id (imported via private path by ``scripts/migrate_non_ascii_template_ids.py``) |
| 13 | ``runtime/orgs/blackboard.py`` | ``orgs/blackboard.py`` | 634 | MAX_{DEPT,NODE,ORG}_MEMORIES, BlackboardBackendProtocol, JsonFileBlackboardBackend, OrgBlackboard, SqliteBlackboardBackend, get_default_blackboard_backend |
| 14 | ``runtime/orgs/command_models.py`` | ``orgs/command_models.py`` | 255 | ForwardTarget, OrgCommandConflict/Error/Request/Response/Source/Surface, OrgOutputScope, default_scope_for_surface, new_command_id, origin_surface_label_cn |
| 15 | ``runtime/orgs/command_service.py`` | ``orgs/command_service.py`` | 896 | BrainProtocol, ChannelGatewayProtocol, CommandRuntimeProtocol, EventEmitterProtocol, OrgCommandService, OrgCommandServiceProtocol, OrgLookupProtocol, SessionManagerProtocol, get_command_service, set_command_service |
| 16 | ``runtime/orgs/manager.py`` | ``orgs/manager.py`` | 813 | OrgFactoryProtocol, OrgLifecycleEmitterProtocol, OrgManager, OrgNameConflictError, OrgPersistenceProtocol, get_org_manager (**self-refs: 3 abs imports @ L55/65/909** -> P10.1) |
| 17 | ``runtime/orgs/memory_models.py`` | ``orgs/memory_models.py`` | 105 | MemoryScope, MemoryType, OrgMemoryEntry |
| 18 | ``runtime/orgs/node_scheduler.py`` | ``orgs/node_scheduler.py`` | 393 | CLEAN_THRESHOLD, FREQUENCY_MULTIPLIER, MAX_FREQUENCY_FACTOR, RECHECK_DELAY, CommandDispatcher, NodeSchedulerProtocol, OrgNodeScheduler, SchedulerRuntimeProbe, ScheduleStore, build_schedule_prompt, compute_next_fire_time |
| 19 | ``runtime/orgs/org_models.py`` | ``orgs/org_models.py`` | 588 | EdgeType, NodeStatus, Organization, OrgEdge, OrgNode, OrgStatus, UserPersona, infer_agent_profile_id_for_node, new_org_id, now_iso |
| 20 | ``runtime/orgs/project_models.py`` | ``orgs/project_models.py`` | 192 | OrgProject, ProjectStatus, ProjectTask, ProjectType, TaskStatus, new_project_id, new_task_id |
| 21 | ``runtime/orgs/project_store.py`` | ``orgs/project_store.py`` | 783 | JsonProjectStore, ProjectStoreProtocol, SqliteProjectStore, get_default_project_store, reset_default_project_stores |
| 22 | ``runtime/orgs/runtime.py`` | ``orgs/runtime.py`` | 364 | EventBusProtocol, NodeLifecycleProtocol, OrgRuntime, RuntimeStateProtocol, get_runtime |
| 23 | ``runtime/orgs/scheduler_models.py`` | ``orgs/scheduler_models.py`` | 122 | NodeSchedule, ScheduleType, new_schedule_id |
| 24 | ``runtime/orgs/sqlite_store.py`` | ``orgs/sqlite_store.py`` | 188 | SqliteOrgStore |
| 25 | ``runtime/orgs/store.py`` | ``orgs/store.py`` | 204 | JsonOrgStore, OrgNotFound, get_default_store, reset_default_store |
| -- | **TOTAL** | -- | **~9 810** | 21 ``from .X import (...)`` blocks; **no** module-level ``__all__`` literal |

All 25 rows are 1:1 (no rename, no shard split, no
merge). The two self-ref clusters (rows 10 + 16, 4
total sites) MUST land in the P10.1 atomic commit so
the v2 tree compiles against the new path standalone
(independent of the P10.2 shim).

## 2. Import sites by category

### 2.1 Per-directory breakdown (sites + files)

| cluster | files | sites | typical caller | sweep phase |
|---|--:|--:|---|---|
| ``src/openakita/api/`` | 5 | 23 | orgs_v2_runtime_orgs (7) / orgs_v2_runtime_projects (5) / server (4) / orgs_v2_runtime_state (2) + 1 sibling route | P10.3a |
| ``src/openakita/channels/`` | 1 | 6 | gateway.py (5 deferred + 1 module-level) | P10.3a |
| ``src/openakita/runtime/orgs/`` (self) | 2 | 4 | manager.py x3 + _runtime_templates.py x1 -- absolute self-imports | **P10.1** (atomic) |
| ``src/openakita/`` (other) | 2 | 2 | core/_reasoning_engine_legacy.py (deferred) + runtime/channel_routing.py | P10.3a |
| ``tests/runtime/orgs/`` | 18 | 23 | v2 contract tests (test_runtime_contract = 4, test_store_contract = 2, etc.) | P10.3b |
| ``tests/runtime/`` (other) | 4 | 7 | test_channel_routing, test_cancel_wall_clock_budget, etc. | P10.3b |
| ``tests/api/`` | 11 | 18 | contracts/test_orgs_v2_contracts_{orgs=3,dispatch=2,projects=2} + server wiring + p97_beta smokes | P10.3c |
| ``tests/parity/`` | 7 | 19 | parity sentinels #1-#6 (Option-B v2-only) + 1 README.md | P10.3c |
| ``tests/unit/`` | 6 | 10 | test_org_setup_tool (3) + c17_second_pass / delegation_preamble (2 each) + others | P10.3d |
| ``tests/integration/`` | 3 | 7 | test_gateway_org_control (5) + 2 IM canary sites | P10.3d |
| ``tests/e2e/`` | 1 | 2 | test_p0_regression.py | P10.3d |
| ``scripts/`` | 3 | 3 | migrate_orgs_to_v2 / migrate_orgs_v2_json_to_sqlite / migrate_non_ascii_template_ids | P10.3e |
| **TOTAL** | **63** | **124** | -- | -- |

### 2.2 Top 12 most-affected files (>=3 sites)

| sites | file |
|--:|---|
| 7 | ``src/openakita/api/routes/orgs_v2_runtime_orgs.py`` |
| 6 | ``tests/parity/orgs/test_node_scheduler_parity.py`` |
| 6 | ``src/openakita/channels/gateway.py`` |
| 5 | ``src/openakita/api/routes/orgs_v2_runtime_projects.py`` |
| 5 | ``tests/integration/test_gateway_org_control.py`` |
| 5 | ``tests/parity/orgs/test_runtime_parity.py`` |
| 4 | ``src/openakita/api/server.py`` |
| 4 | ``tests/runtime/orgs/test_runtime_contract.py`` |
| 3 | ``tests/api/contracts/test_orgs_v2_contracts_orgs.py`` |
| 3 | ``tests/unit/test_org_setup_tool.py`` |
| 3 | ``src/openakita/runtime/orgs/manager.py`` (self) |
| 3 | ``tests/api/test_server_app_wiring.py`` |

Remaining 51 files carry 1-2 sites each (52 sites total).
Full enumeration in ``tmp_p10/_p10b_strict_grep.txt``.

## 3. Non-trivial sites detailed (N = 2)

Both N sites are in the same Markdown file inside the
parity tests tree, inside a 4-space-indented code-block
snippet documenting the v2 blackboard import path for
new contributors.

| # | file:line | content | recommended strategy |
|--:|---|---|---|
| N-1 | ``tests/parity/orgs/README.md:58`` | ``from openakita.runtime.orgs.blackboard import OrgBlackboard`` | mechanical regex swap in P10.3c -- the line lives in a doc, but the ``s/openakita\.runtime\.orgs/openakita\.orgs/`` rule applies cleanly; flag in commit body as "README sample import rewrite" so reviewers know it is doc text, not test code |
| N-2 | ``tests/parity/orgs/README.md:63`` | ``from openakita.runtime.orgs.blackboard import OrgBlackboard`` | same as N-1 (second occurrence in the same code block) |

Zero dynamic-import callers
(``importlib.import_module("openakita.runtime.orgs...")``
returns 0), zero settings-string callers, and zero
comment-only mentions matching the strict regex (the
regex requires line-leading ``from`` / ``import``).

## 4. Shim design (preliminary; P10.2 spec input)

The P10.2 mini-commit lands a single
``src/openakita/runtime/orgs/__init__.py`` re-export
shim (<=30 LOC). Sentinel #9 augment (P10.4) allowlists
this one file and rejects every other site under
``openakita.runtime.orgs.*``.

### 4.1 Surface to re-export

* Current umbrella ``__init__.py`` (row 1 of section 1)
  re-exports via 21 explicit ``from .X import (...)``
  blocks; after P10.1 the new
  ``src/openakita/orgs/__init__.py`` carries the same
  21 blocks verbatim (one ``git mv`` byte-preserves).
* The shim must expose BOTH (a) the umbrella names
  (``from openakita.runtime.orgs import OrgRuntime``)
  AND (b) the submodule paths
  (``from openakita.runtime.orgs.command_service
  import X``; 122 of 124 strict sites use form (b)).
* (a) is handled by ``from openakita.orgs import *``.
* (b) requires registering each of the 24 sibling
  modules in ``sys.modules`` under the legacy dotted
  path so ``from openakita.runtime.orgs.X import Y``
  resolves to the v2 module object.

### 4.2 Pattern sketch (final form lands in P10.2)

* Module docstring: ``"DEPRECATED: import from
  openakita.orgs instead; removed in P10.6."``
* ``import openakita.orgs as _orgs`` +
  ``from openakita.orgs import *`` (umbrella re-export).
* ``warnings.warn(..., DeprecationWarning, stacklevel=2)``
  emitted at first import.
* Loop over the 24 sibling submodule names (10
  public + 14 underscore-prefixed; full list in
  section 1) and ``_sys.modules[f"{__name__}.{name}"]
  = __import__(f"openakita.orgs.{name}", fromlist=...)``
  to register each under the legacy path.

### 4.3 Sentinel #9 augment shape (P10.4 spec input)

* Strict regex same shape as the v1 sentinel:
  ``^(\s*)(from|import)\s+openakita\.runtime\.orgs``
  (the regex used in section 0.1).
* Allowlist = exactly one path:
  ``src/openakita/runtime/orgs/__init__.py``.
* Optional second assertion: shim body contains the
  DeprecationWarning literal (defends against silent
  re-export regression).

## 5. Sub-phase commit plan (P10.3 refinement)

Charter section 2 projected 6-8 P10.3 sub-commits keyed
by directory cluster. With concrete counts in hand (124
sites / 63 files), 5 mini-commits give a clean spread,
each well within the 380-LOC envelope:

| commit | scope | files | sites | est. diff (ins/del) |
|---|---|--:|--:|---|
| P10.3a | ``src/openakita/`` (api 5 + channels 1 + core 1 + runtime/channel_routing 1; ``runtime/orgs/`` self-refs already swept at P10.1) | 8 | 31 | ~+45 / ~-45 |
| P10.3b | ``tests/runtime/`` (incl. ``tests/runtime/orgs/``) | 22 | 30 | ~+45 / ~-45 |
| P10.3c | ``tests/api/`` + ``tests/parity/`` (incl. 2 README N sites) | 18 | 37 | ~+55 / ~-55 |
| P10.3d | ``tests/unit/`` + ``tests/integration/`` + ``tests/e2e/`` | 10 | 19 | ~+30 / ~-30 |
| P10.3e | ``scripts/`` + ledger close + post-sweep grep snapshot | 3 + ledger | 3 | ~+25 / ~-3 |
| **total** | -- | **61 + ledger** | **120** | **~+200 / ~-180** |

(120 not 124 = 4 self-ref sites already rewritten in
P10.1; the 122 mechanical lines move through P10.3a-e
along with the 2 README N sites.) Each sub-commit runs
the strict grep before/after and confirms monotonic
shrinkage. P10.3e closes with ``git grep -l
openakita\.runtime\.orgs -- src/ tests/ scripts/``
returning exactly the one shim file
(``src/openakita/runtime/orgs/__init__.py`` landed at
P10.2).

## 6. Risks revisited (concrete numbers)

* **R-10-1 (MED -> LOW post-P10.4) -- shim coexistence
  import ambiguity.** 124 strict sites confirmed;
  **PT = 0** means zero string-literal sites slip past
  sentinel-#9 augment. P10.4 covers the gap.
* **R-10-2 (MED) -- in-flight branch collisions.**
  Multi-branch survey impractical from a single-branch
  ``revamp/v3-orgs`` checkout; **ACKNOWLEDGED** without
  numeric measure. Mitigation = contiguous P10.1..P10.4
  working session + 24 h freeze on ``revamp/v3-orgs``
  per charter section 3.
* **R-10-3 (LOW) -- third-party plugins.** Unmeasurable
  from in-tree. P10.2 shim + DeprecationWarning is the
  full mitigation; no recon delta.
* **R-10-4 (LOW) -- ``tests/runtime/orgs/`` move.** 18
  files / 23 sites currently live there. After P10.1
  they still test ``openakita.orgs.X`` correctly via
  P10.3b sweep, but the dir name misleads.
  **Recommendation**: rename
  ``tests/runtime/orgs/ -> tests/orgs/`` as a
  post-P10.6 follow-on (or P-RC-11 candidate); **do
  NOT** bundle into P10.1 (would conflate two concerns
  and inflate P10.1 from ~30 LOC ``__init__``
  reshuffle to ~50 file moves).
* **R-10-5 (LOW) -- M-2 shard split.** Untouched by
  this recon; charter section 3 mitigation holds.

## 7. P10.1 readiness verdict: **GREEN**

* All 25 v2 files inventoried; LOC sums to ~9 810; no
  shard split, no rename, no merge.
* Only 4 sites (manager.py x3 + _runtime_templates.py
  x1) are absolute self-imports inside the moving
  directory; they MUST land in the P10.1 atomic commit
  alongside ``git mv`` so the post-move tree compiles
  standalone (independent of the P10.2 shim).
* PT = 0: zero string-literal callers; no hidden
  mock.patch / settings landmines.
* No circular cross-package dependency
  (``__init__.py`` uses 21 relative ``from .X``
  imports; absolute self-refs isolated to 2 files).
* All 25 files independently movable via a single
  ``git mv`` (directory is the unit of movement).

## 8. P10.0b post-write verification

Counts above remain unchanged because P10.0b is
docs-only (touches ``docs/revamp/P-RC-10-RECON.md`` +
ledger only). Re-run the strict grep against the new
HEAD:

```
git grep -nP "^(\s*)(from|import)\s+openakita\.runtime\.orgs" \
    -- src/ tests/ apps/ scripts/ identity/ | Measure-Object -Line
# Expected: 124 lines / 63 files (== section 0.2).
```

If counts drift between this commit's HEAD and P10.1
landing, this inventory is patched in a follow-up
P10.0b-nit commit BEFORE P10.1 opens. No drift at
write time (HEAD ``52f8709a``; recon scripts run in
< 1 s).

## 9. Cross-references + HARD STOP

* Charter ``docs/revamp/P-RC-10-CHARTER.md`` section 1
  (scope + 25-file inventory) + section 2 (P10.0..P10.7
  sub-phase plan; P10.0b mirrored above).
* Charter ``docs/revamp/P-RC-9-P9.9-CHARTER.md`` --
  recon-doc style template.
* ``docs/revamp/P-RC-9-P9.9-IMPORT-SWEEP-INVENTORY.md``
  -- structural template this doc mirrors.
* ADR-0011 (subsystem decomposition; no Protocol change
  at P10.1); ADR-0014 (OrgRuntime budget; M-2 rides
  P10.5a); ADR-0015 (308 shim; ZERO touch in P-RC-10).
* ``docs/revamp/PROGRESS_LEDGER_P10.md`` -- this commit
  appends the P10.0b row.

**HARD STOP**: P10.0b docs-only round. **P10.1 NOT
started.** Next operator signal opens P10.1 (single
atomic ``git mv`` + 4 self-ref rewrites + 2
``__init__.py`` reshuffles per charter section 2;
sentinel #9 augment rides P10.4). ``git diff
52f8709a..HEAD -- src/openakita/ tests/ apps/ scripts/
identity/`` returns empty bytes.

**P-RC-10 status**: P10.0a charter LANDED; **P10.0b
recon LANDED**; P10.1 / P10.2 / P10.3a-e / P10.4 /
P10.5a-f / P10.6 / P10.7 unscheduled.
