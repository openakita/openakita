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
| 2 — Agent rewrite | **In progress (foundation slice)** | 1 (`agent/state.py`) | 17 agent tests |
| 3 — Runtime engine (supervisor + messenger + guardrail) | **In progress (critical path complete)** | 5 (`ledger`, `stall_detector`, `supervisor`, `messenger`, `guardrail/`) | 82 new runtime tests |
| 4 — Nodes | **Implementation complete (G4 review pending)** | 6 (`base`+sig fix, `tool_node`, `llm_node`, `condition_node`+`human_review_node`, `workbench_node`+`manifest`, `happyhorse-video` adoption) | 89 new tests (`test_nodes_*`) + 5 plugin smoke tests |
| 5 — Templates | **Schema + registry + 4 builtins shipped (G5 review pending)** | 6 (`schema`, `registry`, `aigc_video_studio`, `software_team`, `startup_company`, `content_ops`+discovery test) | 75 template tests |
| 6 — API / channels swap | Pending | 0 | — |
| 7 — Cutover + data migration | Pending | 0 | — |
| 8 — Legacy removal | Pending | 0 | — |

Total to date: **36 code commits + 10 ADR commits = 46 commits on
`revamp/v2`**, all lint-clean (ruff), test-green (344 / 344 across
`tests/runtime/`, `tests/agent/`, and `plugins/happyhorse-video/tests/test_workbench_manifest.py`).

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
focused commit with tests.

| Module | ADR | Replaces | Legacy lines | Cap (lines) |
|---|---|---|---|---|
| `agent/identity.py` | ADR-0003 | `core/identity.py` | 495 | 250 |
| `agent/permission.py` | ADR-0003 | `core/permission.py` | 455 | 250 |
| `agent/audit.py` | ADR-0003 | `core/audit_logger.py` | 177 | 150 |
| `agent/output_guard.py` | ADR-0003 | `core/agent_output_guard.py` | 86 | 200 |
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

### Phase 3 — Runtime engine, remaining slices

| Module | ADR | Notes |
|---|---|---|
| `runtime/state_graph.py` | ADR-0007 | LangGraph-style BSP engine. Required for ConditionNode + multi-node fan-out. The supervisor + messenger work end-to-end without it for single-flight, hierarchical orgs (the AIGC studio shape today). |

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

Outstanding before we can declare gate G4 closed:

* `runtime/state_graph.py` (Phase 3 leftover; ADR-0002). The
  ConditionNode contract is layered on top of a missing
  StateGraph: today the supervisor delegates linearly via the
  messenger, so a ConditionNode result with `next_address` is set
  but no engine yet picks the next speaker from it. State graph
  is a separate small commit (next session); ConditionNode itself
  is fully tested in isolation.
* End-to-end smoke test wiring `Supervisor → Messenger → ToolNode
  + LLMNode + WorkbenchNode + ConditionNode` for one synthetic
  org. The plumbing is all there; this is one new
  `tests/runtime/test_node_integration.py`.

### Phase 5 — Templates (delivered)

| Module | ADR | Status |
|---|---|---|
| `runtime/templates/schema.py` | ADR-0008 | **Done.** TemplateSpec / NodeSpec / EdgeSpec / DefaultsSpec / GuardrailSpec / WorkbenchBindingSpec / NodeRuntimeOverridesSpec dataclasses with construction-time `validate()` and JSON round-trip. 25 tests. |
| `runtime/templates/registry.py` | ADR-0008 | **Done.** @template decorator (lazy queue), TemplateRegistry (register / get / list / clear / bootstrap), `instantiate(template_id, name=, overrides=)` mints fresh `OrgV2` with prefixed ULIDs and applies a closed override whitelist (`defaults`, `node_persona_prompts`, `node_runtime_overrides`); unknown override keys raise loudly. `discover_builtins()` auto-imports `runtime.templates.builtin.*`. 16 tests. |
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

* `src/openakita/api/routes/orgs_v2.py` mounts the v2 facade behind
  `runtime_v2_enabled`;
* `src/openakita/channels/gateway.py` learns to route per-org based
  on the same flag;
* `apps/setup-center/src/components/OrgChatPanel.tsx` subscribes to
  the multi-channel StreamBus and renders the progress-ledger
  timeline.

### Phase 7 — Cutover

`scripts/migrate_orgs_to_legacy.py` (ADR-0010) plus
`runtime.facade.bootstrap_builtins()` for the fresh-start data
policy. 7-day burn-in is not gated by code; it is a runbook step.

### Phase 8 — Legacy removal

Mechanical `git rm` of `src/openakita/orgs/` and the rewritten
`src/openakita/core/*.py` files. One commit per concern so a `git
log` reader can diff the world before / after.

## How to resume in the next session

1. Read this `STATUS.md` first.
2. Recommended next slice: **Phase 6 — API facade for templates**.
   Mount `runtime.templates.GLOBAL_REGISTRY` behind
   `/api/orgs/templates` (list) + `/api/orgs/templates/{id}/instantiate`
   (POST → returns the fresh `OrgV2`). The frontend
   `OrgEditor` template-picker can then drop the legacy
   `orgs/templates.py` import. Estimate: one focused commit
   (route + DI wiring + tests), feature-flagged on
   `runtime_v2_enabled`.
3. Alternatively: **Phase 3 `runtime/state_graph.py`**. Not
   strictly on the critical path because the existing supervisor +
   messenger handles single-flight hierarchical delegation, but
   ConditionNode's `next_address` result is currently advisory —
   the engine that consumes it is the missing piece. Estimate:
   ~300 lines + tests.
4. If continuing Phase 2: pick the smallest unfinished module from
   the list above and stand it up under `src/openakita/agent/` with
   a test file under `tests/agent/`.
5. Always:
   * one logical change per commit, English commit body, mention
     the relevant ADR;
   * `python -m pytest tests/runtime tests/agent --no-header -q`
     before every commit;
   * `python -m ruff check src/openakita/runtime src/openakita/agent
     tests/runtime tests/agent` before every commit.
6. Never edit the plan file or the ADR `Status:` lines without a
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
