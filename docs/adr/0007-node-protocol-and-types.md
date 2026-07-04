# ADR-0007 — Node Protocol and Node Types

- **Status**: Accepted
- **Date**: 2026-05-18
- **Accepted**: 2026-05-19 (after P-RC-0..7 implementation review at G-RC-8)
- **Phase**: 0 (Spec Freeze)

## Context

In the legacy runtime the only "node" abstraction is `OrgNode`, a flat
dataclass storing `id`, `role`, `prompt`, `status`, `parent_id`, etc.
The behaviour of a node — how it executes a turn, how it cancels, how it
reports progress — is hard-coded in `OrgRuntime._activate_and_run`,
which switches on profile fields and reaches into the agent runtime
directly.

There is no concept of "node type". `producer` vs `worker` vs
`workbench` are encoded as prompt fragments and as ad-hoc `if` branches.
Adding a new behaviour (e.g. a deterministic tool-only step, or a hold
that waits for human review) means another branch in
`_activate_and_run`.

The plan committed to a per-template **WorkbenchNode** abstraction
(constraint C2). To make WorkbenchNode meaningful we first need a node
**protocol** that all node types implement.

## Decision

Define `NodeProtocol` in `runtime/nodes/base.py`. Every node type is
a class implementing this protocol. The supervisor never reaches into
node internals; it only invokes protocol methods.

### Protocol

```python
# src/openakita/runtime/nodes/base.py
from __future__ import annotations
from typing import Protocol, Any, runtime_checkable

@runtime_checkable
class NodeProtocol(Protocol):
    """Lifecycle and execution contract for a runtime node."""

    node_id: str
    node_type: str            # discriminator for dispatch / serialisation
    org_id: str

    async def on_activate(self, ctx: NodeContext) -> None: ...
    """Called once when the supervisor first delegates to this node.
       The node may load resources, prime caches, etc."""

    async def on_message(self, ctx: NodeContext, msg: NodeMessage) -> NodeResult:
        ...
    """Process one delegation. The node may emit stream events via
       ctx.stream, write to channels via ctx.channels, request
       cancellation via ctx.cancel_token, and so on. The returned
       NodeResult carries the deliverable (or a partial / failure)."""

    async def on_cancel(self, ctx: NodeContext, reason: str) -> None: ...
    """Cooperative cancel hook. The node should save in-flight state
       and return promptly. Idempotent."""

    async def save_state(self) -> dict[str, Any]: ...
    """Return JSON-serialisable state for checkpoint storage."""

    async def load_state(self, state: dict[str, Any]) -> None: ...
    """Restore state previously returned by save_state."""
```

### `NodeContext`

```python
@dataclass
class NodeContext:
    org: OrgV2
    node: NodeV2
    stream: StreamBus
    channels: ChannelManager
    cancel_token: CancellationToken
    checkpointer: BaseCheckpointer
    runtime_facade: "RuntimeFacade"
```

Nodes receive a `ctx` per call rather than holding references to the
runtime. This keeps node implementations testable in isolation.

### Node types in v2

Phase 4 ships these built-in node types. Each lives in its own file
under `runtime/nodes/`:

#### 1. `LLMNode` (`llm_node.py`)

The default node. Hosts an `agent.Agent`. `on_message` runs one
delegation through the agent's reasoning loop and returns the
deliverable. Replaces the implicit `_activate_and_run_inner` of the
legacy runtime.

#### 2. `WorkbenchNode` (`workbench_node.py`) *(constraint C2)*

A multi-mode node backed by a plugin. Reads the plugin's `WORKBENCH`
manifest (see [ADR-0009](0009-plugin-workbench-manifest.md)) and:

- exposes the per-mode tool subset to the LLM;
- applies per-mode `system_prompt_override` and per-mode guardrails;
- registers the plugin's UI URL with the front-end via a `lifecycle`
  stream event so the iframe surface in `OrgChatPanel.tsx` can render;
- routes tool calls through the plugin's existing `register_tools`
  interface unchanged.

A single `WorkbenchNode` instance can switch modes within a turn if the
supervisor's `next_speaker` selection includes a mode hint
(`"happyhorse-video::art_director"`); mode switches reset only the
mode-scoped state, not the node's session.

#### 3. `ToolNode` (`tool_node.py`)

