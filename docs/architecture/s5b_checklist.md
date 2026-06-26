# S5-B Implementation Checklist

> Step-by-step playbook for deleting the v1.28 historical safety
> nets. **Do not start this work until the telemetry gate has passed.**
>
> Last updated: v1.28.3-pre (S5-A landed; S5-B is the planned successor).

## Prerequisites — verify before starting

### 1. Telemetry gate

Run against a production snapshot:

```bash
curl http://prod-host:18900/api/diagnostics/conversation_metrics \
  | python scripts/concurrency_telemetry_analyzer.py
```

Expected verdict for **all 14 consecutive days**:

```
[GO]  S5-B delete force-writes
        All 5 illegal_reasoning_entry source labels at 0 in this snapshot.
```

If any day shows a non-zero count on any of the 5 source labels
(`reason_stream_iter` / `reason_stream_outer` / `run_impl_main_loop` /
`run_impl_ask_user_reply` / `run_impl_ask_user_timeout`), **abort
S5-B and investigate the labelled code path first**. The whole
point of the 5 labels is to detect race paths we don't yet
understand; ignoring them and shipping S5-B turns a soft "force-write
+ pager alert" into a hard SSE stream crash.

### 2. Code state

* `tests/unit/test_no_force_write_state_transitions.py` exists and
  passes — `S5B_BACKLOG_FILES = {"reasoning_engine.py": 9}` +
  `ARCH_FORCE_WRITE_FILES = {"agent_state.py": 1}`.
* `IllegalReasoningEntry` + `ensure_ready_for_reasoning` shipped in
  S5-A.
* `_reason_stream_impl` outer `except IllegalReasoningEntry` exists
  (FIX-S5A-1).
