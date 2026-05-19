# ADR-0001 — Fork-Style Rewrite Policy

- **Status**: Accepted
- **Date**: 2026-05-18
- **Accepted**: 2026-05-19 (after P-RC-0..7 implementation review at G-RC-8)
- **Phase**: 0 (Spec Freeze)
- **Decision owner**: project owner
- **Implementer**: AI agent on `revamp/v2`

## Context

The legacy backend has reached the limits of incremental refactoring. Two
files alone account for more than sixteen thousand lines:

- `src/openakita/core/agent.py` — 8 433 lines.
- `src/openakita/core/reasoning_engine.py` — 7 987 lines.

`src/openakita/orgs/runtime.py` is 5 734 lines and concentrates lifecycle
management, agent activation, scheduling, message dispatch, watchdogs, plugin
asset capture, file output recording, and quota handling in a single class.
Comparable open-source frameworks (LangGraph, AutoGen agentchat, CrewAI) carry
similar responsibilities in well-factored modules of one tenth the size.

Patch-on-patch maintenance has produced repeated regressions: wall-clock
timeouts that cancel correct work (`max_task_seconds`), parents that
re-delegate cancelled subtasks because no checkpoint was saved, command
watchdog logic that cannot see *into* a node, and a UI activity feed that
shows only failure markers. Each of these has its own root cause, but the
ultimate cause is structural: the legacy code does not have first-class
abstractions for state graph, checkpoint, progress signal, or plugin
workbench.

The project owner has authorised an aggressive revamp. The scope (per
[selection record in the plan](../../../claw-research/OPENAKITA_REVAMP.md))
covers `src/openakita/orgs/` and most of `src/openakita/core/`, with a fresh
data start and a 30-week implementation budget.

## Decision

We will **rewrite, not refactor**, the affected packages, using a *fork-style*
strategy:

1. New code is written under fresh package paths
   (`src/openakita/runtime/`, `src/openakita/agent/`) so the legacy paths keep
   running unchanged during the rewrite.
2. A feature flag `runtime_v2_enabled` (default `false` until cutover)
   controls per-org routing between legacy and v2 runtimes.
3. A parity harness under `tests/parity/` runs identical inputs through
   legacy and v2 components to anchor v2 behaviour against today's
   observable behaviour.
4. Cutover is one config flip after Gate G7 burn-in. No big-bang merge.
5. Legacy code is deleted *after* cutover, in mechanical commits, in
   Phase 8.

We explicitly reject the "patch-on-patch" alternative of layering new
abstractions on top of `OrgRuntime` and `Agent` without removing the legacy
internals.

## Consequences

### Positive

- Old code keeps working until v2 is proven; the project never has a "broken
  trunk" window.
- Each Phase boundary is a working software milestone. The owner can stop the
  rewrite at any gate and ship what exists.
- Rollback is a flag flip, not a `git revert`.
- The new code can take the shape that the problem now demands, instead of
  inheriting the shape of accidental complexity.

### Negative / Accepted Cost

- Two implementations live side-by-side for ~25 weeks. Disk and CI cost
  roughly double during the overlap.
- Plugin authors who reach into `core.Agent` internals will need to migrate
  their imports at cutover. We mitigate this by keeping the public
  `Agent` import path stable through a facade module (see
  [ADR-0003](0003-agent-architecture.md)).
- The parity harness adds ~30 test cases that must be authored in Phase 2.
  We accept this as the price of confidence.

## Alternatives considered

1. **Incremental refactor in place.** Rejected: the legacy files have
   organic coupling (e.g. `OrgRuntime._run_agent_task` reaches into
   `agent.reasoning_engine._max_iterations_override`). Incremental work
   would still produce a 5 000-line `OrgRuntime`.
2. **Big-bang rewrite, then merge.** Rejected: a single merge of a 30-week
   rewrite carries unbounded regression risk. Phase boundaries protect us.
3. **Adopt LangGraph or AutoGen as the engine.** Rejected at this layer: the
   identity / persona / memory / channel surfaces are OpenAkita-specific and
   the project owner wants to retain that surface. We *borrow design* from
   those frameworks (see [ADR-0002](0002-runtime-architecture.md) and
   [ADR-0004](0004-dual-ledger-supervisor.md)) without taking a hard runtime
   dependency.

## Review gate

Gate **G0** signs off this ADR set as a whole. After G0 the `Status` lines
on every ADR are updated to `Accepted` in a single follow-up commit, and
Phase 1 begins.

## References

- Comparison and rationale: `D:\claw-research\COMPARISON.md`,
  `D:\claw-research\OPENAKITA_REVAMP.md`.
- Legacy source under audit: [src/openakita/orgs/runtime.py](../../src/openakita/orgs/runtime.py),
  [src/openakita/core/agent.py](../../src/openakita/core/agent.py),
  [src/openakita/core/reasoning_engine.py](../../src/openakita/core/reasoning_engine.py).
