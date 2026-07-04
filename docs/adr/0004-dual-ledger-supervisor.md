# ADR-0004 — Dual-Ledger Supervisor

- **Status**: Accepted
- **Date**: 2026-05-18
- **Accepted**: 2026-05-19 (after P-RC-0..7 implementation review at G-RC-8)
- **Phase**: 0 (Spec Freeze)

## Context

The single most painful failure mode in the legacy runtime is the
**wall-clock timeout cascade**: `runtime_overrides.max_task_seconds` wraps
`agent.chat(...)` in `asyncio.wait_for`. When a long-running but
correctly progressing tool (e.g. `hh_storyboard_decompose` taking 4
minutes against DashScope) trips the timeout, the legacy code:

1. cancels the agent task,
2. marks the task as `CANCELLED` in the project store,
3. returns a `(节点 X 任务超时 ... 自动终止)` marker,
4. the parent producer node sees no deliverable, assumes failure,
5. **re-delegates the same task to the same node**, burning another 4
   minutes.

This is a defect of orchestration design, not of the timeout knob:
nothing in the legacy runtime can decide whether a long step is *making
progress* or is *stuck*. It only sees the clock.

The rest of the legacy stuck-detection (`_command_watchdog`,
`tracker.last_progress_at`) operates one layer above this — at the
user-command granularity. It cannot see *into* a node either; it only
counts seconds since the last `tracker._touch()`.

## Decision

We introduce a **dual-ledger supervisor**, modelled on AutoGen's
Magentic-One orchestrator. The supervisor is the only component that
decides when work is done, when to replan, and when to give up. It does
not decide based on the clock; it decides based on **LLM-evaluated
progress signals** plus a **hard turn cap**.

### Two ledgers

#### TaskLedger (outer loop)

A long-lived record of the user's intent and our plan to satisfy it.
Updated only at outer-loop boundaries.

```python
@dataclass
class TaskLedger:
    command_id: str            # links to UserCommand
    org_id: str
    root_node_id: str
    task: str                  # original user instruction
    facts: str                 # LLM-extracted relevant facts
    plan: str                  # LLM-drafted step-by-step plan
    revision: int = 0          # +1 on every replan
    created_at: datetime
    updated_at: datetime
```

#### ProgressLedger (inner loop)

A short-lived record produced by the LLM on **every inner-loop turn**.
Five required fields, each with both `answer` and `reason`:

```jsonc
{
  "is_request_satisfied":   {"answer": false, "reason": "..."},
  "is_progress_being_made": {"answer": true,  "reason": "..."},
  "is_in_loop":             {"answer": false, "reason": "..."},
  "instruction_or_question":{"answer": "...", "reason": "..."},
  "next_speaker":           {"answer": "art_director", "reason": "..."}
}
```

Parsing is strict; `runtime/ledger.py` retries up to 10 times on bad JSON
before falling through to a single hard error. We use the model's
`structured_output=True` mode when the provider supports it.

### Outer / inner loop

```
Outer Loop:
  1. brain.complete(ORCHESTRATOR_TASK_LEDGER_FACTS_PROMPT)         -> facts
  2. brain.complete(ORCHESTRATOR_TASK_LEDGER_PLAN_PROMPT)          -> plan
  3. publish TaskLedger to all participating nodes
  4. enter Inner Loop

Inner Loop (per turn):
  1. checkpoint.put(...)                              # save current state
  2. brain.complete(ORCHESTRATOR_PROGRESS_LEDGER)     -> ProgressLedger JSON
  3. stream.emit("progress_ledger", ...)              # to UI
  4. if is_request_satisfied            -> final answer, return
  5. update n_stalls (see ADR + below)
  6. if n_stalls >= max_stalls          -> Outer Loop replan, n_stalls = 0
  7. if n_turns >= max_turns            -> final answer with reason="max_turns"
  8. delegate next_speaker.instruction  -> wait for deliverable
  9. guardrail.run(deliverable)         -> apply or feed reason back
 10. continue
```

### Stall counter with regen

```python
if not progress.is_progress_being_made.answer:
    n_stalls += 1
elif progress.is_in_loop.answer:
    n_stalls += 1
else:
    n_stalls = max(0, n_stalls - 1)   # regen on real progress
```

