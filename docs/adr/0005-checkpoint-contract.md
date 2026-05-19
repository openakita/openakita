# ADR-0005 — Checkpoint Contract

- **Status**: Accepted
- **Date**: 2026-05-18
- **Accepted**: 2026-05-19 (after P-RC-0..7 implementation review at G-RC-8)
- **Phase**: 0 (Spec Freeze)

## Context

In the legacy runtime, when a task is cancelled (timeout, user `/stop`,
or process restart), every byte of intermediate progress is lost. The
parent producer node sees no deliverable, decides the task failed, and
re-delegates from scratch. There is no way to ask "where did we get to?"
because no record of mid-task state was ever made.

The legacy `OrgEventStore` writes events but does not carry runtime
state; replaying events does not reconstruct the supervisor or any
node's mid-execution context. The legacy `ProjectStore` records
deliverables but only at the boundary of a completed task.

What v2 needs is a **checkpoint** — a single transactional snapshot,
taken at well-defined points, that lets us resume execution exactly
where it left off, including the supervisor's TaskLedger, ProgressLedger
history, stall counter, pending channel writes, and per-node state.

## Decision

We define a `BaseCheckpointer` protocol modelled on
LangGraph's `BaseCheckpointSaver`, simplified for our needs.

### Protocol

```python
# src/openakita/runtime/checkpoint.py
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, AsyncIterator

CheckpointId = str    # ULID, lexicographically sortable
CommandId   = str

@dataclass(frozen=True)
class CheckpointMetadata:
    checkpoint_id: CheckpointId
    parent_id: CheckpointId | None
    command_id: CommandId
    org_id: str
    superstep: int
    status: str   # "running" | "interrupted" | "done" | "out_of_steps" | "cancelled" | "failed"
    n_stalls: int
    n_turns: int
    created_at: datetime

@dataclass(frozen=True)
class Checkpoint:
    metadata: CheckpointMetadata
    state: dict[str, Any]    # canonical JSON-serialisable supervisor state
                             # contains: task_ledger, progress_ledgers (bounded),
                             #          pending_writes, channel snapshots,
                             #          per-node state slices
    pending_writes: list[dict[str, Any]]  # writes not yet applied

class BaseCheckpointer(ABC):
    @abstractmethod
    async def aput(self, checkpoint: Checkpoint) -> CheckpointMetadata: ...
    @abstractmethod
    async def aget(self, checkpoint_id: CheckpointId) -> Checkpoint | None: ...
    @abstractmethod
    async def aget_latest(self, command_id: CommandId) -> Checkpoint | None: ...
    @abstractmethod
    async def alist(self, command_id: CommandId,
                    *, limit: int = 64) -> AsyncIterator[CheckpointMetadata]: ...
    @abstractmethod
    async def adelete_command(self, command_id: CommandId) -> int: ...
```

### Backends

Phase 1 ships three backends, identical contract:

- `runtime/backends/sqlite.py` — default. WAL mode; one row per
  checkpoint; `state` column stores compressed (zlib) canonical JSON.
- `runtime/backends/memory.py` — in-process dict; for tests and one-off
  scripts.
- `runtime/backends/json_file.py` — one file per checkpoint under
  `data/runtime/checkpoints/<command_id>/<checkpoint_id>.json` for
  developer-mode debugging.

### Checkpoint frequency

Checkpoints are written:

1. **Every supervisor inner-loop turn**, immediately after the
   `ProgressLedger` is generated and before any node delegation.
2. **On every accepted deliverable** (post-guardrail).
3. **On supervisor cancel** — a final checkpoint with
   `status="cancelled"` so resume is exact.

We chose per-turn checkpointing (rather than time-based) because turns
are the only natural transactional boundary; per-second checkpointing
would write redundant state, and per-deliverable alone would lose mid-turn
replan history.

### State envelope

`Checkpoint.state` is a **canonical JSON document** with a versioned
schema:

```jsonc
{
  "$schema_version": 1,
  "supervisor": { "task_ledger": {...}, "progress_ledgers": [...],
                  "n_stalls": 0, "n_turns": 4, "last_speaker": "art_director" },
  "channels":   { "blackboard": {...}, "deliverables": [...], ... },
  "nodes":      { "art_director": {...}, "image_artist": {...}, ... }
}
```

