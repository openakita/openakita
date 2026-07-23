# ADR-0003 — Agent Package Architecture

- **Status**: Accepted
- **Date**: 2026-05-18
- **Accepted**: 2026-05-19 (after P-RC-0..7 implementation review at G-RC-8)
- **Phase**: 0 (Spec Freeze)

## Context

`src/openakita/core/agent.py` has grown to **8 433 lines**, and
`src/openakita/core/reasoning_engine.py` to **7 987 lines**. Together they
hold the entire agent runtime: identity loading, persona compilation,
ReAct loop, brain (LLM frontend), tool selection, tool execution,
permission checks, audit trail, output guards, microcompact, file
history, sandbox enforcement, and dozens of cross-cutting concerns.

For comparison, the equivalent abstractions in upstream open-source are
roughly an order of magnitude smaller (AutoGen `BaseChatAgent` ~250 lines;
LangGraph `prebuilt.create_react_agent` ~600 lines; CrewAI `BaseAgent`
~500 lines). Our extra mass is not feature surface — it is accreted glue
code: copies of similar checks, repeated try/except scaffolding, in-method
state machines, and inlined config readers.

The v2 work needs an agent package whose modules are individually
readable, individually testable, and whose total line count is no more
than three times the upstream baselines.

## Decision

> **Import-surface amendment (2026-07-23):** the transitional
> `openakita.core.*` re-exports described below have been removed.
> `openakita.agent.*` is now the only supported public agent import surface.
> Internal execution code may remain under `openakita.core._*_runtime` while
> it is decomposed, but those underscored modules are not compatibility APIs.

The new agent lives under `src/openakita/agent/`. Strict
single-responsibility split:

```
src/openakita/agent/
  __init__.py
  facade.py             # public Agent class + module-level entry points
  core.py               # Agent class shell (config, lifecycle, top-level chat() entry)
  reasoning.py          # ReAct loop driving runtime/state_graph
  brain.py              # LLM frontend (model selection, retries, usage)
  tools.py              # tool dispatch and result handling
  context.py            # context window management
  identity.py           # SOUL.md / AGENT.md / USER.md loader
  state.py              # AgentState dataclass + session memory adapter
  permission.py         # permission checks (TrustedPaths, capability gates)
  audit.py              # structured audit log adapter
  output_guard.py       # output safety / format validation
  prompt.py             # prompt assembly entry (delegates to prompt/)
```

### Hard size caps

| File | Cap (lines) | Legacy source | Reduction target |
|---|---|---|---|
| `core.py` | 500 | `core/agent.py` (8 433) | **94 %** |
| `reasoning.py` | 600 | `core/reasoning_engine.py` (7 987) | **93 %** |
| `brain.py` | 400 | `core/brain.py` (1 698) | 76 % |
| `tools.py` | 300 | `core/tool_executor.py` (1 609) | 81 % |
| `context.py` | 400 | `core/context_manager.py` (1 569) | 75 % |
| `state.py` | 200 | `core/agent_state.py` (431) | 54 % |
| `identity.py` | 250 | `core/identity.py` (495) | 49 % |
| `permission.py` | 250 | `core/permission.py` (455) | 45 % |
| `audit.py` | 150 | `core/audit_logger.py` (177) | small |
| `output_guard.py` | 200 | `core/agent_output_guard.py` (86) | grow with hook surface |
| `prompt.py` | 200 | `core/prompt_assembler.py` (157) | small |
| `facade.py` | 100 | n/a | new |

These caps reflect the intent that v2 keeps current public capabilities
but eliminates accreted glue. Caps are enforced at the same review
discipline as ADR-0002: any file approaching its cap must justify in the
commit body.

### Public surface kept stable

To avoid a flag-day for plugins and channels, `agent/facade.py` re-exports
the few public names that legacy callers import:

```python
# src/openakita/agent/facade.py
from .core import Agent
from .state import AgentState

__all__ = ["Agent", "AgentState"]
```

For one transitional release, `src/openakita/core/__init__.py` will
re-export from `agent.facade`. After Gate G7 cutover, the legacy
`core/__init__.py` is deleted and external callers must import from
`openakita.agent`. This is the only breaking change for plugin authors.

### Reasoning loop driving runtime/state_graph

`agent.reasoning` does not hand-roll a ReAct cascade. It treats the
ReAct loop as a `StateGraph` (see ADR-0007) with three node types:

- **think node** — calls `agent.brain.complete()` and yields a
  `ReasoningStep`.
- **tool node** — dispatches a tool call through `agent.tools`.
- **observe node** — folds a tool result into the context.

The state graph terminates on `final_answer` or hits its `recursion_limit`
(replaces `max_iterations`). The whole loop body fits in `reasoning.py`
because all the state machinery lives in `runtime/state_graph.py`.

## Consequences

### Positive

- Two 8 000-line files become a tree of <500-line files. Code review
  becomes possible.
- Fixing a permission bug touches `permission.py`, not a 400-line method
  buried inside `core/agent.py`.
- A future port to a different LLM backend touches only `brain.py`.

### Negative / Accepted Cost

- Plugin authors importing `openakita.core.Agent` get a deprecation
  warning during the transitional window. Migration is a one-line
  import change.
- The transitional re-export means `core/__init__.py` exists for one
  release after cutover with stub semantics. We document this in
  `docs/revamp/migration.md` (Phase 7).

## Alternatives considered

1. **Keep monolith, only rewrite reasoning_engine.** Rejected: the
   `agent.py` mass is the real ergonomic blocker; reasoning alone is
   half the problem.
2. **Adopt LangChain `Runnable` chain interface as agent runtime.**
   Rejected: too much surface drift from current behaviour and from the
   identity / permission / audit features that OpenAkita already ships.
3. **Auto-generate the new agent from legacy via deterministic
   refactoring tools.** Rejected: the legacy code's accreted patterns
   would survive the migration. The point of the rewrite is to leave
   them behind.

## References

- AutoGen agent: `D:\claw-research\repos\autogen\python\packages\autogen-agentchat\src\autogen_agentchat\agents\`.
- LangGraph prebuilt ReAct: `D:\claw-research\repos\langgraph\libs\prebuilt\langgraph\prebuilt\`.
- CrewAI agent: `D:\claw-research\repos\crewAI\lib\crewai\src\crewai\agents\`.
- Legacy under audit: [src/openakita/core/agent.py](../../src/openakita/core/agent.py),
  [src/openakita/core/reasoning_engine.py](../../src/openakita/core/reasoning_engine.py).
- Layering host: [ADR-0002](0002-runtime-architecture.md).
- Reasoning state graph: [ADR-0007](0007-node-protocol-and-types.md).
