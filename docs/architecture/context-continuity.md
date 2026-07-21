# Context continuity

OpenAkita keeps long-term memory and conversation compaction as separate systems. Long-term
memory stores reusable facts and experience. Context continuity preserves the exact execution
projection needed to resume one conversation after compaction or process restart.

## Durable records

SQLite schema version 6 adds four records:

- `compaction_checkpoints`: started, completed, and failed compaction attempts. A completed row
  contains the anchored summary, recent tail, final model-visible projection, source digests,
  token counts, context epoch, contributor output, and workspace snapshot reference.
- `context_epochs`: hashes of the system prompt, effective tool schemas, and model identity.
- `tool_output_blobs`: zlib-compressed full tool results removed from the active model context.
  Blob identifiers include the session identity in their digest and reads through
  `MemoryManager` are restricted to the active session.
- `workspace_snapshots`: Git HEAD, changed paths, status digest, a bounded compressed patch, and
  an explicit capture status/error. Git absence, non-repositories, and command failures degrade to
  inspectable partial snapshots instead of blocking compaction.

The original session messages are never overwritten by a checkpoint. Session JSON contains a
bounded mirror of recent checkpoints for fast local recovery; SQLite remains the durable source.

## Compaction flow

1. Build the current context epoch and restore the latest valid completed checkpoint.
2. Verify the raw `SessionContext.messages` prefix digest before applying a restored projection.
3. Cold-store old large tool results while protecting the newest half-window, capped at 40,000
   tokens and never below the recent-tail budget.
4. Select recent tool-interaction groups using a token budget, not only a turn count.
5. Collect bounded contributions from registered `CompactionContributor` implementations.
6. Capture a read-only workspace snapshot.
7. Persist a `started` checkpoint before the summarization request.
8. Persist `completed` with the sanitized final projection, or `failed` if summarization aborts.

Only completed checkpoints are eligible for recovery. A changed context epoch adds an explicit
update marker and the current system context always takes precedence over an older summary.

## Recent-tail configuration

- `context_recent_tail_ratio`: portion of the message hard limit reserved for exact recent text.
- `context_recent_tail_min_tokens`: lower bound for that budget.
- `context_recent_tail_max_tokens`: upper bound for that budget.
- `context_recent_tail_max_groups`: maximum number of recent interaction groups considered.
- `context_min_recent_turns`: compatibility cap retained for existing configurations; token
  budget is authoritative.

## Extension contract

Components register with `ContextManager.register_compaction_contributor()`. A contributor returns
one or more `CompactionContribution` values with a name, priority, content, and token ceiling.
Failures are isolated and do not abort compaction. `MemoryManager` is the first built-in
contributor and supplies the pre-compaction fact snapshot plus active scratchpad state.

## Episode linkage

Episodes generated at session end copy the latest completed `compaction_checkpoint_id` and
`workspace_snapshot_id`. This makes recalled task experience traceable to both the conversation
projection and the code state from which it was learned.