Schema versioning lets us evolve the envelope without breaking past
checkpoints. Loaders dispatch on `$schema_version`; missing or unknown
versions raise `CheckpointSchemaError` with a user-readable diagnosis.

### Resume semantics

```
supervisor = Supervisor(...)
ckpt = await checkpointer.aget_latest(command_id)
if ckpt:
    supervisor.load_state(ckpt.state["supervisor"])
    runtime.restore_channels(ckpt.state["channels"])
    runtime.restore_nodes(ckpt.state["nodes"])
    runtime.replay_pending_writes(ckpt.pending_writes)
    stream.emit("resumed", {"checkpoint_id": ckpt.metadata.checkpoint_id,
                            "superstep": ckpt.metadata.superstep})
await supervisor.run(...)
```

A resumed run continues from the **next** turn after the restored one;
the restored turn's writes are reapplied idempotently. Idempotence is the
caller's responsibility for tool calls; runtime-level writes (channel
updates, ledger appends) are inherently idempotent because they are
content-addressed by `(command_id, superstep, key)`.

### Retention

Default retention: keep the last 32 checkpoints per command, plus all
checkpoints with `status` in `{"cancelled", "failed", "out_of_steps"}`.
Garbage collection runs lazily on `aput`. The `org_command_max_seconds`
guardrail (see [ADR-0004](0004-dual-ledger-supervisor.md)) gates total
checkpoint volume by gating total command duration.

### Migration considerations

Legacy `OrgEventStore` records continue to be appended in parallel during
the v1/v2 overlap period (Phases 6-7), so dashboards and audits keep
working. After Gate G7 cutover the v1 path stops writing; archived
records remain readable until Phase 8.

## Consequences

### Positive

- The duplicate-storyboard cascade described in
  [ADR-0004](0004-dual-ledger-supervisor.md) cannot happen any more,
  because the supervisor that *would* replan from scratch instead resumes
  from the last checkpoint with full ledger context.
- "User cancelled, then changed their mind" becomes a `aget_latest`
  + `supervisor.load_state` sequence rather than a re-kickoff.
- Long-running orgs survive process restarts. The first action after
  startup is `restore` for any non-terminal command.
- Debugging gets easier: a developer can `aget` any past checkpoint
  and inspect a frozen supervisor state.

### Negative / Accepted Cost

- Each inner-loop turn now writes ~10-50 KB to SQLite. With
  `recursion_limit` typically <30 turns, total volume stays small.
- Write contention on a busy command. Mitigation: WAL mode, single-
  writer per command (one supervisor per command, see ADR-0004).
- Schema evolution requires migration scripts. Mitigation: schema is
  versioned; we provide `scripts/migrate_checkpoints.py` per major
  version bump.

## Alternatives considered

1. **Reuse `OrgEventStore` as the checkpoint.** Rejected: events are
   append-only deltas; reconstructing supervisor state requires replaying
   from genesis, which is O(n) per resume. Snapshots are O(1).
2. **Memory-only checkpoint.** Rejected: process restart is one of the
   target recovery scenarios.
3. **Postgres-only.** Rejected for default: SQLite keeps single-binary
   deployments simple. Postgres remains a future backend if a user
   needs multi-process write fan-in.
4. **Per-second checkpointing.** Rejected: writes would dwarf actual
   work; per-turn is the right grain.

## References

- LangGraph `BaseCheckpointSaver`: `D:\claw-research\repos\langgraph\libs\checkpoint\langgraph\checkpoint\base.py`.
- LangGraph SQLite backend: `D:\claw-research\repos\langgraph\libs\checkpoint-sqlite\`.
- AutoGen save/load state: `D:\claw-research\repos\autogen\python\packages\autogen-agentchat\src\autogen_agentchat\teams\_group_chat\_magentic_one\_magentic_one_orchestrator.py` `save_state` / `load_state`.
- Brief: [`D:\claw-research\briefs\03-langgraph.md`](../../../claw-research/briefs/03-langgraph.md).
- Supervisor integration: [ADR-0004](0004-dual-ledger-supervisor.md).
