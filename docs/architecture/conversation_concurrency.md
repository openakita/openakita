# Conversation Concurrency Architecture

> Onboarding doc for the v1.28 concurrency stack.
> Last updated: v1.28.3-pre (S5-A + audit fixes + S5-B prerequisite syntax guard).
>
> For the change history and per-stage rationale, see
> [`release-notes/v1.28.md`](../release-notes/v1.28.md).
> For the original design plan and trade-off discussions, see
> `.cursor/plans/conversation_concurrency_v1.28_c8e4f1d2.plan.md`.

## TL;DR

OpenAkita's concurrency story is built on **five rules** distilled from
five reference projects (claude-code, langchain, openclaw,
hermes-agent, qwenpaw + Sub2API). Every line of the v1.28 stack
exists to enforce one of them:

1. **Per-conversation single-flight** at the HTTP/Channel entry —
   one `conversation_id` runs one agent turn at a time. Race
   conditions like the `completed -> reasoning` crash in
   [issue #572](https://github.com/openakita/openakita/issues/572)
   are made architecturally impossible at the entry layer rather
   than patched at the state-machine layer.
2. **Double-texting policy is explicit** — when a user fires a
   second message before the first turn finishes, the system MUST
   pick one of four behaviours (REJECT / QUEUE / INTERRUPT / STEER)
   and the user-facing channel determines which.
3. **Run state and Session state are separated** — `TaskState`
   carries the per-call ephemeral context (status, abort scope,
   in-flight tools, partial text); `Session` carries the
   persistent conversation context (messages, metadata, memory).
   Cancelling a run cleans the run, not the conversation.
4. **Cancelled turns must synthesize tool results** before the
   next turn dispatches — orphan `tool_use` blocks crash Anthropic
   API with `400 tool_use without tool_result`. The next turn
   inserts a synthetic `tool_result` for every orphan immediately
   after the originating `assistant` message.
5. **Every tool declares whether it can be interrupted** —
   `interrupt_behavior ∈ {"cancel", "block"}`. Block-class tools
   (write_file, shell, browser, MCP) cannot be safely killed
   mid-execution; INTERRUPT policy auto-downgrades to QUEUE when
   one is in-flight.

If any one of these rules is missing, the others can't enforce
correctness. v1.27.x had #2 and #4 partial; v1.28 closes all five.

## Where each rule lives in code

```text
src/openakita/
├── api/
│   └── routes/
│       ├── conversation_lifecycle.py  # Rule #1: per-conv single-flight (entry lock)
│       ├── chat.py                    # Rule #2: DoubleTextingPolicy dispatch
│       └── double_texting.py          # Rule #2: Policy enum + per-channel map
├── core/
│   ├── agent.py                       # Rule #2: _preempt_or_queue_prev_task
│   ├── agent_state.py                 # Rule #3+#5: TaskState lifecycle + abort tree
│   ├── reasoning_engine.py            # Rule #3: ensure_ready_for_reasoning entry
│   ├── tool_executor.py               # Rule #4+#5: orphan synth + in-flight tracking
│   ├── tool_interrupt_behavior.py     # Rule #5: central interrupt registry
│   ├── cancel_cleanup.py              # Rule #4: synthesize_tool_results_for_orphans
│   ├── sse_replay.py                  # Rule #3: SSE ringbuffer (run-state replay)
│   ├── sse_throttle.py                # Rule #3: DeltaCoalescer (S1+ P0-2)
│   ├── conversation_metrics.py        # observability counters for all 5 rules
│   └── policy_v2/                     # (orthogonal — content safety, not concurrency)
└── sessions/
    └── user.py                        # Rule #3: Session != TaskState boundary
```

## How an agent turn actually runs (data flow)

```text
                      ┌─────────────────────────────────────┐
   user message ─────▶│ HTTP / IM channel                    │
                      │   • turn_id pinned                   │
                      │   • DoubleTextingPolicy dispatch     │ (Rule #2)
                      └─────────────────┬───────────────────┘
                                        │
                      ┌─────────────────▼───────────────────┐
                      │ ConversationLifecycleManager.start() │
                      │   • per-conv lock (Rule #1)          │
                      │   • REJECT / QUEUE / INTERRUPT /     │
                      │     STEER branch                     │
                      └─────────────────┬───────────────────┘
                                        │
                      ┌─────────────────▼───────────────────┐
                      │ Agent.chat_with_session{_stream}     │
                      │   • _preempt_or_queue_prev_task      │ (Rule #5 in-flight check
                      │   • begin_task → fresh TaskState     │  for INTERRUPT downgrade)
                      └─────────────────┬───────────────────┘
                                        │
                      ┌─────────────────▼───────────────────┐
                      │ ReasoningEngine._reason_stream_impl  │
                      │   • ensure_ready_for_reasoning       │ (Rule #3 contract)
                      │   • iterate: REASONING → ACTING →    │
                      │     OBSERVING → REASONING …          │
                      └────────┬─────────────────────┬──────┘
                               │ tool call           │
                               ▼                     │
                      ┌────────────────────┐         │
                      │ ToolExecutor       │         │
                      │   • begin_tool()   │ (Rule #5)│
                      │   • AbortScope     │ (Rule #3)│
                      │   • end_tool()     │         │
                      └────────┬───────────┘         │
                               │ result              │
                               └─────────────────────┘

   on cancel / preempt:
                      ┌─────────────────────────────────────┐
                      │ TaskState.cancel()                   │
                      │   • abort_scope.event.set()          │ (Rule #3 tree)
                      │   • children abort fan-out           │
                      │   • mark_settled()                   │
                      └─────────────────────────────────────┘

   next turn entry:
                      ┌─────────────────────────────────────┐
                      │ synthesize_tool_results_for_orphans  │ (Rule #4)
                      │   • scan working_messages            │
                      │   • insert synth tool_result blocks  │
                      └─────────────────────────────────────┘
```

## The state machine

```text
   IDLE ──┬─▶ COMPILING ────┐
          │                  │
          └────────────────▶ REASONING ◀──────────────┐
                              │  ▲                    │
                              ▼  │                    │
                            ACTING │                  │
                              │    │                  │
                              ▼    │                  │
                            OBSERVING ────────────────┤
                              │                       │
                              ▼                       │
                            VERIFYING ────────────────┤
                              │                       │
                              ▼                       │
                            WAITING_USER ─────────────┤
                              │  ▲                    │
                              ▼  │                    │
                            (any non-terminal) ◀── MODEL_SWITCHING
                              │                       ▲
                              ▼                       │
                            COMPLETED ─┐              │
                            FAILED ────┤              │
                            CANCELLED  │              │
                              │        │              │
                              ▼        ▼              │
                          (IDLE again via begin_task) │
                                                      │
   cancel() from ANY state  ────────────────────▶  CANCELLED
   _handle_llm_error from non-terminal  ──────▶  MODEL_SWITCHING
```

Source of truth: `_VALID_TRANSITIONS` in `core/agent_state.py`.
A few quirks worth knowing:

* **Terminal states are NOT dead ends.** `COMPLETED → IDLE`,
  `FAILED → IDLE`, `CANCELLED → IDLE` are all legal (and used by
  `AgentState.begin_task()` to reuse a TaskState slot for the
  next turn). Terminal states also retain `→ CANCELLED` so
  `cancel()` is idempotent — that's the *one* permanent
  force-write (see `agent_state.py` `# cancel-idempotent-force-write`).
* **`REASONING → REASONING` is allowed** as a recovery edge — if a
  preempted task is observed in `ACTING` when a new message arrives,
  the new turn can re-enter REASONING via `ensure_ready_for_reasoning()`
  without bouncing through IDLE.
* **`COMPILING`** is the optional prompt-compilation phase before
  REASONING (some agent profiles do template assembly outside the
  reasoning loop). Most turns go `IDLE → REASONING` directly.
* **The `is_terminal` property** still only returns True for
  COMPLETED / FAILED / CANCELLED — it's the test
  `ensure_ready_for_reasoning()` uses, not "no outbound edges".

### `ensure_ready_for_reasoning()` — the v1.28.3 contract

Re-entering REASONING is a recurring pattern (main-loop iteration,
post-tool-loop, post-observation, post-verify). Pre-S5-A this used
to be `state.transition(REASONING); except ValueError: pass / state.status = REASONING`,
a hot-fix from [`06c67221`](../release-notes/v1.28.md#stage-5-a--state-machine-contract-helper-v1283-pre)
that papered over the issue #572 race. Post-S5-A:

```python
try:
    state.ensure_ready_for_reasoning()
except IllegalReasoningEntry:
    # State is terminal → race past S1's single-flight guard.
    # Emit pager-alert telemetry + structured SSE error.
    inc_illegal_reasoning_entry(source="reason_stream_iter")
    yield {"type": "error", "code": "illegal_state", "message": ...}
    yield {"type": "done"}
    return
except ValueError:  # s5b-allow-force-write
    # Belt-and-suspenders: non-terminal-illegal-source.  The
    # _VALID_TRANSITIONS table makes this unreachable in practice
    # (every non-terminal status has REASONING in its target set —
    # pinned by test_every_non_terminal_status_can_reach_reasoning).
    # S5-B will delete this after 2 weeks of zero telemetry hits.
    state.status = TaskStatus.REASONING
```

`ensure_ready_for_reasoning()` semantics:

| Source state | Action |
|---|---|
| `REASONING` | no-op (idempotent) |
| `IDLE` / `COMPILING` / `ACTING` / `OBSERVING` / `VERIFYING` / `WAITING_USER` / `MODEL_SWITCHING` | `transition(REASONING)` |
| `COMPLETED` / `FAILED` / `CANCELLED` | raise `IllegalReasoningEntry` |

The exception type matters: `IllegalReasoningEntry` is a typed signal
that the caller MUST handle (with telemetry + user-facing error),
while raw `ValueError` from `transition()` should bubble up to the
outer `_reason_stream_impl` try as a generic concurrency bug.

## AbortScope tree (Rule #3, cancel hygiene)

Pre-v1.28.1 cancellation was a single `asyncio.Event` on `TaskState`.
That handles "user clicks stop" but not "user starts a new turn while
a sub-agent is still running a tool". Post-S3:

```text
   TaskState.abort_root            (root)
   ├── tool_executor child         (per-tool create_child)
   ├── sub-agent child             (delegated turn attaches via outer wrapper)
   │   └── tool_executor child
   └── …

   abort(reason) on any node:
     • set self.event
     • walk children synchronously, abort each with _from=root.name
     • Settled-event chain ensures cleanup runs before next turn starts
```

Implementation details:

- `current_abort_scope: ContextVar[AbortScope]` carries the scope
  across `asyncio.create_task` boundaries (contextvar is shallow-copied
  per spawn — verified by `test_abort_scope_crosses_create_task`).
- `cancel_event` on `TaskState` is a back-compat property alias for
  `abort_root.event` — no double-tracking.
- `synthesize_tool_results_for_orphans` runs at **the next turn entry**
  (not at cancel exit) — simpler, deterministic, and avoids persistent
  "synthetic queue" files. The synth block is inserted immediately
  after the originating `assistant` message, not at the tail, so a
  user "continue" doesn't insert content mid-stream and re-trigger
  Anthropic 400.

## Double-texting policy (Rule #2)

When a user message arrives while a turn is still running for the
same `conversation_id`:

| Policy | Behaviour | Default channels |
|---|---|---|
| `REJECT` | 409 Conflict + Retry-After. Old turn keeps running. | wechat, cross-client (different `client_id`) |
| `QUEUE` | Hold new turn until old turn settles. Keepalive SSE pings every 5s. | desktop, cli, telegram, feishu, dingtalk, wework, qqbot, onebot |
| `INTERRUPT` | Cancel old turn, start new. Auto-downgrades to QUEUE if block-class tools are in-flight. | (opt-in via header, default off in v1.28.2) |
| `STEER` | Inject new user message as a follow-up to the running turn (no cancel). | (planned, plan §1.4 P1) |

Per-conversation override: clients can send
`X-Conversation-Policy: interrupt` to opt in to INTERRUPT for a
single request. The auto-downgrade to QUEUE still applies if
block-class tools are running — the header is "try INTERRUPT" not
"force-cancel-no-matter-what". See FOLLOW-UP-S4-C for the deferred
force-cancel escape hatch discussion.

### INTERRUPT auto-downgrade decision

`_preempt_or_queue_prev_task` in `core/agent.py` checks the
previous task's `in_flight_tools` list:

```python
in_flight = prev_task.get_in_flight_tools()
if has_any_block_in_flight(in_flight, mcp_client=self.mcp_client):
    inc_interrupt_downgrade(channel=..., reason="block_in_flight")
    policy = DoubleTextingPolicy.QUEUE  # downgrade
elif any(tool not in REGISTERED_TOOLS for tool in in_flight):
    inc_interrupt_downgrade(channel=..., reason="unknown_tool")
    policy = DoubleTextingPolicy.QUEUE
# else: keep INTERRUPT
```

`has_any_block_in_flight` understands MCP encoding —
`mcp:server:sub_tool` entries resolve to the server's MCP
annotations (`interruptBehavior: "cancel" | "block"`) when present.
Built-in classifications always win over MCP annotations (an MCP
server can't downgrade built-in `write_file` to cancel).

### QUEUE timeout extension

QUEUE waits up to `preempt_settle_timeout_ms` (default 30s) for
the previous task to settle. If a block-class tool is still
in-flight at the deadline, the wait is extended by
`preempt_block_tool_extension_ms` (default 24s) before finally
cancelling. The extension is opt-in via `inc_queue_extended`
counter — see telemetry catalog below.

## Tool interrupt behavior (Rule #5)

Central registry at `core/tool_interrupt_behavior.py`. The map itself
is a module-private `_INTERRUPT_BEHAVIOR_MAP`; do NOT import it directly
— use the public helper functions:

```python
# Public API (callers should use these):
from openakita.core.tool_interrupt_behavior import (
    get_tool_interrupt_behavior,      # primary query: str → "cancel" | "block"
    has_any_block_in_flight,          # mcp-aware aggregate check
    resolve_mcp_tool_behavior,        # explicit MCP annotation path
    encode_mcp_sub_tool,              # build "mcp:server:sub_tool" key
    parse_mcp_sub_tool,               # inverse
    known_tools,                      # frozenset of all classified names
    is_unknown_tool,                  # for telemetry / unclassified warnings
)

# Inside the registry (private):
_INTERRUPT_BEHAVIOR_MAP: dict[str, InterruptBehavior] = {
    # cancel-class: safe to kill mid-flight
    "ask_user": "cancel",
    "web_search": "cancel",
    ...
    # block-class: kill causes data corruption or orphan resources
    "write_file": "block",
    "run_shell": "block",
    "browser_click": "block",
    "memory_save": "block",
    "call_mcp_tool": "block",  # conservative default; MCP annotations refine
    ...
}
```

**139 built-in tools** are explicitly classified (70 block + 69
cancel as of v1.28.3-pre — verify with
`python -c "from openakita.core.tool_interrupt_behavior import _INTERRUPT_BEHAVIOR_MAP; print(len(_INTERRUPT_BEHAVIOR_MAP))"`).
The `tests/unit/test_tool_interrupt_behavior_completeness.py` AST
scanner fails any tool definition lacking explicit classification.

MCP tools follow the encoding `mcp:<server>:<sub_tool>`. When
resolving in-flight behavior, the order in
`resolve_in_flight_behavior()` is:

1. If the full key (`mcp:server:sub_tool`) is in
   `_INTERRUPT_BEHAVIOR_MAP`, use it (allows per-sub-tool
   overrides for the rare case a hostile MCP server lies in
   annotations).
2. Else, ask the MCP client for the server's tool annotations
   (`mcp_annotations.interruptBehavior`) via `resolve_mcp_tool_behavior`.
3. Else, fall back to `block` (safety-by-default).

Built-in classifications **always win** over MCP annotations. A
malicious / buggy MCP server can't downgrade built-in `write_file`
to "cancel" — that's a deliberate safety boundary.

## Telemetry catalog

All counters live in `core/conversation_metrics.py`. The table
below lists the **stored counter name** (what `snapshot()` returns
in the `"name"` field) and not the `inc_*()` Python function name —
the analyzer script matches on stored name:

| Stored name | Inc fn | Labels | Fires when |
|---|---|---|---|
| `preempt` | `inc_preempt` | `policy`, `channel` | INTERRUPT/STEER abruptly takes the slot from a prior task |
| `queue` | `inc_queue` | `channel` | QUEUE policy held a new turn |
| `settled_timeout` | `inc_settled_timeout` | `policy`, `channel` | QUEUE waited past `preempt_settle_timeout_ms` |
| `abandon` | `inc_abandon` | `policy`, `channel` | Preempted task abandoned its writes after settle timeout |
| `takeover` | `inc_takeover` | `channel` | HTTP `start()` returned `took_over` (lock transfer) |
| `interrupt_downgrade` | `inc_interrupt_downgrade` | `channel`, `reason ∈ {block_in_flight, unknown_tool}` | INTERRUPT downgraded to QUEUE |
| `queue_extended` | `inc_queue_extended` | `channel`, `reason ∈ {block_in_flight, unknown_tool}` | QUEUE timeout extended (block tool still in-flight) |
| `illegal_reasoning_entry` | `inc_illegal_reasoning_entry` | `source ∈ {reason_stream_iter, reason_stream_outer, run_impl_main_loop, run_impl_ask_user_reply, run_impl_ask_user_timeout}` | Reasoning entry hit a terminal state — race past S1 |

### `inc_illegal_reasoning_entry` source labels — why five?

Each label pinpoints **where** the race was caught. S5-B's gating
gate ("2 weeks of zero hits") would have been vacuously met by the
SSE-only telemetry shipped in S5-A's first version — IM/CLI users
go through `_run_impl`, not `_reason_stream_impl`. FIX-S5A-1 + FIX-S5A-2
added the four extra labels to cover all entry points:

| Label | Entry point | Channel |
|---|---|---|
| `reason_stream_iter` | SSE main loop inner catch | desktop, web |
| `reason_stream_outer` | SSE outer defensive net | desktop, web (rare) |
| `run_impl_main_loop` | IM/CLI main loop top | telegram, feishu, etc. |
| `run_impl_ask_user_reply` | IM/CLI continue after user reply | telegram, feishu, etc. |
| `run_impl_ask_user_timeout` | IM/CLI continue after ask_user timeout | telegram, feishu, etc. |

A non-zero count on ANY label means S1's per-conversation
single-flight was bypassed in production. Investigate the label's
specific code path before shipping S5-B.

## Invariants and CI guards

The test suite enforces architectural invariants that prose docs
can't sustain:

### `tests/unit/test_no_force_write_state_transitions.py`

AST-based syntax guard pinning the population of
`except ValueError: state.status = X` force-writes at exactly
**9** in `reasoning_engine.py` (S5-B backlog) + **1** in
`agent_state.py` (`TaskState.cancel()` architectural idempotent).

Two token kinds:

- `# s5b-allow-force-write` — temporary, S5-B deletes after 2-week
  telemetry confirms unreachability.
- `# cancel-idempotent-force-write` — permanent, cancel() MUST
  succeed from any prior state.

A new `state.status = X` after `except ValueError` without an
opt-in token within ±5 lines fails the test.

### `tests/unit/test_reason_stream_state_race.py`

Pins the `ensure_ready_for_reasoning` contract:

- Idempotent on REASONING source.
- Terminal → raises `IllegalReasoningEntry`.
- Every non-terminal source has REASONING in its `_VALID_TRANSITIONS` set.
- `inc_illegal_reasoning_entry` source labels are unique (5 of them).
- `_reason_stream_impl` outer try-ladder lists `IllegalReasoningEntry`
  before generic `Exception`.
- `_run_impl` hot-fix sites use the correct counter labels guarded by
  `if state.is_terminal:`.

### `tests/unit/test_tool_interrupt_behavior_completeness.py`

AST-scans `core/tools/definitions/*.py` and asserts every tool has
an explicit `interrupt_behavior` annotation. New tool without
classification → test fail with the file/lineno of the offending
tool.

### `tests/integration/test_interrupt_downgrade.py`

Behavioural test of the INTERRUPT auto-downgrade decision +
QUEUE timeout extension + MCP sub-tool resolution.

### `tests/integration/test_cancel_cleanup.py`

End-to-end coverage of AbortScope tree, orphan tool_use synthesis,
and back-compat of legacy `cancel_event` readers.

## Deferred work and trigger conditions

Some work is intentionally not done in v1.28.3-pre.  Each deferral
has a concrete trigger:

### v1.28.2.1 — desktop INTERRUPT default

**Trigger**: v1.28.2 (S4) shipped + 1 week of `inc_interrupt_downgrade` telemetry showing:

- `downgrade_rate < 5%` (less than 1 in 20 INTERRUPT attempts hit block tools)
- `abandon_rate < 1%` (preempted writes don't leave inconsistent state)

When the trigger is met, flip `double_texting_per_channel["desktop"]`
from `"queue"` to `"interrupt"` — that's the only code change.
See `scripts/concurrency_telemetry_analyzer.py` for the verdict
machine.

### v1.28.3 / S5-B — delete 20 historical safety nets

**Trigger**: 2 weeks of zero hits on `inc_illegal_reasoning_entry`
across ALL 5 source labels AND `preempt + queue` traffic above
`S5B_MIN_TRAFFIC_FLOOR` (analyzer enforces both).

When the trigger is met:

1. Introduce `IllegalStateTransition(RuntimeError)` in `agent_state.py`;
   keep `IllegalReasoningEntry` as a subclass so all existing
   catch sites keep working.
2. Delete 8 `except ValueError: state.status = X` blocks in
   `reasoning_engine.py` (lines tagged `# s5b-allow-force-write`)
   — the 9th site (MODEL_SWITCHING) is *reclassified* as
   architectural-permanent (analogous to `cancel-idempotent`).
3. Delete 8 pure-pass `except ValueError: pass` blocks at
   non-reasoning transition points in `_run_impl`.
4. Refactor 3 `except ValueError:` blocks with FIX-S5A-2
   telemetry wiring (`run_impl_main_loop` /
   `run_impl_ask_user_reply` / `run_impl_ask_user_timeout`) to
   `except IllegalStateTransition: ...; raise` — preserves the
   `inc_illegal_reasoning_entry` counter while letting the
   exception propagate (no more silent loop continue).
5. Update `S5B_BACKLOG_FILES[reasoning_engine.py]` in the syntax
   guard test from 9 to 0 (or 1 if MODEL_SWITCHING is reclassified
   under a new `model-switch-idempotent-force-write` token).
6. Update `test_each_known_force_write_target_is_present`
   parametrize list — remove deleted TaskStatus targets.

See [`docs/architecture/s5b_checklist.md`](./s5b_checklist.md) for
the line-by-line implementation guide, including the 3 telemetry
refactors that are easy to mis-execute as blind deletes.

### S2 — RunContext per-call (long-term debt)

`ReasoningEngine` keeps 11 mutable instance attributes per turn
(`_last_delivery_receipts`, `_max_iterations_override`, etc.). For
multi-conversation concurrent runs in the same process, these
attributes race. S2 was originally a hard pre-requisite for S3 and
S5; in practice S3+S4+S5-A all bypass this dependency
(`AbortScope` lives on `TaskState`, not on a new RunContext).

S2 remains pending as long-term debt. **Not blocked on anything,
not blocking anything.** Trigger: user reports cross-conversation
state corruption, or someone has 2 days for a focused refactor.

### S6 — multi-tab SSE fan-out

Single-tab reconnect was absorbed by S1+ P0-1 (SSE ringbuffer).
Multi-tab fan-out (same user opens the same conversation in two
tabs and both see live events) is product-feature-driven, not
correctness-driven. Trigger: product requirement.

### FOLLOW-UP-S4-C — force-cancel escape hatch

For "power user has only read-only MCP tools and is annoyed by
INTERRUPT auto-downgrade to QUEUE" — would add
`double_texting_force_cancel: bool = False` config that bypasses
in-flight checks. Deferred because:

1. INTERRUPT header + S4-A/B make INTERRUPT viable for most cases.
2. Power-user pain isn't strong enough to justify the config surface.

Trigger: user feedback explicitly requesting force-cancel
behaviour after 1-2 weeks of v1.28.2 production.

## Failure modes and playbook

### "tool_use without tool_result" 400 from Anthropic

**Diagnosis**: Orphan tool_use blocks in `working_messages` after a
mid-tool cancel.

**Fix**: `synthesize_tool_results_for_orphans` at next turn entry
covers this. If still seen, check that the cancel path actually
calls `TaskState.cancel()` (not `state.status = CANCELLED` direct
write — that path skips the abort tree fan-out).

### `inc_illegal_reasoning_entry` non-zero in production

**Diagnosis**: S1's per-conversation single-flight was bypassed.
The label tells you the entry point.

**Investigate**: Is `ConversationLifecycleManager.start()` actually
running before the offending request? Check for direct calls into
`Agent.chat_with_session*` from anywhere other than `api/routes/chat.py`.

**Don't**: Just add another force-write to silence the error.
S5-A's whole point is that this signal is now visible — listen to it.

### Frontend shows "上一条消息正在收尾" but task seems finished

**Diagnosis**: The race actually happened, S5-A caught it, the
typed error event is what the user sees. The previous task likely
hit a non-S1-path entry (a bug to fix).

**Investigate**: Same as above — `inc_illegal_reasoning_entry` label
identifies the code path.

### INTERRUPT requests always end up as QUEUE

**Diagnosis**: A tool you didn't expect is in-flight and classified
as block, OR an unknown tool is in-flight (registry drift).

**Investigate**:
- Read the `interrupt_downgrade` counter's labels — `reason=block_in_flight`
  means correct downgrade, `reason=unknown_tool` means classification gap.
- For `unknown_tool`, add the tool name to `_INTERRUPT_BEHAVIOR_MAP`
  in `core/tool_interrupt_behavior.py` (the test
  `test_tool_interrupt_behavior_completeness` will fail until the
  classification is added).

## History

| Version | What changed | Why |
|---|---|---|
| v1.27.12 | (issue #572) `completed -> reasoning` crash exposed | (root cause analysis) |
| v1.27.13 | Hot-fix `06c67221`: `try transition() except ValueError: pass / state.status = X` | Stop the crash; ship while plan unfolds |
| v1.27.14 | S1: per-conv single-flight + DoubleTextingPolicy enum (default all QUEUE) | Rule #1 + #2 |
| v1.27.15 | S1+: 8 user-pain fixes (SSE resume, keepalive ping, partial text, STEER, soft-kill shell, …) + 6 audit fixes | Industry pattern absorption |
| v1.28.1 | S3: AbortScope tree + orphan tool_use synth + sub-agent attach | Rule #3 + #4 |
| v1.28.2 | S4: 139 tools classified + INTERRUPT auto-downgrade + QUEUE extension + MCP sub-tool resolve | Rule #5 |
| v1.28.3-pre | S5-A: `IllegalReasoningEntry` + `ensure_ready_for_reasoning` + 5 telemetry labels + syntax guard | Rule #3 contract typed |
| v1.28.2.1 | desktop default → INTERRUPT (pending telemetry) | S4 user-visible win |
| v1.28.3 | S5-B: delete 8+8 force-writes/silent-pass + refactor 3 telemetry sites (pending telemetry) | Tech debt cleanup |
| v1.28.0 | S2: RunContext per-call (pending, breaking) | Multi-conv hygiene |
| v1.28.x | S6: multi-tab SSE fan-out (pending, product-driven) | UX enhancement |

## Further reading

- Release notes: [`docs/release-notes/v1.28.md`](../release-notes/v1.28.md)
- Original plan + revisions: `.cursor/plans/conversation_concurrency_v1.28_c8e4f1d2.plan.md`
- S5-B implementation guide: [`docs/architecture/s5b_checklist.md`](./s5b_checklist.md)
- Telemetry analyzer: `scripts/concurrency_telemetry_analyzer.py`
- Reference projects (informed the 5 rules):
  - claude-code — interruptBehavior + cancel-after-synth
  - langchain — RunContext + ThreadId separation
  - openclaw — `persistAbortedPartials` + `emitChatDelta` throttle
  - hermes-agent — DoubleTextingPolicy 4 strategies + `_drain_pending_steer`
  - qwenpaw + Sub2API — TaskTracker resume + gateway-helper queue keepalive
