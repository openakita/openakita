# ADR-0006 — Stream Channels Schema

- **Status**: Accepted
- **Date**: 2026-05-18
- **Accepted**: 2026-05-19 (after P-RC-0..7 implementation review at G-RC-8)
- **Phase**: 0 (Spec Freeze)

## Context

The legacy runtime emits WebSocket events through scattered
`_broadcast_ws("org:xxx", payload)` calls. Each call invents its own
shape; downstream consumers (the React app `OrgChatPanel.tsx`) match
event names with a tangle of `if/else` branches. The recent UX
regression where the activity feed showed only red `command timeout`
banners stems directly from this — there is no schema saying "every
node activation must emit a structured event".

What v2 needs is a **named, typed multi-channel stream**. Inspiration:
LangGraph's `stream_mode` parameter, where the same execution can be
observed at multiple granularities (`values`, `updates`, `tasks`,
`checkpoints`, `messages`, `debug`).

## Decision

### Channels

The supervisor and node runtime emit events on six named channels:

| Channel | Purpose | Frequency | Primary consumer |
|---|---|---|---|
| `values` | full state snapshot at end of each superstep | per superstep | UI summary, tests |
| `updates` | per-node delta writes within a superstep | per node finish | UI activity feed |
| `tasks` | "next we will execute X" announcements | per superstep start | UI tasks panel |
| `checkpoints` | checkpoint metadata when one is written | per checkpoint | UI checkpoint timeline |
| `messages` | LLM token / tool-call streams | per token batch | UI chat bubbles |
| `debug` | structured trace events for developers | best-effort | logs, dev tools |

Two additional supervisor-level channels supplement these:

| Channel | Purpose | Frequency |
|---|---|---|
| `progress_ledger` | the LLM's five-key progress evaluation each turn | per inner turn |
| `lifecycle` | `started` / `replanning` / `cancelled` / `done` etc. | per state transition |

### Event envelope

Every event uses the same outer envelope, regardless of channel:

```python
@dataclass(frozen=True)
class StreamEvent:
    channel: str               # one of the channel names above
    event_id: str              # ULID
    command_id: str
    org_id: str
    superstep: int             # current supervisor superstep when emitted
    emitted_at: datetime       # UTC
    type: str                  # channel-scoped event name
    payload: dict[str, Any]    # channel-scoped payload (validated by JSON schema)
    correlation_id: str | None # for request/response pairs
```

Channel-scoped event types are enumerated in `runtime/stream.py` and
documented inline. JSON schemas live next to the enum so both Python
producers and TypeScript consumers can validate.

#### `messages` envelope (LLM streaming)

Because LLM tokens arrive in tight bursts, `messages` events use a
slightly different payload shape with a token batch and a sequence id:

```python
{
  "type": "token_delta" | "tool_call_delta" | "thought_delta" | "final",
  "node_id": "art_director",
  "seq": 42,
  "delta": "...",        # text delta or structured tool-call delta
  "model": "qwen-max",
  "tokens": 17           # tokens in this delta (for budgeting)
}
```

Consumers can subscribe to a *subset* of channels rather than the full
firehose. The bus exposes:

```python
class StreamBus:
    def subscribe(self, *channels: str) -> AsyncIterator[StreamEvent]: ...
    async def emit(self, channel: str, type: str, payload: dict,
                   *, correlation_id: str | None = None) -> StreamEvent: ...
    async def emit_batch(self, events: list[tuple[str, str, dict]]) -> list[StreamEvent]: ...
```

### Backpressure

Subscribers are bounded queues (default 256). When full, the bus drops
the oldest event in that subscriber's queue and increments a counter
exposed on `debug`. Producers are never blocked. Slow UI consumers
therefore lose the *oldest* event, not the most recent — important for a
real-time feed.

### Mapping to today's WebSocket and SSE

Phase 6 maps StreamBus events to the existing WebSocket frame format used
by `OrgChatPanel.tsx`:

```
WebSocket frame: { "topic": "org:<channel>", "data": StreamEvent.payload, ... }
```

This preserves the front-end contract. After cutover (Phase 7) the
front-end can subscribe per-channel directly, but legacy single-topic
clients keep working.

## Consequences

### Positive

- The activity feed bug class is gone: every node activation is required
  to emit `updates` events with a typed payload; the UI cannot fall
  through to a generic red banner because a typed event always exists.
- Multi-pane UI views become trivial: the "checkpoint timeline" panel
  subscribes to `checkpoints`; the "progress ledger" panel subscribes
  to `progress_ledger`; both can run independently of the chat feed.
- Tests assert on a typed event stream rather than on string-matching
  WebSocket payloads.

### Negative / Accepted Cost

- Six channels mean six (or eight) JSON schemas to maintain. We accept
  this; the schemas are tiny (each <50 lines).
- Backpressure drops events under load. We document this and add a
  `dropped_count` counter to `debug` so the user can see it happening.

## Alternatives considered

1. **Single-topic event stream like today.** Rejected: producing the bug
   we are solving.
2. **Pub/sub on Redis.** Rejected: in-process pub/sub suffices and keeps
   single-binary deployments simple.
3. **OpenTelemetry only.** Rejected as the *primary* stream: OTel is
   excellent for tracing/metrics but its consumer model is wrong for a
   live UI. We can still emit OTel spans alongside; that is a future
   ADR.

## References

- LangGraph stream modes: `D:\claw-research\repos\langgraph\libs\langgraph\langgraph\stream\`.
- Brief: [`D:\claw-research\briefs\03-langgraph.md`](../../../claw-research/briefs/03-langgraph.md).
- Cortex telemetry (informative): `D:\claw-research\repos\cortex\lib\cortex\telemetry.ex`.
- AutoGen output_message_queue (informative): `D:\claw-research\repos\autogen\python\packages\autogen-agentchat\src\autogen_agentchat\teams\_group_chat\_base_group_chat.py`.
- Frontend consumer: [apps/setup-center/src/components/OrgChatPanel.tsx](../../apps/setup-center/src/components/OrgChatPanel.tsx).
