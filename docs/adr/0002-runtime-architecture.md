# ADR-0002 — Runtime Package Architecture

- **Status**: Accepted
- **Date**: 2026-05-18
- **Accepted**: 2026-05-19 (after P-RC-0..7 implementation review at G-RC-8)
- **Phase**: 0 (Spec Freeze)
- **Supersedes**: none

## Context

Today the orchestration runtime lives in `src/openakita/orgs/` with one
file (`runtime.py`, 5 734 lines) that owns: lifecycle, agent activation,
chain tracking, command tracking, watchdogs, scheduler integration,
plugin asset capture, file output recording, quota handling, and message
routing. Adding any one new concern requires editing this file. There are
no protocols separating concerns; methods reach freely into other
methods' private state.

The legacy directory also contains 25 sibling files that interact with
`runtime.py` through implicit conventions (e.g. `OrgBlackboard`,
`OrgEventStore`, `OrgMessenger`, `OrgHeartbeat`). Tests for one concern
(say, command cancellation) cannot stand alone because they pull in the
whole runtime.

The v2 work needs an explicit, layered architecture so that each future
change touches one file, and each file has one declared responsibility.

## Decision

The new runtime lives under `src/openakita/runtime/` with a strict
layering. Top-level modules:

```
src/openakita/runtime/
  __init__.py
  models.py             # OrgV2, NodeV2, EdgeV2 dataclasses
  state_graph.py        # explicit BSP execution graph
  supervisor.py         # Magentic-One dual-ledger orchestrator
  ledger.py             # TaskLedger + ProgressLedger dataclasses
  stall_detector.py     # progress/stall counter, replan trigger
  checkpoint.py         # BaseCheckpointer protocol
  backends/
    __init__.py
    sqlite.py           # SqliteCheckpointer
    memory.py           # MemoryCheckpointer (tests / dev)
    json_file.py        # JsonFileCheckpointer (debugging)
  stream.py             # StreamBus, channel definitions
  cancel_token.py       # CancellationToken (cooperative)
  retry_policy.py       # RetryPolicy, retriable error taxonomy
  event_store.py        # hash-chained, append-only event log
  messenger.py          # MessengerV2 — node addressing & routing
  guardrail/
    __init__.py
    runner.py           # GuardrailRunner
    builtin.py          # length/json-schema/regex/llm-judge guardrails
    types.py            # Guardrail protocol
  nodes/                # see ADR-0007 for node types
    __init__.py
    base.py             # NodeProtocol
    llm_node.py
    workbench_node.py
    tool_node.py
    condition_node.py
    human_review_node.py
  templates/            # see ADR-0008 for template registry
    __init__.py
    registry.py
    schema.py
    builtin/
      aigc_video_studio.py
      customer_service.py
      research_team.py
      ...
  facade.py             # public-facing API for api/routes consumers
```

### Layering rules (enforced by review at every commit)

```
                facade
                  |
                  v
              supervisor
              /   |    \
             /    |     \
   state_graph  ledger  stall_detector
             \    |     /
              \   v    /
              messenger
                  |
                  v
            checkpoint, stream, event_store, cancel_token, retry_policy
                  |
                  v
                models
```

- A module may depend on modules **strictly below** it in the diagram, never
  sideways or upward.
- `models.py` is leaf-level and has no internal dependencies.
- `nodes/` import from `models`, `cancel_token`, `stream`, and the public
  parts of `state_graph`. They never reach into `supervisor`.
- `templates/` import only from `models`, `nodes` (for `NodeProtocol`), and
  `schema`. They never run code at import time.
- `facade.py` is the only module imported by `api/routes/` and
  `channels/gateway.py`.

### Size budget

Each top-level module has a soft cap; the AI agent must justify in the
commit body whenever a file is about to exceed its cap:

| Module | Soft cap (lines) | Inspiration / reference |
|---|---|---|
| `models.py` | 600 | LangGraph `pregel/protocol.py` |
| `state_graph.py` | 800 | LangGraph `pregel/_loop.py` core slice |
| `supervisor.py` | 900 | AutoGen `_magentic_one_orchestrator.py` (~600 lines today) |
| `ledger.py` | 300 | AutoGen `LedgerEntry` plus prompts |
| `stall_detector.py` | 200 | n_stalls counter + threshold logic |
| `checkpoint.py` | 250 | LangGraph `BaseCheckpointSaver` |
| `backends/sqlite.py` | 400 | LangGraph `checkpoint-sqlite` |
| `stream.py` | 350 | LangGraph multi-mode stream emit |
| `cancel_token.py` | 150 | AutoGen `_cancellation_token.py` (~50 lines) |
| `retry_policy.py` | 250 | LangGraph `pregel/_retry.py` core slice |
| `event_store.py` | 350 | SINT `engine-evidence-ledger` shape |
| `messenger.py` | 500 | clean rebuild of legacy 552-line `messenger.py` |
| `guardrail/runner.py` | 350 | CrewAI `process_guardrail` |
| `nodes/base.py` | 200 | NodeProtocol + lifecycle hooks |
| `nodes/llm_node.py` | 400 | thin wrapper over `agent.Agent` |
| `nodes/workbench_node.py` | 500 | manifest-driven multi-mode node |
| `nodes/tool_node.py` | 200 | deterministic single-tool step |
| `nodes/condition_node.py` | 200 | LLM/rule branch |
| `nodes/human_review_node.py` | 200 | interrupt_before semantics |
| `templates/registry.py` | 300 | template loading and indexing |
| `templates/schema.py` | 400 | JSON-serialisable spec |
| `facade.py` | 500 | public API surface |

A file that exceeds its cap by more than 20% must be split before its
commit lands; the splitter ADR-0002 supersession path applies.

## Consequences

### Positive

- Every reader can find concern X in module X. No hunting through 5 000 lines.
- Tests can target a single layer; a `messenger.py` test does not need
  `supervisor` or `nodes` imports.
- Future contributors (human or AI) have explicit dependency rules to enforce
  during code review.

### Negative / Accepted Cost

- More files. Twenty-plus instead of one. We accept the import overhead.
- Layering rules must be policed during review; we add a custom
  Ruff/`importlinter` configuration in Phase 1 to flag layer violations
  automatically.

## Alternatives considered

1. **Single big file like today.** Rejected: this is the failure mode we are
   leaving behind.
2. **Microservice split.** Rejected: orchestration latency must stay sub-100ms
   between supervisor decisions and node activation; in-process layering
   suffices.
3. **`langgraph` as a runtime dependency.** Rejected (see ADR-0001
   alternative 3). We borrow architecture without adopting the dependency.

## References

- LangGraph layering: `D:\claw-research\repos\langgraph\libs\langgraph\langgraph\pregel\` directory.
- AutoGen layering: `D:\claw-research\repos\autogen\python\packages\autogen-core\` and `autogen-agentchat`.
- Cortex Elixir layering: `D:\claw-research\repos\cortex\lib\cortex\` (informative only — Elixir).
- Legacy code being replaced: [src/openakita/orgs/runtime.py](../../src/openakita/orgs/runtime.py).
