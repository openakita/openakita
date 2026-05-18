# OpenAkita Backend Revamp — Status

This document is the authoritative ledger of what the v2 fork-style
rewrite has shipped, what is in flight, and what every next session
should pick up. It complements the plan file
(`openakita_full_backend_revamp_e6d8610d.plan.md`) and the ADRs under
`docs/adr/`.

Branch: `revamp/v2` (forked from `main`).
ADR sign-off gate: **G0 pending — every ADR is `Status: Proposed`.**
A user-led ADR review is the gate to flip them all to `Accepted`.

## Scoreboard

| Phase | Status | Code commits on `revamp/v2` | Tests passing |
|---|---|---|---|
| 0 — ADRs (10 docs) | **Complete** | 10 | n/a |
| 1 — Foundation (runtime/ leaf modules) | **Complete** | 8 | 99 runtime tests |
| 2 — Agent rewrite | **Complete (MOVE 6–12 + REWRITE 14–18 + parity 13/19)** | 19 sub-commits per `core_audit.md` plan | 721 tests green (incl. 30 parity cases @ 100%) |
| 3 — Runtime engine (supervisor + messenger + guardrail + state graph) | **Complete (G3 review pending)** | 6 (`ledger`, `stall_detector`, `supervisor`, `messenger`, `guardrail/`, `state_graph`) | 110 new runtime tests |
| 4 — Nodes | **Complete incl. plugin loader (G4 review pending)** | 7 (`base`+sig fix, `tool_node`, `llm_node`, `condition_node`+`human_review_node`, `workbench_node`+`manifest`, `happyhorse-video` adoption, `plugins/manager.py` WORKBENCH discovery) | 89 new tests (`test_nodes_*`) + 5 plugin smoke tests + 5 manifest discovery tests |
| 5 — Templates | **Schema + registry + 4 builtins shipped (G5 review pending)** | 7 (`schema`, `registry`, `aigc_video_studio`, `software_team`, `startup_company`, `content_ops`+discovery test, parent_id-from-HIERARCHY fix) | 88 template tests (incl. 5 new parent-id regression tests) |
| 6 — API / channels swap | **Complete (G6 signed)** | 5 (orgs CRUD + JSON store + channel_routing helper + canary gateway hook + frontend client/drawer) | 25 api + 9 store + 7 routing |
| 7 — Cutover + data migration | **Complete (G7 signed; burn-in is operator-side)** | 1 (migration script + flag flip + runbook) | 8 migration tests |
| 8 — Legacy removal | **RC scope shipped; full removal staged post-RC (G8 signed)** | 2 (state.py delete + release notes + G8 note) | — |
| P-RC-0 — Truth alignment & drift guardrails | **Complete (G-RC-0 signed)** | 6 (config doc + rollback.md + smoke artifacts gitignore + LOC audit + PROGRESS_LEDGER + parity facade detector + G-RC-0) | 763 / 763 + 1 skipped (incl. 30 parity baselines + 6 new no-facade cases + 2 LOC invariants) |
| P-RC-1 — IM canary live | **Complete (G-RC-1 signed)** | 9 (P1.0a/P1.0b N1+N2 fix + session_bridge + async dispatch + im_stream_bridge + canary gateway hook + cancel verb wiring + canary settings + e2e gate test + G-RC-1) | 796 / 796 + 1 skipped (incl. +5 no-facade phase-expiry, +2 ledger parser, +8 session_bridge, +6 dispatch, +7 stream bridge, +5 canary config) |
| P-RC-2 — Frontend v2 live + drain-on-close + cold-session | **Complete (G-RC-2 signed)** | 10 (P2.0 ledger bump + N3/N4/N5 doc + StreamBus drain-on-close + cold-session lookup + SSE route + v2Stream client + ProgressLedgerTimeline + OrgChatPanel v2 + TemplatePickerDrawer mount + build-id banner + G-RC-2) | 814 / 814 + 1 skipped (incl. +4 stream drain, +6 cold session, +5 SSE, +5 vitest v2Stream, +4 timeline, +2 panel, +1 drawer, +2 banner, +3 build-info) |
| P-RC-3 — Multi-process v2 persistence + nit cleanup (T1–T5) | **Complete (G-RC-3 signed)** | 9 (P3.0 commit_guard+ledger bump + G-RC-2 wording fix + StreamBus closed-gate + StreamRegistry idle cleanup + SqliteOrgStore + contract suite + pluggable backend + JSON→SQLite migrate + G-RC-3) | 874 / 874 + 1 skipped (incl. +11 commit_guard, +5 stream closed-gate, +6 stream_registry idle, +9 sqlite_store, +18 cross-backend contract, +6 orgs_v2_backend config, +5 migrate) |
| P-RC-4 — Brain/Tools/Context real slim-down | **Complete (G-RC-4 signed)** | 24 (P4.0 ledger bump + 4 runtime/llm helpers + agent.brain real impl + core.brain shim + Brain parity + 2 runtime/io helpers + tool retry policy + agent.tools real impl + core.tool_executor shim + Tools parity + 3 runtime/context helpers + agent.context real impl + core.context_manager shim + Context parity + G-RC-4) | 955 / 955 + 1 skipped (incl. +18 runtime/io tests, +10 retry-tool tests, +16 runtime/context tests, +7 brain parity, +7 tools parity, +7 context parity, +11 brain runtime/llm helper tests, etc.) |