Deterministic single-tool step (no LLM). Used when a template wants to
encode "always run tool X with these args" as a graph step. Replaces
hard-coded "always-run" hacks scattered through the legacy producer
prompts.

#### 4. `ConditionNode` (`condition_node.py`)

LLM- or rule-driven branch. Returns a string label that the state graph
maps to the next node. Implements the LangGraph `add_conditional_edges`
pattern.

#### 5. `HumanReviewNode` (`human_review_node.py`)

Implements LangGraph-style `interrupt_before` semantics for the
OpenAkita `org_request_human_review` workflow. `on_message` raises a
`SupervisorInterrupt` that the supervisor catches and converts into a
suspended checkpoint plus a stream `lifecycle` event of type
`awaiting_human_review`. The user resumes the run by replying with an
approval/rejection through any channel; the supervisor's `resume()`
path takes over.

### Lifecycle and node state

The supervisor tracks per-node state via `NodeContext.runtime_facade.set_node_status`
which emits a typed `lifecycle` event. Allowed transitions form a state
machine that supersedes the legacy `NodeStatus`:

```
            ┌──────── on_activate ────────┐
            │                             v
   created ──────────────────────────► idle
                                      │   ▲
                              activate│   │complete
                                      v   │
                                     busy
                                      │   ▲
                            stall(supv)│   │progress
                                      v   │
                                   suspect
                                      │
                                cancel│
                                      v
                                  cancelled
```

`error` and `offline` remain as terminal-ish states for unrecoverable
failures and explicit unloading. `frozen` (legacy) is dropped — its only
use was a debugging aid that we replace with `suspect` plus stream events.

### Cooperation with the supervisor

A node MUST:

- emit at least one `updates` stream event per turn so the activity
  feed shows progress;
- check `ctx.cancel_token.is_cancelled()` at every safe point and
  return promptly when set;
- return `NodeResult` with one of `Status.OK | Status.GUARDRAIL_FAIL |
  Status.PARTIAL | Status.SKIPPED` so the supervisor's guardrail and
  replan paths can distinguish outcomes.

A node MAY:

- emit `messages` token deltas for live UI streaming;
- request a sub-checkpoint via `ctx.checkpointer.aput(...)` for very
  long single-turn work (e.g. a 30s tool call mid-turn).

A node MUST NOT:

- import from `runtime/supervisor.py`;
- decide its own retry policy (the supervisor does this);
- write to `runtime/checkpoint.py` outside of `save_state` /
  `load_state`.

## Consequences

### Positive

- Adding a new node type is one new file under `runtime/nodes/` plus a
  registry entry. No edits to supervisor or state graph.
- Tests for a node type can run against a mock `NodeContext` and assert
  pure protocol-level behaviour.
- WorkbenchNode (constraint C2) becomes a clean, plugin-driven
  implementation rather than another `if branch` in legacy code.
- Human review (constraint inherited from existing
  `org_request_human_review`) gets a first-class node type with
  resumable semantics.

### Negative / Accepted Cost

- Plugin authors who today reach into `OrgNode` private fields will
  need to migrate to `WorkbenchNode` manifests. We provide a migration
  doc in Phase 4.

## Alternatives considered

1. **Single `Node` class with strategy field.** Rejected: same code
   smell as legacy. Strategies grow into another monolith.
2. **AutoGen `BaseChatAgent` directly.** Rejected: their Agent and our
   Agent have different lifecycle assumptions (in particular, our
   permissions and audit layer). NodeProtocol is minimal enough to
   coexist.
3. **No protocol, just dataclasses.** Rejected: behaviour-bearing nodes
   are essential to make the supervisor minimal.

## References

- LangGraph node API: `D:\claw-research\repos\langgraph\libs\langgraph\langgraph\graph\state.py`.
- AutoGen agent base: `D:\claw-research\repos\autogen\python\packages\autogen-agentchat\src\autogen_agentchat\base\_chat_agent.py`.
- Legacy node activation being replaced: [src/openakita/orgs/runtime.py](../../src/openakita/orgs/runtime.py) `_activate_and_run`.
- Plugin workbench manifest spec: [ADR-0009](0009-plugin-workbench-manifest.md).
- Supervisor protocol contract: [ADR-0004](0004-dual-ledger-supervisor.md).
