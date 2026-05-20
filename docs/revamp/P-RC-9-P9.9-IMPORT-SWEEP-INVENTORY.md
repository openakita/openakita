# P-RC-9 P9.9α-1 Import-Sweep Inventory (HEAD ``1071a8b0``)

**Status: docs-only inventory.** Per-file:line catalogue of
every ``from openakita.orgs.X`` / ``import openakita.orgs.X``
import remaining at HEAD ``1071a8b0`` (branch
``revamp/v3-orgs``; P9.9 charter ``d49388bb`` + ``α-1``
opener), mapped to its v2 ``openakita.runtime.orgs.X`` (or
absorbed) target, the P9.9 sweep phase (β / γ / δ / ε / "self-
delete"), and per-file notes. Mirrors P9.7γ ``ENDPOINT-
INVENTORY`` + P9.8α ``CALLER-INVENTORY`` format.

This document is the **scope authority** for β-1 / γ-1..2 /
δ-2..3 / δ-4 atomic deletion / ε-1; charter §5 phase
ordering applies (β before ε; tests/orgs/ delete in δ-4
before ε; ADR-0015 NO-OP for 308 shim ζ).

Strict-additive backend boundary: ``git diff
1071a8b0..HEAD -- src/openakita/ tests/ apps/`` returns
empty bytes (this commit + every commit before β-1).

## 0. Measurement summary

All counts MEASURED via strict regex
``(?<![\w.])openakita\.orgs(?=[.\s])`` on lines starting
``from`` or ``import`` (the negative look-behind plus
trailing ``[.\s]`` guard rejects every
``openakita.runtime.orgs.X`` substring -- see §6 false-
positive forensics).

Recon scripts (kept under ``tmp_p10/p99/_recon.py`` and
``tmp_p10/p99/_audit.py``; not committed) reproduce these
numbers byte-for-byte against HEAD ``1071a8b0``.

### 0.1 Aggregate site counts (STRICT)

| tree | files | sites | external? | resolution |
|---|--:|--:|---|---|
| ``src/openakita/orgs/`` | 6 | 49 | NO (internal) | deleted with parent in **ε-1** |
| ``src/openakita/api/`` | 4 | 31 | mixed | 3 swept in **γ-1**, 1 (``orgs.py``) deleted in **ε-1** |
| ``src/openakita/channels/`` | 1 | 5 | YES | swept in **β-1** (R3 invariant) |
| ``src/openakita/core/`` | 1 | 1 | YES | swept in **γ-2** |
| ``src/openakita/runtime/`` | 1 | 1 | YES (special) | swept in **γ-2**; v2 manager importing v1 models |
| **src total** | **13** | **87** | -- | -- |
| ``tests/orgs/`` | 47 | 195 | NO (internal) | deleted with parent in **δ-4** |
| ``tests/parity/orgs/`` | 5 | 11 | YES (oracle) | swept in **δ-2** with Option B transition (§5) |
| ``tests/unit/`` | 8 | 14 | YES | swept in **δ-2** |
| ``tests/e2e/`` | 1 | 2 | YES | swept in **δ-3** |
| ``tests/integration/`` | 1 | 5 | YES | swept in **δ-3** |
| **tests total** | **62** | **227** | -- | -- |

**External-sweep file counts** (REAL, after stripping
internal trees + self-deleting v1 router):

* **β-1**: 1 src file / 5 sites (``channels/gateway.py``)
* **γ-1..2**: 5 src files / 9 sites (3 api + 1 core + 1
  runtime; ``api/routes/orgs.py`` excluded as it deletes in
  ε-1 with parent v1 subsystem)
* **δ-2..3**: 15 test files / 32 sites (5 parity/orgs +
  8 unit + 1 e2e + 1 integration)
* **δ-4**: 47 test files / 195 sites (atomic
  ``git rm -r tests/orgs/``)
* **ε-1**: 6 src files / 49 sites + ``api/routes/orgs.py``
  internal imports vanish with parents

**Charter §3.1 vs STRICT**: charter projected 23 src + 17
tests = 40 external; REAL is 7 + 15 = **22**. ~18-file
delta dominated by ``runtime/`` (14 -> 1; §6 FP forensics).

## 1. External callers -- backend src (7 files / 38 sites)

Every row STRICT-grepped at HEAD ``1071a8b0``. ``api/
routes/orgs.py`` is listed for completeness (24 internal
sites that vanish in ε-1) but does **not** count toward γ
scope -- the file itself is ``git rm``-ed in the same
commit that deletes ``src/openakita/orgs/``.

### 1.1 ``src/openakita/api/`` (4 files / 31 sites)

| # | file | line | v1 import | v2 target | phase | notes |
|--:|---|--:|---|---|---|---|
| 1 | ``api/routes/chat.py`` | 1511 | ``from openakita.orgs.command_service import (OrgCommandError, OrgCommandRequest, OrgCommandSource, OrgCommandSurface, default_scope_for_surface)`` | ``from openakita.runtime.orgs.command_service import (...)`` | **γ-1** | deferred import inside SSE handler; 1-to-1 rewrite |
| 2 | ``api/routes/orgs.py`` | 22 | ``from openakita.orgs.command_service import (...)`` | self-delete | **ε-1** | parent file ``git rm``-ed |
| 3 | ``api/routes/orgs.py`` | 103 | ``from openakita.orgs.models import OrgStatus`` | self-delete | **ε-1** | -- |
| 4 | ``api/routes/orgs.py`` | 163 | ``from openakita.orgs.manager import OrgNameConflictError`` | self-delete | **ε-1** | -- |
| 5 | ``api/routes/orgs.py`` | 174 | ``from openakita.orgs.tool_categories import list_avatar_presets`` | self-delete | **ε-1** | absorbed module |
| 6 | ``api/routes/orgs.py`` | 234 | ``from openakita.orgs.plugin_workbench_templates import build_workbench_templates`` | self-delete | **ε-1** | -> ``_runtime_plugin_assets`` if revived |
| 7-18 | ``api/routes/orgs.py`` | 257..2279 (17 sites) | mix of ``manager.OrgNameConflictError`` (x4), ``models.{NodeSchedule, NodeStatus, MemoryScope/Type, InboxPriority, OrgProject, ProjectStatus, ProjectType, ProjectTask, TaskStatus}``, ``blackboard.OrgBlackboard`` (x2), ``project_store.ProjectStore``, ``tool_categories.expand_tool_categories``, ``command_service.get_command_service`` (full enumeration: ``tmp_p10/p99/_recon.txt`` § ``src/openakita/api/``) | self-delete | **ε-1** | all parent-file ``git rm``-ed; per-line catalogue preserved in recon txt; no γ rewrite required |
| 19 | ``api/routes/orgs_v2_runtime_orgs.py`` | 99 | ``from openakita.orgs.tool_categories import list_avatar_presets`` | -> absorbed (revive as ``runtime/orgs/_runtime_plugin_assets`` helper OR re-implement inline) | **γ-1** | v2 router still leaning on v1 helper; see §3 |
| 20 | ``api/routes/orgs_v2_runtime_orgs.py`` | 134 | ``from openakita.orgs.plugin_workbench_templates import build_workbench_templates`` | ``from openakita.runtime.orgs._runtime_plugin_assets import build_workbench_templates`` | **γ-1** | per §3 absorption map |
| 21 | ``api/server.py`` | 363 | ``from openakita.orgs.manager import OrgManager`` | ``from openakita.runtime.orgs.manager import OrgManager`` | **γ-1** | 1-to-1 |
| 22 | ``api/server.py`` | 364 | ``from openakita.orgs.runtime import OrgRuntime`` | ``from openakita.runtime.orgs.runtime import OrgRuntime`` | **γ-1** | 1-to-1 |
| 23 | ``api/server.py`` | 365 | ``from openakita.orgs.templates import ensure_builtin_templates`` | ``from openakita.runtime.orgs._runtime_plugin_assets import ensure_builtin_templates`` | **γ-1** | absorbed per §3 |
| 24 | ``api/server.py`` | 372 | ``from openakita.orgs.command_service import OrgCommandService, set_command_service`` | ``from openakita.runtime.orgs.command_service import (...)`` | **γ-1** | 1-to-1 |

**γ-1 net edits in api/**: 3 active files
(``chat.py``, ``orgs_v2_runtime_orgs.py``, ``server.py``)
/ 7 sites. ``orgs.py`` (24 sites) excluded -- parent file
deletes in ε-1.

### 1.2 ``src/openakita/channels/`` (1 file / 5 sites; β-1)

| # | file | line | v1 import | v2 target | phase | notes |
|--:|---|--:|---|---|---|---|
| 25 | ``channels/gateway.py`` | 3182 | ``from openakita.orgs.command_service import get_command_service`` | ``from openakita.runtime.orgs.command_service import get_command_service`` | **β-1** | 1-to-1; deferred import in IM handler |
| 26 | ``channels/gateway.py`` | 3256 | same | same | **β-1** | -- |
| 27 | ``channels/gateway.py`` | 3335 | same | same | **β-1** | -- |
| 28 | ``channels/gateway.py`` | 3449 | same | same | **β-1** | -- |
| 29 | ``channels/gateway.py`` | 3739 | ``from openakita.orgs.command_service import (OrgCommandError, OrgCommandRequest, OrgCommandSource, OrgCommandSurface, default_scope_for_surface)`` | ``from openakita.runtime.orgs.command_service import (...)`` | **β-1** | 5-name multi-line; same surface used in chat.py:1511 |

**β-1 net edits**: 1 file / 5 sites; ~50 LOC insertions
(symbol-for-symbol substitution).

### 1.3 ``src/openakita/core/`` (1 file / 1 site; γ-2)

| # | file | line | v1 import | v2 target | phase | notes |
|--:|---|--:|---|---|---|---|
| 30 | ``core/_reasoning_engine_legacy.py`` | 7920 | ``from openakita.orgs.runtime import get_runtime  # 延迟导入避免环路`` | ``from openakita.runtime.orgs.runtime import get_runtime`` | **γ-2** | deferred import preserving cycle-break comment |

### 1.4 ``src/openakita/runtime/`` (1 file / 1 site; γ-2 special)

| # | file | line | v1 import | v2 target | phase | notes |
|--:|---|--:|---|---|---|---|
| 31 | ``runtime/orgs/manager.py`` | 55 | ``from openakita.orgs.models import (NodeSchedule, Organization, OrgEdge, OrgNode, OrgStatus, ScheduleType, UserPersona, _new_id, _now_iso, infer_agent_profile_id_for_node)`` | split into 4 typed shards (see §3) | **γ-2** | v2 manager still leaning on v1 ``models``; the 10 symbols re-route per-symbol to ``command_models`` / ``memory_models`` / ``project_models`` / ``scheduler_models`` |

**γ-2 net edits**: 2 files / 2 sites (1 cross-tree
deferred import + 1 v2-internal symbol-split).

### 1.5 ``src/openakita/orgs/`` (6 files / 49 sites; ε-1)

Internal imports inside the v1 subsystem
(``command_service.py``, ``identity.py``,
``project_store.py``, ``runtime.py``, ``templates.py``,
``tool_handler.py``). All vanish atomically when
``git rm -r src/openakita/orgs/`` lands in ε-1. **NOT γ
scope** -- no sweep edit required.

## 2. External callers -- tests (15 files / 32 sites)

### 2.1 ``tests/e2e/`` (1 file / 2 sites; δ-3)

| # | file | line | v1 import | v2 target | phase | notes |
|--:|---|--:|---|---|---|---|
| 1 | ``tests/e2e/test_p0_regression.py`` | 180 | ``from openakita.orgs.command_service import OrgCommandService`` | ``from openakita.runtime.orgs.command_service import OrgCommandService`` | **δ-3** | 1-to-1 |
| 2 | ``tests/e2e/test_p0_regression.py`` | 241 | ``from openakita.orgs.tool_handler import OrgToolHandler`` | absorbed: ``from openakita.runtime.orgs._runtime_agent_pipeline import OrgToolHandler`` (or re-export shim) -- §3 | **δ-3** | per-symbol mapping required; see §3 |

### 2.2 ``tests/integration/`` (1 file / 5 sites; δ-3)

| # | file | line | v1 import | v2 target | phase | notes |
|--:|---|--:|---|---|---|---|
| 3 | ``tests/integration/test_gateway_org_control.py`` | 113, 139, 147, 174, 198 | ``from openakita.orgs import command_service as cs_module`` (x5) | ``from openakita.runtime.orgs import command_service as cs_module`` | **δ-3** | 5 deferred imports in IM-canary fixtures; 1-to-1 |

### 2.3 ``tests/parity/orgs/`` (5 files / 11 sites; δ-2 ORACLE)

These import v1 as **parity oracle** (compare v2 output
against v1 output on identical inputs). After v1 deletion,
each file's transition strategy per §5 (recommend Option B
for all 5: convert to v2-only smoke, drop v1 import,
preserve regression baseline).

| # | file | line | v1 import | δ-2 action | notes |
|--:|---|--:|---|---|---|
| 4 | ``parity/orgs/test_blackboard_parity.py`` | 30 | ``from openakita.orgs.blackboard import OrgBlackboard as V1Blackboard`` | drop import + rewrite 8 cases to v2-only smoke | Option B; sentinel #1 |
| 5 | ``parity/orgs/test_blackboard_parity.py`` | 31 | ``from openakita.orgs.models import MemoryScope, MemoryType`` | swap to ``from openakita.runtime.orgs.memory_models import MemoryScope, MemoryType`` | -- |
| 6 | ``parity/orgs/test_command_service_parity.py`` | 44 | ``from openakita.orgs.command_service import (...)`` | drop import + rewrite 10 cases to v2-only smoke | Option B; sentinel #4 |
| 7 | ``parity/orgs/test_manager_parity.py`` | 82 | ``from openakita.orgs.manager import OrgManager`` | drop import + rewrite 12 cases to v2-only smoke | Option B; sentinel #5 |
| 8 | ``parity/orgs/test_node_scheduler_parity.py`` | 81 | ``from openakita.orgs.models import NodeSchedule as V1NS`` | swap to ``from openakita.runtime.orgs.scheduler_models import NodeSchedule as V1NS`` (rename alias to ``NS``) | shared shard import |
| 9 | ``parity/orgs/test_node_scheduler_parity.py`` | 82 | ``from openakita.orgs.models import ScheduleType as V1ST`` | swap to ``from openakita.runtime.orgs.scheduler_models import ScheduleType as ST`` | -- |
| 10 | ``parity/orgs/test_node_scheduler_parity.py`` | 140-142 | repeat of 81-82 + ``from openakita.orgs.node_scheduler import OrgNodeScheduler as V1Sched`` | drop V1Sched import + collapse parity to v2-only smoke | Option B; sentinel #3 |
| 11 | ``parity/orgs/test_project_store_parity.py`` | 102 | ``from openakita.orgs.models import OrgProject, ProjectTask, TaskStatus`` | swap to ``from openakita.runtime.orgs.project_models import (...)`` | -- |
| 12 | ``parity/orgs/test_project_store_parity.py`` | 103 | ``from openakita.orgs.project_store import ProjectStore`` | drop import + rewrite 8 cases to v2-only smoke | Option B; sentinel #2 |

**Note**: ``tests/parity/orgs/test_runtime_parity.py`` (P9.6γ
sentinel #6; 20 cases) does **NOT** import v1 -- it pins v2
against golden dicts per ADR-0014 (v1 6 355 LOC monolith too
coupled to import; charter §1.2 sentinel survey). Sentinel
#6 survives ε-1 unchanged.

### 2.4 ``tests/unit/`` (8 files / 14 sites; δ-2)

| # | file | line | v1 import | v2 target | phase | notes |
|--:|---|--:|---|---|---|---|
| 13 | ``unit/test_c17_second_pass_audit.py`` | 222, 267 | ``from openakita.orgs.event_store import OrgEventStore`` (x2) | absorbed: ``from openakita.runtime.orgs._runtime_event_bus import OrgEventStore`` (per §3) | **δ-2** | per §3 absorption |
| 14 | ``unit/test_delegation_preamble.py`` | 138 | ``from openakita.orgs.identity import OrgIdentity`` | absorbed: ``from openakita.runtime.orgs.manager import OrgIdentity`` (per §3) | **δ-2** | identity folded into OrgManager (P9.5c) |
| 15 | ``unit/test_delegation_preamble.py`` | 139 | ``from openakita.orgs.models import Organization, OrgNode`` | per-symbol shard split (§3) | **δ-2** | -- |
| 16 | ``unit/test_failure_diagnoser_tone.py`` | 14 | ``from openakita.orgs.failure_diagnoser import (...)`` | ``from openakita.runtime.orgs._runtime_watchdog import (...)`` | **δ-2** | per §3 |
| 17 | ``unit/test_org_delegation_validator.py`` | 11 | ``from openakita.orgs.failure_diagnoser import summarize`` | ``from openakita.runtime.orgs._runtime_watchdog import summarize`` | **δ-2** | per §3 |
| 18 | ``unit/test_org_runtime_root_chain_dedup.py`` | 20 | ``from openakita.orgs.models import OrgNode`` | per-symbol shard split (§3) | **δ-2** | -- |
| 19 | ``unit/test_org_runtime_root_chain_dedup.py`` | 21 | ``from openakita.orgs.runtime import OrgRuntime`` | ``from openakita.runtime.orgs.runtime import OrgRuntime`` | **δ-2** | 1-to-1 |
| 20 | ``unit/test_org_setup_tool.py`` | 22 | ``from openakita.orgs.models import Organization`` | per-symbol shard split (§3) | **δ-2** | -- |
| 21 | ``unit/test_org_setup_tool.py`` | 343, 396 | ``from openakita.orgs.manager import OrgManager`` (x2) | ``from openakita.runtime.orgs.manager import OrgManager`` | **δ-2** | 1-to-1 |
| 22 | ``unit/test_remaining_qa_fixes.py`` | 4 | ``from openakita.orgs.models import OrgNode`` | per-symbol shard split (§3) | **δ-2** | -- |
| 23 | ``unit/test_remaining_qa_fixes.py`` | 5 | ``from openakita.orgs.runtime import OrgRuntime`` | 1-to-1 v2 | **δ-2** | -- |
| 24 | ``unit/test_web_search_provider_panel.py`` | 414 | ``from openakita.orgs.runtime import OrgRuntime`` | 1-to-1 v2 | **δ-2** | deferred import |

### 2.5 ``tests/orgs/`` (47 files / 195 sites; δ-4)

Internal v1-surface test bank. All 195 imports vanish
atomically when ``git rm -r tests/orgs/`` lands in δ-4
(after δ-1 cross-coverage audit per charter §2.2). **NOT
δ-2/3 scope** -- no sweep edit required. The 47 files +
their ``__init__.py`` + ``conftest.py`` are listed
collectively in the δ-4 commit; per-file table is
unnecessary at α-1 (covered by δ-1 audit doc).

**Note vs charter §3.2**: charter said tests/api/ has 1 v1
import. STRICT scan reports **0** ``tests/api/`` files with
v1 imports -- the 1 file was a loose-grep false positive on
``openakita.runtime.orgs``. δ-3 has no tests/api/ work.

## 3. v1 module -> v2 module mapping (absorbed / non-1:1)

From charter §3.3 + G-RC-9.6 §13 absorption panorama. Per
v1 module, the v2 destination(s). **1-to-1 same-name
mappings** (no per-symbol routing): ``blackboard``,
``command_service``, ``manager``, ``node_scheduler``,
``project_store``, ``runtime``, ``sqlite_store``,
``store`` (each maps verbatim to ``openakita.runtime.orgs.X``).

**Absorbed non-1-to-1 mappings** (8 v1 modules; per-symbol
sweep targets):

| v1 import | absorbed-to v2 import | absorbed in | callers using it (this inventory) |
|---|---|---|---|
| ``openakita.orgs.command_tracker`` | ``openakita.runtime.orgs._runtime_dispatch`` (+ ``_runtime_watchdog`` half) | P9.4c + P9.6γ | tests/orgs/* only (vanishes in δ-4) |
| ``openakita.orgs.failure_diagnoser`` | ``openakita.runtime.orgs._runtime_watchdog`` | P9.6γ | unit/test_failure_diagnoser_tone.py; unit/test_org_delegation_validator.py |
| ``openakita.orgs.heartbeat`` | ``openakita.runtime.orgs._runtime_watchdog`` + ``_runtime_event_bus`` | P9.6γ | tests/orgs/* only |
| ``openakita.orgs.event_router`` | ``openakita.runtime.orgs._runtime_event_bus`` | P9.4c | src/openakita/orgs/runtime.py:5221 (vanishes in ε-1) |
| ``openakita.orgs.messenger`` | ``openakita.runtime.orgs._runtime_event_bus`` + ``command_service`` | P9.4c | tests/orgs/* only |
| ``openakita.orgs.identity`` | ``openakita.runtime.orgs.manager`` (folded into OrgManager) | P9.5c | unit/test_delegation_preamble.py:138 |
| ``openakita.orgs.templates`` | ``openakita.runtime.orgs._runtime_plugin_assets`` | P9.2c | api/server.py:365 |
| ``openakita.orgs.plugin_workbench_templates`` | ``openakita.runtime.orgs._runtime_plugin_assets`` | P9.2c | api/orgs_v2_runtime_orgs.py:134 |
| ``openakita.orgs.tool_categories`` | absorbed inline (revive in ``_runtime_plugin_assets`` OR keep as standalone helper) | P9.2c | api/orgs_v2_runtime_orgs.py:99 |
| ``openakita.orgs.event_store`` | ``openakita.runtime.orgs._runtime_event_bus`` (OrgEventStore re-export) | P9.4c | unit/test_c17_second_pass_audit.py:222, 267 |
| ``openakita.orgs.tool_handler`` (3 474 LOC) | split: ``_runtime_agent_pipeline`` + ``runtime/dispatch`` + ``runtime/tools``; per-symbol | P9.4c + P9.6γ | e2e/test_p0_regression.py:241 (``OrgToolHandler``) |
| ``openakita.orgs.models`` (1 018 LOC) | split into 4 typed shards: ``command_models`` / ``memory_models`` / ``project_models`` / ``scheduler_models`` under ``runtime/orgs/`` | P9.1c-P9.4c | runtime/orgs/manager.py:55; tests/parity/orgs/*; tests/unit/* |

**Per-symbol map for ``openakita.orgs.models`` (10
symbols imported by runtime/orgs/manager.py:55)** -- needed
for γ-2 apply:

| symbol | v2 shard | rationale |
|---|---|---|
| ``NodeSchedule`` | ``scheduler_models`` | NodeScheduler subsystem |
| ``Organization``, ``OrgEdge``, ``OrgNode``, ``UserPersona`` | ``command_models`` (org graph types) | manager / command_service domain |
| ``OrgStatus`` | ``command_models`` | -- |
| ``ScheduleType`` | ``scheduler_models`` | -- |
| ``_new_id``, ``_now_iso`` | re-exported by every shard (utility helpers) | per P9.4c factoring; import from any shard |
| ``infer_agent_profile_id_for_node`` | ``command_models`` | profile-resolution helper |

γ-2 apply verifies the per-symbol home in
``runtime/orgs/{command,memory,project,scheduler}_models.py``
before rewriting.

## 4. Phase assignment summary + commit boundary proposal

### 4.1 Phase totals (REAL, after FP filter)

| phase | files | sites | charter estimate | delta vs charter |
|---|--:|--:|--:|---|
| α-1 (this doc) | 1 doc | -- | 1 doc | -- |
| β-1 (channels) | 1 | 5 | 1 | match |
| γ-1..2 (src sweep) | 5 | 9 | 23 ext src (loose) | **-18 files** (FP filter; §6) |
| δ-1 (coverage audit) | 1 doc | -- | 1 doc | -- |
| δ-2..3 (test sweep) | 15 | 32 | 17 ext tests | **-2 files** (parity 6->5 + api 1->0) |
| δ-4 (tests/orgs/ rm) | 47 | 195 | 48 (incl. ``conftest``) | -1 (rounding; conftest counted) |
| ε-1 (atomic delete) | 26 src + 1 router | -- | 26+1 | match |

### 4.2 γ commit boundary proposal (2 commits, NOT 3)

Charter §5.3 projected 3 γ commits (api / runtime / core)
on the loose 7+14+2 estimate. With FP filter the runtime
γ-2 reduces to 1 file; the 3-commit cadence is over-
engineered. Proposal:

| commit | scope | files | sites | est ins |
|---|---|--:|--:|--:|
| **γ-1** | ``src/openakita/api/`` swap | 3 (chat / orgs_v2_runtime_orgs / server) | 7 | ~50 LOC |
| **γ-2** | cross-tree swap: ``core/_reasoning_engine_legacy.py`` (1 deferred) + ``runtime/orgs/manager.py`` (1 multi-line per-symbol split) + R4 ``__init__`` confirm-only audit (§7) | 2 | 2 | ~40 LOC |

Each γ commit: re-run STRICT grep + parity/orgs + sentinels
#7+#8 green; inventory grep monotonically shrinks. **γ-3
NOT scheduled** -- contingency reserved for ε-1 split
fallback (charter §5.5).

### 4.3 δ commit boundary proposal (4 commits as charter)

| commit | scope | files | sites | est ins / del |
|---|---|--:|--:|---|
| **δ-1** | ``tests/runtime/orgs/coverage_audit.md`` (cross-coverage doc per charter §2.2) | 1 doc | -- | +150 / 0 |
| **δ-2** | ``tests/parity/orgs/`` (5; Option B v2-only smoke per §5) + ``tests/unit/`` (8) sweep | 13 | 25 | +100 / ~30 |
| **δ-3** | ``tests/e2e/`` (1) + ``tests/integration/`` (1) sweep; **no tests/api/ touch** (0 hits) | 2 | 7 | +30 / ~10 |
| **δ-4** | atomic ``git rm -r tests/orgs/`` (47 + ``__init__`` + ``conftest``) + sentinel-#7/#8 confirm + ledger | 47+ | -- | +20 ledger / **-12 238** |

## 5. ``tests/parity/orgs/`` transition strategy (per file)

The 5 parity files import v1 as ORACLE (v2 vs v1 dict
equality on identical inputs, 48 / 48 currently green).
After v1 deletion, recommend **Option B** for all 5:
*convert to v2-only smoke*: drop the v1 import, keep the
fixture inputs + assertion harness, replace ``v1.do(X) ==
v2.do(X)`` with ``v2.do(X) == EXPECTED`` (or
``v2.do(X)`` smoke-runs without raising). This preserves
the regression-net baseline; v2 contract tests under
``tests/runtime/orgs/`` already pin per-Protocol behaviour
(28 files / 7 348 LOC; charter §2.2).

**Option A** (delete entirely): rejected -- loses 48
golden-dict cases that v2-only contract tests do not
duplicate (parity tests pin v1's exact output dict shape;
contracts pin v2 Protocol behaviour). **Option C** (JSON
snapshot v1 outputs before deletion, replay against v2):
rejected -- adds maintenance burden; if v2 evolves, the
JSON fixtures need updating anyway, and the contract tests
already serve as forward-looking baseline.

**Per-file recommendation** (all Option B; rewrite lands
in δ-2):

| # | file | sentinel | v1 oracle uses | Option B action |
|--:|---|--:|---|---|
| 1 | ``test_blackboard_parity.py`` | #1 (8/8) | V1Blackboard + MemoryScope/Type | drop V1Blackboard import; replace 8 ``v1==v2`` asserts with v2-only behaviour asserts; keep MemoryScope/Type (swap to shard) |
| 2 | ``test_command_service_parity.py`` | #4 (10/10) | OrgCommandRequest/Source/Surface + OrgCommandError | drop v1 module; replace 10 ``v1.submit(req)==v2.submit(req)`` with ``v2.submit(req) == EXPECTED`` golden-dict harness |
| 3 | ``test_manager_parity.py`` | #5 (12/12) | OrgManager | drop V1 OrgManager; rewrite 12 cases as v2 OrgManager behavioural smoke; v2 already has matching API surface |
| 4 | ``test_node_scheduler_parity.py`` | #3 (10/10) | OrgNodeScheduler + NodeSchedule + ScheduleType | drop V1Sched; swap NodeSchedule/ScheduleType to ``scheduler_models``; convert 10 cases to v2-only |
| 5 | ``test_project_store_parity.py`` | #2 (8/8) | ProjectStore + OrgProject/ProjectTask/TaskStatus | drop V1 ProjectStore; swap models to ``project_models``; convert 8 cases to v2-only |

**Sentinel impact**: post-Option-B the parity sentinels
become *v2-baseline sentinels* (assert v2 produces
EXPECTED golden dicts). Sentinel count stays 9 / 9 ACTIVE
through G-RC-9.9 -- only the semantics shift from
"oracle-equality" to "v2-baseline" (same shape as
sentinel #6 test_runtime_parity.py, already v2-only).
**Charter §7.1 sentinels #1-#6 status updated**: per-file
oracle drop is in-scope of δ-2 (no separate ADR).

## 6. ``runtime=14`` false-positive forensics

Charter §3.1 reported 14 ``src/openakita/runtime/`` files
matching v1 imports. STRICT regex shows **1** real v1
import in the entire runtime/ tree
(``runtime/orgs/manager.py:55``). The 13-file delta is
substring artifact.

**Why the loose grep over-counted**: loose grep
``openakita\.orgs`` matches the v2 dotted path
``openakita.runtime.orgs.X`` (the substring ``openakita.
orgs`` literally appears inside ``openakita.runtime.orgs``
starting at offset 9). With ~280 ``from openakita.runtime.
orgs.X`` imports across the v2 subsystem, every v2 file
trips the loose grep.

**Strict regex fix**:
``(?<![\w.])openakita\.orgs(?=[.\s])`` -- the negative
look-behind ``(?<![\w.])`` rejects ``.orgs`` preceded by
``runtime.``; the look-ahead ``(?=[.\s])`` rejects bare
substring matches (requires a real dotted continuation
``.X`` or trailing whitespace / EOL).

**Reproducible audit** (kept in
``tmp_p10/p99/_recon.py``):

```python
import re
STRICT_V1 = re.compile(r"(?<![\w.])openakita\.orgs(?=[.\s])")
# applied only to lines matching ^\s*(from|import)\s+
```

**Filtered runtime/ tree (loose-only, NOT strict v1)**: 3
files match loose ``openakita.orgs`` substring but **not**
strict v1 -- all three contain only v2 docstring /
narrative mentions of the v1 surface they replaced:

* ``runtime/orgs/_runtime_agent_pipeline.py``
* ``runtime/orgs/_runtime_node_lifecycle.py``
* ``runtime/orgs/_runtime_plugin_assets.py``

These are FALSE positives even at the loose-grep level
(narrative, not import). The remaining 10 files the
loose grep counted are pure ``openakita.runtime.orgs.X``
import lines (v2 internal cross-refs).

**Net γ scope correction**: charter projected 14
``src/openakita/runtime/`` γ-2 edits; real count is **1**
edit (``runtime/orgs/manager.py``). γ-2 reduces from
~100 LOC est to ~40 LOC; combined γ-1+γ-2 ~90 LOC vs
charter ~160 LOC.

## 7. R4 ``__init__.py`` re-export audit

Charter §R4 flagged risk of top-level / package
``__init__.py`` re-exports for backward-compat
(``from openakita.orgs.X import Y``). STRICT grep across
all ``__init__.py`` files under ``src/openakita/`` and
``tests/`` at HEAD ``1071a8b0``:

**Result: ZERO ``__init__.py`` files re-export from
``openakita.orgs.*``.** R4 risk **CLEAR**. No package
``__init__`` requires rewrite-or-delete decision in
γ / δ. Confirm-only audit folded into γ-2 commit
(post-sweep re-grep asserts zero).

## 8. P9.9α-1 post-write verification

After this doc lands, re-run STRICT grep to confirm site
counts above are unchanged:

```bash
python tmp_p10/p99/_recon.py
# Expected: src strict hits 87 (13 files), tests strict
# hits 227 (62 files); external = 7 src / 15 tests.
```

If counts drift between this commit's HEAD and α-1
landing, the inventory is patched in a follow-up
α-1-nit commit BEFORE β-1 opens. No drift observed at
write time (HEAD ``1071a8b0``; recon script runs in
<2 s).

## 9. Cross-references + HARD STOP

* Charter ``docs/revamp/P-RC-9-P9.9-CHARTER.md`` §3
  (import matrix) + §5 (phase breakdown).
* Gate ``docs/revamp/gates/G-RC-9.6.md`` §13 (subsystem
  panorama; absorbed-not-1-to-1 mapping inputs).
* Gate ``docs/revamp/gates/G-RC-9.8.md`` §12 (channels
  gateway 5 in-process imports).
* ADR-0011 (subsystem decomposition; no new Protocol).
* ADR-0012 (v1 deletion at P9.9 per Q-B ACCEPTED (b)).
* ADR-0015 (308 shim governance; ζ NO-OP).

**HARD STOP**: α-1 docs-only round. **β-1 NOT started.**
Next operator signal opens β-1 (gateway.py 5-site swap per
§1.2; R3 invariant before ε-1). ``git diff 1071a8b0..HEAD
-- src/openakita/ tests/ apps/`` returns empty bytes.

**P-RC-9 status**: P9.0..P9.8 closed (8 sentinels ACTIVE);
P9.9 charter + ADR-0015 LANDED; **α-1 inventory LANDED**;
β-1 / γ-1..2 / δ-1..4 / ε-1 / η-1..2 unscheduled.