This is the AutoGen Magentic-One regen pattern. It tolerates intermittent
plateaus (one slow turn does not trip the threshold) but reliably catches
sustained loops or no-progress sequences.

### What replaces wall-clock?

| Legacy concept | v2 replacement |
|---|---|
| `max_task_seconds` (per-node) | `max_stalls` (per-command, default 3) |
| `org_command_stuck_warn_secs` | informational only — supervisor emits stall warnings via `stream.emit("stall_warning", ...)` |
| `org_command_stuck_autostop_secs` | `max_turns` (per-command, default 30) |
| `_command_watchdog` (heuristic deadlock) | supervisor itself |
| `cancel_user_command(...)` | calls `supervisor.cancel()` which writes a final checkpoint, emits `stream.emit("cancelled", ...)`, then unblocks |

A coarse fallback `org_command_max_seconds` (default 30 minutes) remains
as a last-resort guardrail in case the supervisor itself hangs (e.g.
infinite tool loop inside a node). It is set high enough that no legitimate
user task should ever hit it.

### Per-command supervisor

Each `UserCommand` instantiates exactly one `Supervisor`. Supervisors do
not outlive their command; cross-command state (e.g. shared blackboard,
agent caches) lives outside the supervisor and is owned by the runtime
facade.

### State persistence

The supervisor's `save_state()` returns a `SupervisorState` payload that
is the heart of every checkpoint:

```python
@dataclass
class SupervisorState:
    task_ledger: TaskLedger
    progress_ledgers: list[ProgressLedger]    # bounded, default last 16
    n_stalls: int
    n_turns: int
    pending_writes: list[ChannelWrite]
    last_speaker: str | None
```

`load_state(state)` restores a supervisor at any checkpoint. See
[ADR-0005](0005-checkpoint-contract.md) for storage details.

## Consequences

### Positive

- The duplicate-storyboard regression class is eliminated by construction.
  Long but progressing tasks no longer trip a clock.
- The user can see `progress_ledger` events in the UI in real time, with
  the LLM's own reason text — the activity feed becomes a process view
  instead of a failure-only red banner.
- Replan replaces cancel-and-retry. When `max_stalls` trips, the
  supervisor *updates* facts and plan, broadcasts a new TaskLedger, and
  continues. No information is thrown away.
- Save/load state means cancel-and-resume works at no extra cost.

### Negative / Accepted Cost

- Each inner turn now spends one extra LLM call (the progress ledger).
  Mitigation: keep the progress ledger prompt short and use a cheaper
  model tier for it via `brain.complete(role="orchestrator")`.
- Bad JSON output from the model can stall the supervisor; we accept
  10 retries plus a final hard error path with a user-readable message.

## Alternatives considered

1. **Keep wall-clock, just raise the cap.** Rejected: the user
   explicitly rejected this in conversation ("只改超时实在不是治本的问
   题"). It also does not solve the duplicate-delegate cascade.
2. **External monitor process polling node state.** Rejected: in-process
   supervisor with a structured ledger is simpler and gives the LLM a
   first-class voice in detecting its own stalls.
3. **Single-ledger (progress only).** Rejected: without the
   `TaskLedger.facts` and `plan`, the replan path has nowhere to land
   when `max_stalls` trips. The outer-loop ledger is the recovery
   surface.
4. **CrewAI guardrail-only.** Rejected as the *sole* mechanism: guardrails
   help with deliverable quality (see [ADR-0008](0008-template-registry.md)
   and the guardrail runner) but do not detect mid-step loops; we use
   both.

## References

- AutoGen Magentic-One orchestrator: `D:\claw-research\repos\autogen\python\packages\autogen-agentchat\src\autogen_agentchat\teams\_group_chat\_magentic_one\_magentic_one_orchestrator.py`.
- Brief: [`D:\claw-research\briefs\06-autogen.md`](../../../claw-research/briefs/06-autogen.md).
- Failure timeline producing the requirement: prior conversation summary.
- Legacy command watchdog being replaced: [src/openakita/orgs/runtime.py](../../src/openakita/orgs/runtime.py) `_command_watchdog`.
- Checkpoint integration: [ADR-0005](0005-checkpoint-contract.md).