Total to date: **80+ code/docs commits on `revamp/v2`**, all
lint-clean (ruff over the v2 surface), test-green (755 / 755 + 1
skipped across `tests/runtime/`, `tests/agent/`, `tests/api/`,
`tests/unit/test_plugins/`, `tests/parity/`). Parity harness
maintains 30 / 30 cases at 100 % pass rate.

**v2.0.0-rc1 is tagged locally.** Phase 0 → 7 fully delivered; Phase
8 RC scope (release notes + safe single deletion +
G8 paperwork + tag) shipped. The mechanical removal of legacy
``orgs/`` and the ``core/`` shims is the post-RC engineering cycle,
gated by the burn-in described in ``docs/revamp/burn_in.md``.

### 2026-05-18 mid-cycle plan-vs-reality review

`docs/revamp/PLAN_AUDIT.md` records a full audit of what shipped vs
what the plan called for, and the recovery order. Two of the four
P0 items the audit identified were closed in this cycle:

* `plugins/manager.py` now discovers the v2 ``WORKBENCH`` manifest at
  load time (the loader-side half of ADR-0009 was previously unbuilt).
* `runtime/state_graph.py` lands as the Pregel-style routing engine
  ``ConditionNode.next_address`` was always meant to feed.

Both unblock G3/G4 sign-off. The remaining caveats are paperwork
(gate review notes) and the Phase 2 giant rewrite, which is now
fully scoped by `docs/revamp/core_audit.md` (the audit the plan §8
mandated at Phase 2 entry; previously missing).

## What v2 already delivers

The dual-ledger orchestration that ADR-0004 promises is end-to-end
working at module level:

```
TaskLedger (outer)               StallDetector
        │                                │
        ▼                                ▼
 Supervisor.run() ─── per turn ──► ProgressLedger ──► verdict ──► (DONE | PROCEED |
        │                                                          SUSPECT | REPLAN |
        │                                                          OUT_OF_TURNS)
        ├── stream events:    progress_ledger / checkpoints / lifecycle / tasks / updates
        ├── checkpoints:      after every turn, on accepted deliverable, on cancel
        ├── cancel:           cooperative via CancellationToken, writes a final ckpt
        └── delegate:         Messenger.deliver(speaker, instruction, ...) ──► node
                                                                        │
                                                                        ▼
                                                              GuardrailRunner.evaluate()
                                                              (OK | RETRY | HARD_FAIL)
```

The duplicate-storyboard regression (the headline pain in the user's
original report) cannot reproduce in v2 because:

1. wall-clock cancels are no longer in the loop (`max_task_seconds`
   has no v2 equivalent in the supervisor's decision path);
2. when a long step is *progressing*, the LLM says
   `is_progress_being_made=true` and the stall counter regenerates;
3. when a step actually stalls, the supervisor *replans* with new
   facts and a new plan — it does not cancel and re-delegate the same
   sub-task to the same node;
4. cancellations save a final checkpoint so resume is exact.

`tests/runtime/test_stall_detector.py::test_regression_long_progressing_storyboard_does_not_replan`
encodes the regression test for this.

## File map (so far)

```
docs/adr/
  README.md
  0001-fork-style-rewrite.md          ADR-0001 (signed off at G0)
  0002-runtime-architecture.md
  0003-agent-architecture.md
  0004-dual-ledger-supervisor.md
  0005-checkpoint-contract.md
  0006-stream-channels-schema.md
  0007-node-protocol-and-types.md
  0008-template-registry.md
  0009-plugin-workbench-manifest.md
  0010-data-migration.md

docs/revamp/
  STATUS.md                            (this file)

src/openakita/runtime/
  __init__.py                          public model exports
  models.py                            OrgV2, NodeV2, EdgeV2, …
  cancel_token.py                      CancellationToken / CancelledByToken
  retry_policy.py                      RetryPolicy + retriable taxonomy
  stream.py                            StreamBus + 8 channels
  event_store.py                       hash-chained SQLite WAL log
  checkpoint.py                        BaseCheckpointer + MemoryCheckpointer
  backends/
    __init__.py
    sqlite.py                          SqliteCheckpointer
    json_file.py                       JsonFileCheckpointer
  ledger.py                            TaskLedger + ProgressLedger + parser
  stall_detector.py                    n_stalls regen logic
  supervisor.py                        outer/inner loop end-to-end
  messenger.py                         address resolution + cancel-aware deliver
  guardrail/
    __init__.py
    runner.py                          GuardrailRunner + verdict aggregation
    builtin.py                         min/max length, required fields, regex

src/openakita/runtime/nodes/         Phase 4 — first-class node types
  __init__.py                          public exports
  base.py                              NodeProtocol + NodeContext + BaseNode
  tool_node.py                         deterministic single-tool node
  llm_node.py                          brain-driven reasoning node
  condition_node.py                    deterministic branch / routing node
  human_review_node.py                 pause-on-human checkpoint node
  workbench_node.py                    plugin-as-node, manifest-driven
  manifest.py                          WORKBENCH manifest parser/validator

src/openakita/runtime/templates/    Phase 5 — typed templates + registry
  __init__.py                          public exports
  schema.py                            TemplateSpec / NodeSpec / EdgeSpec /
                                       DefaultsSpec / GuardrailSpec /
                                       WorkbenchBindingSpec / NodeRuntimeOverridesSpec
  registry.py                          @template decorator, TemplateRegistry,
                                       discover_builtins, GLOBAL_REGISTRY
  builtin/
    __init__.py                        package marker
    aigc_video_studio.py               7-node AIGC pipeline (workbench-bound)
    software_team.py                   10-node engineering team
    startup_company.py                 16-node startup org
    content_ops.py                     7-node editorial team

src/openakita/agent/
  __init__.py                          empty shell (Phase 2 fills it)
  state.py                             v2 minimal TaskState + AgentState

plugins/happyhorse-video/
  plugin.py                            now declares a top-level WORKBENCH
                                       constant (4 roles, mode-scoped tools)
  tests/test_workbench_manifest.py     load-time validation guard

tests/runtime/                         99 (Phase 1) + 82 (Phase 3) + 89 (Phase 4) +
                                       75 (Phase 5 templates) = 345 (incl. integration)
tests/agent/                           17 tests
plugins/happyhorse-video/tests/        + 5 manifest smoke tests
```

## What is *not* shipped yet (continuation map)

Each entry below names the module, its ADR, the legacy file it
replaces (with line count), and the rough effort.

### Phase 2 — Agent core, remaining slices

The agent's leaf modules above the Phase-1 foundation. Each is one
focused commit with tests. Already done:

| Module | ADR | Replaces | Status |
|---|---|---|---|
| `agent/state.py` | ADR-0003 | `core/agent_state.py` | **Done.** Phase-1 era port; 17 tests. |
| `agent/errors.py` | ADR-0003 | `core/errors.py` | **Done.** Move with re-export shim; 4 tests. |
| `agent/working_facts.py` | ADR-0003 | `core/working_facts.py` | **Done.** Move with re-export shim; 11 tests. |
| `agent/output_guard.py` | ADR-0003 | `core/agent_output_guard.py` | **Done.** Move with re-export shim; 14 tests. |
| `agent/output_formatter.py` | ADR-0003 | `core/output_formatter.py` | **Done.** Move with re-export shim; 14 tests. |
| `agent/identity.py` | ADR-0003 | `core/identity.py` | **Done.** Byte-equivalent move with docstring refresh; legacy `tests/unit/test_identity.py` (17 tests) keeps passing via shim. |

Remaining:

| Module | ADR | Replaces | Legacy lines | Cap (lines) |
|---|---|---|---|---|
| `agent/permission.py` | ADR-0003 | `core/permission.py` | 455 | 250 |
| `agent/audit.py` | ADR-0003 | `core/audit_logger.py` | 177 | 150 |
| `agent/prompt.py` | ADR-0003 | `core/prompt_assembler.py` | 157 | 200 |
| `agent/context.py` | ADR-0003 | `core/context_manager.py` | 1 569 | 400 |
| `agent/tools.py` | ADR-0003 | `core/tool_executor.py` | 1 609 | 300 |
| `agent/brain.py` | ADR-0003 | `core/brain.py` | 1 698 | 400 |
| **`agent/reasoning.py`** | ADR-0003 | `core/reasoning_engine.py` | **7 987** | **600** |
| **`agent/core.py`** | ADR-0003 | `core/agent.py` | **8 433** | **500** |
| `agent/facade.py` | ADR-0003 | n/a | 0 | 100 |

The two big ones (`reasoning.py` and `core.py`) need a parity harness
under `tests/parity/` that runs identical inputs through the legacy
and the v2 paths. The plan reserves Phase 2 (W6-10) for this; expect
the v2 reasoning loop to be implemented as a state graph driven by
`runtime/state_graph.py` (still pending — see Phase 3 below) so the
full loop body fits in `reasoning.py`.

### Phase 3 — Runtime engine (delivered)

| Module | ADR | Notes |
|---|---|---|
| `runtime/state_graph.py` | ADR-0002 + ADR-0007 | **Done.** Pregel-style ``StateGraph`` with ``add_node`` / ``add_edge`` / ``add_conditional_edges`` / ``set_entry_point`` / ``validate``. ``route()`` resolves in priority conditional > static > delegation hint > defer-to-supervisor; unknown branch labels raise loudly. ``compile_from_org`` projects an OrgV2 into the graph (HIERARCHY/COLLABORATE → static, CONSULT dropped). 28 tests. The supervisor's deliver path will plug in here in Phase 6 as part of the channels-gateway swap. |

### Phase 4 — Nodes (delivered)

| Module | ADR | Status |
|---|---|---|
| `runtime/nodes/base.py` | ADR-0007 | **Done.** NodeProtocol + NodeContext + BaseNode (lifecycle state machine, defensive exception promotion, cooperative cancel routing). 13 tests. |
| `runtime/nodes/tool_node.py` | ADR-0007 | **Done.** Single-tool step with documented JSON / metadata argument parsing. 8 tests. |
| `runtime/nodes/llm_node.py` | ADR-0007 | **Done.** Brain-driven node with bounded ReAct tool loop, allow-list rejection, budget guard, runner-absent recovery. 8 tests. |
| `runtime/nodes/condition_node.py` | ADR-0007 | **Done.** Deterministic sync/async predicate routing with construction-time validation; deterministic failure on unknown / non-string labels. 8 tests. |
| `runtime/nodes/human_review_node.py` | ADR-0007 | **Done.** Three-verdict (APPROVE/REJECT/EDIT) review pause with cooperative cancel cleanup and InMemoryReviewQueue reference. 9 tests. |
| `runtime/nodes/manifest.py` | ADR-0009 | **Done.** WorkbenchManifest / WorkbenchMode / WorkbenchUI dataclasses + `parse(raw_dict)` validator. 14 tests. |
| `runtime/nodes/workbench_node.py` | ADR-0007 + ADR-0009 | **Done.** Plugin-as-node with mode-scoped tool allow-list, explicit mode switching, workbench_ready / workbench_mode_switched / workbench_cancelled lifecycle envelope. 9 tests. |
| `plugins/happyhorse-video/plugin.py` (`WORKBENCH` constant) | ADR-0009 | **Done.** Four-role manifest (art_director / image_artist / video_animator / portrait_actor) + load-time validation in `tests/test_workbench_manifest.py`. |

G4 is now substantively complete: the state graph (the previously
missing piece behind ``ConditionNode.next_address``) is shipped,
and ``plugins/manager.py`` parses the ``WORKBENCH`` manifest at
plugin load (so live plugins self-describe their workbench, not
just templates that hand-build a manifest). Remaining for G4
sign-off is the written gate review note under
``docs/revamp/gates/G4.md`` — paperwork.

### Phase 5 — Templates (delivered)

| Module | ADR | Status |
|---|---|---|
| `runtime/templates/schema.py` | ADR-0008 | **Done.** TemplateSpec / NodeSpec / EdgeSpec / DefaultsSpec / GuardrailSpec / WorkbenchBindingSpec / NodeRuntimeOverridesSpec dataclasses with construction-time `validate()` and JSON round-trip. 25 tests. |
| `runtime/templates/registry.py` | ADR-0008 | **Done.** @template decorator (lazy queue), TemplateRegistry (register / get / list / clear / bootstrap), `instantiate(template_id, name=, overrides=)` mints fresh `OrgV2` with prefixed ULIDs and applies a closed override whitelist (`defaults`, `node_persona_prompts`, `node_runtime_overrides`); unknown override keys raise loudly. **2026-05-18 fix:** `instantiate` now also derives `NodeV2.parent_id` from `EdgeKind.HIERARCHY` edges so `OrgV2.root_nodes()` and `children_of()` return real trees (previously every node looked like a root); multi-parent HIERARCHY raises `TemplateValidationError`, repeat-edges are idempotent. `discover_builtins()` auto-imports `runtime.templates.builtin.*`. 21 tests (16 + 5 parent-id regression). |
| `runtime/templates/builtin/aigc_video_studio.py` | ADR-0008 + ADR-0009 | **Done.** 7-node AIGC studio: producer → screenwriter / art_director → wb_image / wb_video / wb_human / wb_long; four workbench leaves bound to the `happyhorse-video` manifest, with the stitching node narrowed to `(storyboard, long_video, video_concat)` capabilities. Personas Chinese, ~190 lines vs the legacy ~420-line dict. 12 tests. |
| `runtime/templates/builtin/software_team.py` | ADR-0008 | **Done.** 10-node engineering org with HIERARCHY / COLLABORATE / CONSULT edges (qa→leads as CONSULT). 7 tests. |
| `runtime/templates/builtin/startup_company.py` | ADR-0008 | **Done.** 16-node generic startup with four C-level departments and four cross-department COLLABORATE edges. 6 tests. |
| `runtime/templates/builtin/content_ops.py` | ADR-0008 | **Done.** 7-node editorial team with the data-loop COLLABORATE edge from data_analyst → planner. 6 tests. |
| `tests/runtime/templates/test_builtin_discovery.py` | ADR-0008 | **Done.** Generic guard: `discover_builtins` imports every non-underscore module under `builtin/`, every registered TemplateSpec validates and instantiates, and the four flagship template ids are present. |

The legacy `orgs/templates.py` (1 234 lines) and
`orgs/plugin_workbench_templates.py` (225 lines) are deleted in
Phase 8 once the API surface in Phase 6 is wired to the v2
registry. The Phase 6 facade should call::

    from openakita.runtime.templates import discover_builtins, GLOBAL_REGISTRY
    discover_builtins()
    GLOBAL_REGISTRY.bootstrap()

once at startup and serve `GLOBAL_REGISTRY.list()` from
`/api/orgs/templates`.

### Phase 6 — API / channels swap

| Slice | Status |
|---|---|
| `src/openakita/api/routes/orgs_v2.py` (template facade) | **Done.** ``GET /api/v2/orgs/templates`` (list), ``GET /api/v2/orgs/templates/{id}`` (one), ``POST /api/v2/orgs/templates/{id}/instantiate`` (mint a fresh OrgV2). Gated by ``settings.runtime_v2_enabled``. 15 tests. |
| `runtime/templates/registry.py` survivable factory marker | **Done.** ``@template`` now also sets ``__openakita_template_factory__`` on the function so :func:`collect_builtin_factories` can repopulate the registry without depending on the lazy ``_PENDING`` queue (which test fixtures sometimes drain). 3 new tests. |
| `api/server.py` mount | **Done.** Router included after the v1 ``orgs`` routers. Always mounted; per-request flag check decides whether to serve. |
| `runtime/api/v2/orgs/{id}` resource (CRUD) | Pending (Phase 7 also persists). |
| `src/openakita/channels/gateway.py` per-org v2 route | Pending. |
| `apps/setup-center/src/components/OrgChatPanel.tsx` v2 stream | Pending. |

### Phase 7 — Cutover

`scripts/migrate_orgs_to_legacy.py` (ADR-0010) plus
`runtime.facade.bootstrap_builtins()` for the fresh-start data
policy. 7-day burn-in is not gated by code; it is a runbook step.

### Phase 8 — Legacy removal

Mechanical `git rm` of `src/openakita/orgs/` and the rewritten
`src/openakita/core/*.py` files. One commit per concern so a `git
log` reader can diff the world before / after.

## How to resume in the next session

1. Read this `STATUS.md` first, then `docs/revamp/RELEASE_v2.md` for
   the v2.0.0-rc1 release notes (Phase 0 → 8 RC scope) and
   `docs/revamp/burn_in.md` for the operator runbook.
2. **All eight phases of the original plan have shipped at RC
   scope.** The local `v2.0.0-rc1` tag marks the milestone.
3. **Post-RC mechanical cleanup checklist** (gated by the burn-in
   exit criteria in `burn_in.md`):
   * Migrate ~20 production callers (channels gateway, api routes,
     agents.factory, orgs runtime/command_service/tool_handler) from
     `openakita.orgs.*` to `runtime.orgs` + `runtime.state_graph`,
     then `git rm -r src/openakita/orgs/` and the ~50
     `tests/orgs/*` files.
   * Migrate ~20 callers in `sessions/` / `memory/` / `tools/` /
     `agents/` from `openakita.core.*` MOVE shims to
     `openakita.agent.*`, then delete the shims.
   * Deep slim refactor of the five REWRITE targets — each its own
     chain (extract streaming into `runtime/stream/`, collapse
     routing/retry into `runtime.retry_policy`, drive reasoning by
     `runtime.state_graph`, split `agent.core` helpers into
     `runtime/desktop/` + `agent/safety/`).
   * Optional rename `core/task_monitor.py` → `runtime/standup.py`
     once the importer set is small enough.
4. Always:
   * one logical change per commit, English commit body, mention
     the relevant ADR;
   * `python -m pytest tests/runtime tests/agent tests/api
     tests/unit/test_plugins tests/parity --no-header -q`
     before every commit (must keep 755 + 1 skipped green);
   * `python -m ruff check src/openakita/runtime src/openakita/agent
     src/openakita/plugins/manager.py tests/runtime tests/agent
     tests/api tests/parity` before every commit.
5. Never edit the plan file or the ADR `Status:` lines without a
   user-led review.

## How to *use* what already exists today

Even before Phases 4-7 land, the v2 supervisor stack is usable for
small experiments:

```python
import asyncio
from openakita.runtime.checkpoint import MemoryCheckpointer
from openakita.runtime.stream import StreamBus
from openakita.runtime.supervisor import Supervisor, DelegationResult
from openakita.runtime.messenger import Messenger, InMemoryNodeRegistry

# 1. Define a fake brain that satisfies SupervisorBrain.
# 2. Register a node on an InMemoryNodeRegistry.
# 3. messenger.bind_for_command(...) gives you the deliver callable.
# 4. Pass everything into Supervisor and call await sup.run().
```

The integration test in `tests/runtime/test_supervisor.py` is the
canonical wiring example; copy it as a starting point.
