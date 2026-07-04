# P-RC-9 P9.9ε-1b Audit -- v1 src deletion readiness

Authority: P-RC-9 P9.9 charter (``d49388bb``) §2.1 + §4 R1;
P9.9ε charter (``0765b3e0``) §0 exit criteria + §3 risk
register. Required by ε charter §2 as the verdict gate for
ε-2 scheme selection (default 2-phase GREEN vs scheme C
3-phase YELLOW). Mirrors the δ-1 ``P-RC-9-P9.9-COVERAGE-AUDIT``
template that retired R2.

HEAD at audit: ``0765b3e0`` (close of ε-1a; ``revamp/v3-orgs``;
charter doc only). All measurements taken at this HEAD; ε-1a
charter introduces no code edits, so every count below also
matches HEAD ``4b5499a6`` (close of δ-4). Strict-additive on
v1 src: ``git diff a3a5fde6..HEAD -- src/openakita/orgs/`` =
empty bytes.

## 0. Measurement summary

| tree | files | LOC |
|---|--:|--:|
| ``src/openakita/orgs/`` (v1; **ε-2b delete target**) | 26 | 20 237 |
| ``src/openakita/runtime/orgs/`` (v2 destination) | 23 | 10 886 |
| ``src/openakita/api/routes/orgs.py`` (v1 router; **ε-2a delete target**) | 1 | 2 533 |
| ``src/openakita/api/routes/_orgs_v2_legacy_redirects.py`` (308 shim; **ADR-0015 NO-OP**) | 1 | 101 |
| ``scripts/run_org_live_test.py`` + ``scripts/test_org_full_task.py`` (dev probes; **ε-2a delete target**) | 2 | ~560 |

v2 net LOC count is *less than half* the v1 baseline because
v1 carried ~10 kLOC of plan-feature toggles, legacy event
fan-out, and tool-handler glue that v2 collapses into focused
shards (per γ-1b / γ-2b absorption history; main charter §3.3).

### 0.1 Reproducibility commands

```
git ls-files src/openakita/orgs/                                              # 26
python -c "from pathlib import Path; print(sum(len(p.read_text(encoding='utf-8',errors='ignore').splitlines()) for p in Path('src/openakita/orgs').glob('*.py')))"   # 20237
git ls-files src/openakita/runtime/orgs/                                      # 23
python -m pytest --collect-only -q 2>&1 | Select-Object -Last 5               # 6160/6166
python -m pytest tests/parity/orgs/ tests/runtime/orgs/ tests/api/ tests/integration/test_v2_im_canary_e2e.py -q --tb=no | Select-Object -Last 3    # 585 passed
```

## 1. v1 src inventory (26 files / 20 237 LOC)

Per ``git ls-files src/openakita/orgs/`` + per-file LF-line
count at HEAD ``0765b3e0``:

| # | file | LOC |
|--:|---|--:|
| 1 | ``__init__.py`` | 47 |
| 2 | ``blackboard.py`` | 391 |
| 3 | ``command_service.py`` | 963 |
| 4 | ``command_tracker.py`` | 139 |
| 5 | ``event_router.py`` | 123 |
| 6 | ``event_store.py`` | 411 |
| 7 | ``failure_diagnoser.py`` | 502 |
| 8 | ``heartbeat.py`` | 454 |
| 9 | ``identity.py`` | 475 |
| 10 | ``inbox.py`` | 312 |
| 11 | ``manager.py`` | 683 |
| 12 | ``messenger.py`` | 651 |
| 13 | ``models.py`` | 1 018 |
| 14 | ``node_scheduler.py`` | 215 |
| 15 | ``notifier.py`` | 200 |
| 16 | ``plugin_assets.py`` | 159 |
| 17 | ``plugin_workbench_templates.py`` | 258 |
| 18 | ``policies.py`` | 331 |
| 19 | ``project_store.py`` | 281 |
| 20 | ``reporter.py`` | 227 |
| 21 | ``runtime.py`` | 6 355 |
| 22 | ``scaler.py`` | 419 |
| 23 | ``templates.py`` | 1 266 |
| 24 | ``tool_categories.py`` | 172 |
| 25 | ``tool_handler.py`` | 3 474 |
| 26 | ``tools.py`` | 711 |
| -- | **TOTAL** | **20 237** |

Top-5 dominate: ``runtime`` + ``tool_handler`` + ``templates``
+ ``models`` + ``command_service`` = **13 076 LOC = 65 %** of
the subsystem; remaining 21 files cover the long tail.

## 2. Production caller scan