* All 9 force-write sites carry `# s5b-allow-force-write` token
  (this file's section 4 will tell you exactly where).

### 3. Branch hygiene

Do this work on a fresh branch off `main`. Do NOT create the
branch ahead of time and let it sit — keep the diff small and
land it within 1-2 days of starting.

## High-level plan

S5-B has four sub-steps. Each one is independently reversible
and ships a small, focused commit. Do them in order:

| Step | Files | Risk | Reversible? |
|---|---|---|---|
| **S5-B.1** Type the illegal-transition signal | `agent_state.py` | low | yes (revert exception type) |
| **S5-B.2** Delete 9 force-writes in `reasoning_engine.py` | `reasoning_engine.py` | medium | yes (re-add diff) |
| **S5-B.3** Delete 11 silent `except ValueError: pass` at non-reasoning sites | `reasoning_engine.py` | low | yes |
| **S5-B.4** Tighten tests & ratchet syntax guard | `tests/` + docs | trivial | yes |

After all four steps land, run `scripts/concurrency_telemetry_analyzer.py`
weekly for the first month — if any source label fires, you've
removed a safety net that was still load-bearing in some unknown
race path. Roll back step 2 (or step 1) to the affected sites only.

## S5-B.1 — Type the illegal-transition signal

`TaskState.transition()` currently raises `ValueError` for any
illegal target. Post-S5-B we want the **outer caller** to catch a
typed `IllegalStateTransition` and route it through the same SSE
error pipeline as `IllegalReasoningEntry`.

**Why not just keep `ValueError`?**

S5-A used `IllegalReasoningEntry` only for the
`ensure_ready_for_reasoning` helper because the helper does the
terminal-state check explicitly. Step S5-B.2 deletes the 9
force-write sites that today re-catch `ValueError` from
`transition()` directly; after deletion the `ValueError` bubbles up
to the outer `except Exception`, which loses the structured
`code="illegal_state"` field + pager-alert counter that S5-A made
work for the reasoning-entry path. Typing the signal at
`transition()` means **every** illegal transition (not just
reasoning-entry ones) joins the same observability + UX pipeline.

### Change

`src/openakita/core/agent_state.py`:

```python
class IllegalStateTransition(RuntimeError):
    """v1.28.3 S5-B: typed signal for any illegal TaskState.transition.

    Replaces the historical bare ValueError so reasoning_engine outer
    catches can route this through the inc_illegal_reasoning_entry
    counter + structured SSE error, instead of falling into the
    generic ``except Exception`` handler.
    """

# Keep IllegalReasoningEntry as a *subclass* — existing code that
# catches IllegalReasoningEntry keeps working unchanged.
class IllegalReasoningEntry(IllegalStateTransition):
    ...

class TaskState:
    def transition(self, new_status: TaskStatus) -> None:
        valid_targets = _VALID_TRANSITIONS.get(self.status, set())
        if new_status not in valid_targets:
            raise IllegalStateTransition(
                f"非法状态转换: {self.status.value} -> {new_status.value}. "
                f"合法目标: {[s.value for s in valid_targets]}"
            )
        ...
```

### Test update

Add a single regression case to `tests/unit/test_reason_stream_state_race.py`:

```python
def test_transition_raises_illegal_state_transition_subclass() -> None:
    """S5-B contract: TaskState.transition raises IllegalStateTransition
    on illegal transitions; IllegalReasoningEntry is a subclass so
    existing catch sites keep working."""
    state = TaskState(...)
    state.status = TaskStatus.COMPLETED
    with pytest.raises(IllegalStateTransition):
        state.transition(TaskStatus.ACTING)
    # ensure_ready_for_reasoning still raises the more specific subclass
    state.status = TaskStatus.COMPLETED
    with pytest.raises(IllegalReasoningEntry):
        state.ensure_ready_for_reasoning()
```

Any existing test that uses `pytest.raises(ValueError)` for an
illegal transition needs `IllegalStateTransition` (or `ValueError`
remains a base — see Audit Risk 1 below).

### Audit risk 1: backward compat

If any caller catches `ValueError` from `transition()` and depends on
that exception type (not just message), step S5-B.1 breaks them.
Grep before shipping:

```bash
rg "except ValueError" src/openakita/ \
  | rg -v "test_|# s5b-allow-force-write|# cancel-idempotent-force-write"
```

Each remaining hit must be reviewed — does it catch `transition`'s
ValueError? If yes, decide:

- (a) The caller wanted a typed signal — change to `except IllegalStateTransition`.
- (b) The caller is a different ValueError source (parse error, etc.) — leave it.

## S5-B.2 — Delete 9 force-writes in `reasoning_engine.py`

The exact 9 sites are tagged `# s5b-allow-force-write`. Locate
them by token, not lineno (linenos drift):

```bash
rg -n "# s5b-allow-force-write" src/openakita/core/reasoning_engine.py
```

| # | Site | Target | Post-S5-B behaviour |
|---|---|---|---|
| 1 | `_reason_stream_impl` main-loop entry (after `ensure_ready_for_reasoning`) | REASONING | **Delete the entire `except ValueError` block.** The outer `except IllegalReasoningEntry` (FIX-S5A-1) catches the typed exception. ValueError shouldn't escape `ensure_ready_for_reasoning` anyway — that path is dead code per `test_every_non_terminal_status_can_reach_reasoning`. |
| 2 | `_reason_stream_impl` verify-incomplete branch | FAILED / COMPLETED | Delete the except. `IllegalStateTransition` bubbles to outer `except IllegalStateTransition` (step S5-B.1) and yields structured `code="illegal_state"`. Acceptable — verify-incomplete is end-of-turn anyway. |
| 3 | `_reason_stream_impl` verify branch | VERIFYING | same — delete |
| 4 | `_reason_stream_impl` tool-call branch | ACTING | same — delete |
| 5 | `_reason_stream_impl` ask_user branch | WAITING_USER | same — delete |
| 6 | `_reason_stream_impl` observe branch | OBSERVING | same — delete |
| 7 | `_reason_stream_impl` loop_terminated branch | FAILED | same — delete |
| 8 | `_reason_stream_impl` max_iterations branch | FAILED | same — delete |
| 9 | `_handle_llm_error` model-switch | MODEL_SWITCHING | **Special case — do NOT delete blindly.** MODEL_SWITCHING happens during error retry. Verify that retry actually works on a terminal state before deciding. Recommended: reclassify as permanent (analogous to `cancel-idempotent-force-write`) with a new token `# model-switch-idempotent-force-write` and bump `ARCH_FORCE_WRITE_FILES[reasoning_engine.py] = 1` while dropping `S5B_BACKLOG_FILES[reasoning_engine.py]` to 0. |

### After deletion, verify

1. `pytest tests/unit/test_no_force_write_state_transitions.py` —
   update `S5B_BACKLOG_FILES[reasoning_engine.py] = 0` (or 1 if
   MODEL_SWITCHING is reclassified, then move it to
   `ARCH_FORCE_WRITE_FILES` and add the new token).
2. Remove the corresponding parametrize entries from
   `test_each_known_force_write_target_is_present`.
3. Full regression: `pytest tests/ -q` should pass.
4. Manual smoke: kick off a turn, click "stop", send a second
   message — should not see Anthropic 400, should not see
   `code="illegal_state"` SSE event unless the race actually
   happened.

### Audit risk 2: SSE error event quality

After deletion, if a race somehow does happen at a non-reasoning
transition site (e.g. trying to enter VERIFYING from terminal),
the outer `except IllegalStateTransition` (step S5-B.1) must
emit a structured event. Verify:

```python
# In _reason_stream_impl outer try ladder:
except IllegalStateTransition as e:
    inc_illegal_reasoning_entry(source="reason_stream_outer")
    yield {
        "type": "error",
        "code": "illegal_state",
        "message": "上一条消息正在收尾，请稍候再试或新建会话。",
    }
    yield {"type": "done"}
    return
```

(Replaces the current `except IllegalReasoningEntry` clause — wider
catch, same handling.)

## S5-B.3 — Delete silent `except ValueError: pass` (with care for FIX-S5A-2)

These are non-reasoning transition swallow sites in `_run_impl`:

```bash
rg -n "except ValueError:" src/openakita/core/reasoning_engine.py \
  | rg -v "s5b-allow-force-write|cancel-idempotent-force-write"
```

This produces **11 hits**, split into two categories:

### Category A: 8 pure-pass sites (delete entirely)

The 8 sites where the body is literally just `pass` — no counter,
no logging. Delete `try` + `except` + indent the wrapped
`state.transition(...)` call back into the surrounding flow.
`IllegalStateTransition` (post step S5-B.1) propagates to the
`run()` outer wrapper and from there to the HTTP / IM channel
adapter, which returns a graceful error.

### Category B: 3 FIX-S5A-2 telemetry sites (preserve counter, drop swallow)

Three sites in `_run_impl` carry the FIX-S5A-2 counter wiring
(audit fix from 2612213b). They look like:

```python
except ValueError:
    if state.is_terminal:
        logger.warning(...)
        inc_illegal_reasoning_entry(source="run_impl_main_loop")
```

**Do not delete these blocks naively** — the counter call lives
INSIDE the `except` block, so removing the except also removes
the telemetry that the whole S5-A audit was added to provide.
Refactor each one to catch the typed exception and re-raise after
telemetry:

```python
# Post-S5-B shape (telemetry preserved, swallow removed):
except IllegalStateTransition:
    if state.is_terminal:
        logger.warning(
            "[ReAct] Iter %d: state already terminal (%s) before "
            "REASONING transition; preempt protocol bypassed "
            "(session=%r)",
            iteration + 1,
            state.status.value,
            conversation_id,
        )
        inc_illegal_reasoning_entry(source="run_impl_main_loop")
    raise  # let the outer wrapper handle the user-facing error
```

The three sites and their source labels:

| Lineno | Source label |
|---|---|
| ~2452 | `run_impl_main_loop` |
| ~2989 | `run_impl_ask_user_reply` |
| ~3039 | `run_impl_ask_user_timeout` |

After this refactor, `inc_illegal_reasoning_entry` continues to
fire on terminal-state races (so future audits can still see
incidence rates), but the race no longer silently continues into
the next loop iteration — the typed exception propagates and the
HTTP layer emits a structured error.

### Audit risk 3: IM channel error handling

After deletion, run the existing IM channel adapter regression:

```bash
pytest tests/integration/test_wework_ws_adapter.py \
       tests/integration/test_telegram_adapter.py \
       tests/integration/test_feishu_adapter.py -q
```

If any adapter doesn't gracefully handle a propagated
`IllegalStateTransition`, fix the adapter first.

## S5-B.4 — Tighten tests and ratchet syntax guard

1. **Update the syntax guard ratchet.** In
   `tests/unit/test_no_force_write_state_transitions.py`:
   * If MODEL_SWITCHING reclassified as architectural-permanent:
     drop `S5B_BACKLOG_FILES[reasoning_engine.py]` from 9 to 0,
     and add `ARCH_FORCE_WRITE_FILES[reasoning_engine.py] = 1`
     for MODEL_SWITCHING. Add the new token
     `model-switch-idempotent-force-write` to `RECOGNISED_TOKENS`.
   * If MODEL_SWITCHING fully deleted:
     drop `S5B_BACKLOG_FILES[reasoning_engine.py]` to 0; no new
     architectural entries.
2. `EXPECTED_ILLEGAL_ENTRY_LABELS` in
   `scripts/concurrency_telemetry_analyzer.py` doesn't change —
   S5-B doesn't add/remove source labels.
3. `test_each_known_force_write_target_is_present` parametrize list
   shrinks to whatever set survives. If MODEL_SWITCHING is
   reclassified, keep its parametrize entry but rename the test
   class section to distinguish "permanent" from "backlog".
4. Add a new test:
   `test_illegal_state_transition_propagates_to_outer_catch` —
   fire an illegal transition from inside `_reason_stream_impl`
   (mock a terminal state) and assert the outer `except`
   surfaces a structured `code="illegal_state"` event with the
   correct source label.
5. Add a regression test for the FIX-S5A-2 refactor:
   `test_run_impl_terminal_race_increments_counter_and_raises` —
   mock a terminal-state race in each of the 3 run_impl sites,
   assert (a) `inc_illegal_reasoning_entry` fires with the
   correct source label, and (b) the exception propagates (does
   NOT silently continue the loop).
6. Update `docs/architecture/conversation_concurrency.md` —
   remove the S5-B "pending" section from "Deferred work", add
   a brief note under "History" that S5-B shipped at vX.Y.Z.
7. Update `docs/release-notes/v1.28.md` — replace the
   "draft" S5-B section with the actual shipping notes including
   the per-step diff sizes and the regression numbers.

## Rollback plan

If S5-B causes a production regression in the first 2 weeks:

1. Identify which step (S5-B.1/.2/.3) is the culprit by reading
   the failing `inc_illegal_reasoning_entry` source labels +
   any new exception types in error reports.
2. Revert that step's commit. The four steps are intentionally
   separated so rollback is surgical.
3. If step 1 (`IllegalStateTransition` rename) is the culprit,
   it's safe to revert independently — `IllegalReasoningEntry`
   stays a subclass either way.
4. If step 2 (`reasoning_engine.py` deletions) is the culprit,
   the syntax guard test will already have been ratcheted to
   `S5B_BACKLOG_FILES[reasoning_engine.py] = 0` (or 1 with
   MODEL_SWITCHING reclassified). Revert that ratchet first, then
   the source deletions.
5. After rollback, file a bug against the specific source label
   that fired. Don't re-attempt S5-B until the root cause is
   understood.

## Estimated work

| Step | Time | Reviewer effort |
|---|---|---|
| S5-B.1 IllegalStateTransition | 0.5 day | 30 min |
| S5-B.2 reasoning_engine.py deletions (8 deletes + 1 reclassify) | 1 day | 1 hour (slow, careful) |
| S5-B.3 _run_impl: 8 pure-pass deletes + 3 telemetry refactors | 0.75 day | 45 min |
| S5-B.4 test + docs ratchet | 0.5 day | 30 min |
| **Total** | **~2.75 days** | **~2.75 hours review** |

Plus the 2-week telemetry wait before starting.

The S5-B.3 estimate grew slightly compared to the original (0.5d →
0.75d) because of CHECK-A2-1 — the FIX-S5A-2 telemetry refactor
needs more thought than a blind delete. Reviewer should verify each
of the 3 sites still emits the counter AND raises.