### 2.1 Loose grep (substring match)

``git grep -ln "openakita\.orgs" -- src/openakita/
":(exclude)src/openakita/orgs/" apps/ scripts/ identity/`` returns
**22 files**. The bulk (19 files) are docstring / comment
back-references inside ``src/openakita/runtime/orgs/*.py`` that
name the v1 module each v2 shard replaces (“replaces v1
``openakita.orgs.X``”) -- **not** active imports. Per ADR-0011
no-shim invariant ``runtime/orgs/`` ships zero ``openakita.orgs``
runtime imports.

### 2.2 Strict grep (real import statements only)

``git grep -n -E "^(\s+)?(from|import)\s+openakita\.orgs(\.|$|\s)"
-- src/openakita/ ":(exclude)src/openakita/orgs/" apps/ scripts/
identity/`` returns **3 files / 30 sites**:

| file | sites | nature | retirement |
|---|--:|---|---|
| ``scripts/run_org_live_test.py`` | 3 | dev smoke probe: ``OrgManager`` / ``OrgRuntime`` / ``OrgNode`` + ``Organization`` + ``OrgEdge`` + ``EdgeType`` -- 3-agent live-LLM run | ``git rm`` in ε-2a |
| ``scripts/test_org_full_task.py`` | 3 | dev smoke probe: ``OrgManager`` / ``OrgRuntime`` / ``models.{EdgeType,NodeStatus,OrgEdge,OrgNode,OrgStatus,Organization}`` -- end-to-end live-LLM run | ``git rm`` in ε-2a |
| ``src/openakita/api/routes/orgs.py`` | 24 | v1 router itself (89 endpoints / 2 533 LOC): ``command_service`` (×2), ``models`` (×11), ``manager`` (×4), ``tool_categories`` (×2), ``plugin_workbench_templates`` (×1), ``blackboard`` (×2), ``project_store`` (×1), ``InboxPriority`` (×1) | ``git rm`` in ε-2a |
| **TOTAL** | **30** | -- | all retired in ε-2a |

``apps/`` + ``identity/`` scans return **0 hits** (P9.8δ-2
closed frontend caller migration; ``identity/`` has never
touched ``orgs/``).

### 2.3 Top-level re-export consumers

``git grep -n -E "from openakita\.orgs import|import openakita\.orgs(\s|$)"
-- src/openakita/ apps/ scripts/ identity/`` returns **0 hits**.
R4 (orphaned ``__init__`` re-exports per main P9.9 charter §4)
does NOT apply for ε -- the v1 ``__init__.py`` re-export chain
has no live consumer; the chain dies with the directory in ε-2b.

## 3. v1 → v2 absorption matrix (26 rows; R-ε-4 evidence)

Verdict legend: **COMPLETE** = same-name v2 file (1:1) OR
absorbed-into-named-shard with live caller; **ABSORBED-
TRANSITIVELY** = no live caller exists for the v1 surface
(parent module disappears in ε-2b by construction); **ABSENT**
= live caller present but v2 lacks coverage (would block ε-2b).

| # | v1 module | v2 destination (per γ-1b / γ-2b / P9.x history) | verdict |
|--:|---|---|---|
| 1 | ``__init__.py`` | ``runtime/orgs/__init__.py`` (v2 re-exports; no live ``from openakita.orgs import`` consumer per §2.3) | COMPLETE |
| 2 | ``blackboard.py`` | ``runtime/orgs/blackboard.py`` (1:1) | COMPLETE |
| 3 | ``command_service.py`` | ``runtime/orgs/command_service.py`` + ``command_models.py`` | COMPLETE |
| 4 | ``command_tracker.py`` | ``runtime/orgs/_runtime_dispatch.py`` + ``_runtime_watchdog.py`` (P9.4c + P9.6γ) | COMPLETE |
| 5 | ``event_router.py`` | ``runtime/orgs/_runtime_event_bus.py`` (P9.4c) | COMPLETE |
| 6 | ``event_store.py`` | ``runtime/orgs/_runtime_event_bus.py`` (state surface) | COMPLETE |
| 7 | ``failure_diagnoser.py`` | ``runtime/orgs/_runtime_watchdog.py`` (P9.6γ) | COMPLETE |
| 8 | ``heartbeat.py`` | ``runtime/orgs/_runtime_watchdog.py`` + ``_runtime_event_bus.py`` | COMPLETE |
| 9 | ``identity.py`` | ``runtime/orgs/manager.py`` (folded into OrgManager, P9.5c) | COMPLETE |
| 10 | ``inbox.py`` | ``runtime/orgs/_runtime_event_bus.py`` (state surface) | COMPLETE |
| 11 | ``manager.py`` | ``runtime/orgs/manager.py`` (1:1) | COMPLETE |
| 12 | ``messenger.py`` | ``runtime/orgs/_runtime_event_bus.py`` + ``command_service.py`` (P9.4c) | COMPLETE |
| 13 | ``models.py`` | 5-shard split: ``command_models`` / ``memory_models`` / ``project_models`` / ``scheduler_models`` + ``org_models`` (P9.1c-P9.4c + γ-2b) | COMPLETE |
| 14 | ``node_scheduler.py`` | ``runtime/orgs/node_scheduler.py`` (1:1) | COMPLETE |
| 15 | ``notifier.py`` | ``runtime/orgs/_runtime_event_bus.py`` (no live v1 caller per §2.2) | ABSORBED-TRANSITIVELY |
| 16 | ``plugin_assets.py`` | ``runtime/orgs/_runtime_plugin_assets.py`` (P9.2c) | COMPLETE |
| 17 | ``plugin_workbench_templates.py`` | ``runtime/orgs/_runtime_templates.py`` (γ-1b absorbed) | COMPLETE |
| 18 | ``policies.py`` | global ``identity/policies`` + ``runtime/orgs/manager.py`` (no live v1 caller) | ABSORBED-TRANSITIVELY |
| 19 | ``project_store.py`` | ``runtime/orgs/project_store.py`` + ``sqlite_store.py`` + ``store.py`` | COMPLETE |
| 20 | ``reporter.py`` | covered by ``_runtime_event_bus.py`` snapshot surface (no live v1 caller) | ABSORBED-TRANSITIVELY |
| 21 | ``runtime.py`` | ``runtime/orgs/runtime.py`` + 5 ``_runtime_*`` slices | COMPLETE |
| 22 | ``scaler.py`` | covered by ``_runtime_lifecycle.py`` / ``_runtime_node_lifecycle.py`` (no live v1 caller) | ABSORBED-TRANSITIVELY |
| 23 | ``templates.py`` | ``runtime/orgs/_runtime_templates.py`` + ``_runtime_plugin_assets.py`` (γ-1b absorbed) | COMPLETE |
| 24 | ``tool_categories.py`` | ``runtime/orgs/_runtime_templates.py`` + ``_runtime_plugin_assets.py`` (γ-1b absorbed) | COMPLETE |
| 25 | ``tool_handler.py`` | ``runtime/orgs/_runtime_agent_pipeline.py`` + ``_runtime_dispatch.py`` (org-graph half γ-2b absorbed; agent-pipeline half exercised through executor; no live v1 caller per §2.2) | COMPLETE |
| 26 | ``tools.py`` | global ``src/openakita/tools/`` subsystem (out of orgs/; no live v1 caller) | ABSORBED-TRANSITIVELY |
| -- | **TOTAL** | -- | **21 COMPLETE + 5 ABSORBED-TRANSITIVELY + 0 ABSENT** |

**Verdict: 0 ABSENT.** R-ε-4 RETIRED at audit close. The 5
ABSORBED-TRANSITIVELY rows (``notifier``, ``policies``,
``reporter``, ``scaler``, ``tools``) have zero strict-grep
production callers per §2.2 -- their parent v1 module disappears
in ε-2b and no out-of-tree consumer exercises the symbol.

## 4. 308 shim cleanness (R-ε-2 evidence)

``src/openakita/api/routes/_orgs_v2_legacy_redirects.py``: 101
LOC; ``git grep -n "openakita\.orgs" --
src/openakita/api/routes/_orgs_v2_legacy_redirects.py`` returns
**0 hits**.

The shim imports ONLY ``fastapi.{APIRouter, Request, Response}``
and ``__future__.annotations`` -- zero ``openakita.*`` imports
at all (not just zero v1; zero v2 either). Each of the 9 routes
is a ``router.add_api_route(...)`` registering a 308 ``Response``
whose ``Location`` header points at ``/api/v2/orgs-spec/...``.

Therefore ε-2b deleting ``src/openakita/orgs/`` has **zero
blast radius** on the 308 shim, and ADR-0015 NO-OP for shim is
structurally preserved by construction (not just by discipline).

**R-ε-2 RETIRED at audit close.**

## 5. Baseline metrics (R-ε-3 evidence + predicted post-deltas)

Measured at HEAD ``0765b3e0`` (ε-1a charter close; no code
delta vs HEAD ``4b5499a6``):

| metric | value | command |
|---|---|---|
| pytest --collect-only | **6 160 / 6 166** (6 deselected) | ``python -m pytest --collect-only -q | Select-Object -Last 5`` |
| narrow slice | **585 / 585 PASS in 65.62 s** | ``python -m pytest tests/parity/orgs/ tests/runtime/orgs/ tests/api/ tests/integration/test_v2_im_canary_e2e.py -q --tb=no`` |
| v2 IM canary (×3) | 1 / 1 PASS at **1.61 / 1.62 / 1.63 s** (avg **1.62 s**) | ``python -m pytest tests/integration/test_v2_im_canary_e2e.py -q --tb=no`` (×3) |
| sentinels ACTIVE | **8 / 8** (case counts 8 + 6 + 4 + 10 + 12 + 20 + 1 + 1) | per δ-4 ledger row (``4b5499a6``) |

Predicted post-ε-2b deltas (per ε charter §6):

| metric | baseline | post-ε-2a | post-ε-2b | rationale |
|---|---|---|---|---|
| collect-only | 6 160 / 6 166 | 6 160 / 6 166 ±0 | **6 160 / 6 166 ±0** | every v1 site is inside ε-2a or ε-2b deletion; scripts are not collected; v1 router self-deletes |
| narrow slice | 585 / 585 | 585 / 585 | **585 / 585 unchanged** | narrow slice imports zero v1 since β-1 / γ-1 / γ-2 |
| canary avg | 1.62 s | 1.62 s ±5 % | **1.62 s ±5 %** | canary uses v2 runtime since P9.9β-1 |
| sentinels | 8 / 8 | 8 / 8 | **8 / 8** (9th deferred to G-RC-9.9 η-1) | ε commits touch no sentinel file |

R-ε-3 MITIGATED via per-ε-commit ``--collect-only`` gate.

## 6. R-ε verdicts

* **R-ε-1 (HIGH)** residual v1 imports in production code:
  **CONDITIONAL on ε-2a landing first**. 3 files / 30 sites
  (§2.2). Retires unconditionally at ε-2a close.
* **R-ε-2 (MED)** 308 shim imports v1: **RETIRED**. 0 v1
  literals at HEAD ``0765b3e0`` (§4).
* **R-ε-3 (MED)** pytest collect-only inflation: **MITIGATED**.
  Per-commit ``--collect-only`` re-run gate (§5); no v1 site
  survives ε-2b deletion.
* **R-ε-4 (LOW)** ``runtime/orgs`` absorption gap: **RETIRED**.
  Matrix is 21 COMPLETE + 5 ABSORBED-TRANSITIVELY + 0 ABSENT
  (§3).

## 7. ε-2 readiness verdict: **YELLOW (scheme C required)**

Trigger: R-ε-1 is CONDITIONAL because 3 production files / 30
sites import ``openakita.orgs.*`` at HEAD ``0765b3e0``:

* ``src/openakita/api/routes/orgs.py`` -- the v1 router itself
  (24 sites). Original P9.9 main charter §1.1 grouped its
  deletion with the subsystem ``git rm``; this charter splits
  them into ε-2a + ε-2b for review clarity and OpenAPI
  snapshot separability.
* ``scripts/run_org_live_test.py`` (3 sites) +
  ``scripts/test_org_full_task.py`` (3 sites) -- dev smoke
  probes written against v1; not collected by pytest, not
  packaged; ``git rm`` in ε-2a (alternative = swap to v2, but
  default = delete since both predate v2 and have no active
  maintainers).

Scheme C path (per ε charter §2):

1. ε-1a (charter) ✓ landed at ``0765b3e0``.
2. **ε-1b** (this audit) ← current commit.
3. ε-2a -- retire v1 router + 2 dev scripts (R-ε-1 retires).
4. ε-2b -- atomic ``git rm -r src/openakita/orgs/`` (mission
   exit criteria 1 + 2 GREEN; R-ε-3 + R-ε-4 verified GREEN
   post-commit).

Scheme C is the **safer default** even if a future operator
folds ε-2a + ε-2b back into one commit (GREEN-fallback per
charter §2): the split preserves the OpenAPI snapshot
regenerate as a separate auditable event and keeps the ε-2b
deletion commit deletion-only.

Strict additive on v1 src after this audit: ``git diff
a3a5fde6..HEAD -- src/openakita/orgs/`` expected to remain
**empty** (this commit ships docs-only, like ε-1a).

**HARD STOP** per brief: ε-2a NOT started this commit; awaits
explicit operator signal after this audit closes with the
YELLOW (scheme C) verdict confirmed.
