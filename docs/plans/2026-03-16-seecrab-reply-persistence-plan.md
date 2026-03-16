# SeeCrab Reply Persistence & deliver_artifacts Cleanup — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Remove deliver_artifacts from SeeCrab adapter layer (not applicable to webapp) and persist reply state (thinking, step_cards, plan_checklist, timer) so steps survive page refresh.

**Architecture:** Store reply_state as a sibling field in the assistant message dict via `session.add_message(role, content, reply_state=...)`. The existing `SessionManager` JSON persistence handles serialization automatically. Frontend `_mapHistoryMessages()` reads `reply_state` on session load to reconstruct full `ReplyState`.

**Tech Stack:** Python (FastAPI backend), TypeScript/Vue (Pinia store), existing JSON file session storage.

**Design doc:** `docs/plans/2026-03-16-seecrab-reply-persistence-design.md`

---

### Task 1: Remove deliver_artifacts from backend adapter layer

**Files:**
- Modify: `src/seeagent/api/adapters/seecrab_models.py:32-36` (whitelist)
- Modify: `src/seeagent/api/adapters/card_builder.py:18` (CARD_TYPE_MAP)
- Modify: `src/seeagent/api/adapters/title_generator.py:52` (HUMANIZE_MAP)
- Modify: `src/seeagent/api/adapters/seecrab_adapter.py:193-198,215-233` (artifact synthesis)
- Test: `tests/unit/test_step_filter.py`, `tests/unit/test_card_builder.py`, `tests/unit/test_title_generator.py`

**Step 1: Update tests to remove deliver_artifacts expectations**

In `tests/unit/test_step_filter.py`:
- Line 22: Remove `assert self.f.classify("deliver_artifacts", {}) == FilterResult.WHITELIST`

In `tests/unit/test_card_builder.py`:
- Line 60: Change `assert self.builder._get_card_type("deliver_artifacts") == "file"` to `assert self.builder._get_card_type("deliver_artifacts") == "default"` (it should now fall through to default since the mapping is removed)

In `tests/unit/test_title_generator.py`:
- Lines 26-28: Delete the entire `test_deliver_artifacts_with_filename` method

**Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_step_filter.py tests/unit/test_card_builder.py tests/unit/test_title_generator.py -v`
Expected: 3 failures (whitelist, card_type, title tests)

**Step 3: Remove deliver_artifacts from backend code**

In `src/seeagent/api/adapters/seecrab_models.py` line 34:
- Remove `"deliver_artifacts",` from the `whitelist` default_factory list

In `src/seeagent/api/adapters/card_builder.py` line 18:
- Remove `"deliver_artifacts": "file",` from `CARD_TYPE_MAP`

In `src/seeagent/api/adapters/title_generator.py` line 52:
- Remove `"deliver_artifacts": lambda args: f'发送 {args.get("filename", "文件")}',` from `HUMANIZE_MAP`

In `src/seeagent/api/adapters/seecrab_adapter.py`:
- Lines 193-198: Remove the `deliver_artifacts` artifact synthesis block in `_handle_tool_call_end()`
- Lines 215-233: Delete the entire `_extract_artifact()` static method

**Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_step_filter.py tests/unit/test_card_builder.py tests/unit/test_title_generator.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/seeagent/api/adapters/seecrab_models.py src/seeagent/api/adapters/card_builder.py src/seeagent/api/adapters/title_generator.py src/seeagent/api/adapters/seecrab_adapter.py tests/unit/test_step_filter.py tests/unit/test_card_builder.py tests/unit/test_title_generator.py
git commit -m "fix(seecrab): remove deliver_artifacts from webapp adapter layer

deliver_artifacts is an IM channel tool, not applicable to the SeeCrab
webapp. Removes it from whitelist, card_type map, humanize map, and
the artifact synthesis logic in SeeCrabAdapter."
```

---

### Task 2: Remove Artifact type and event handling from frontend

**Files:**
- Modify: `apps/seecrab/src/types/index.ts:3,6,33,86-91`
- Modify: `apps/seecrab/src/stores/chat.ts:29,92-93,189,196`

**Step 1: Remove Artifact from types/index.ts**

In `apps/seecrab/src/types/index.ts`:
- Line 6: Remove `| 'artifact'` from `SSEEventType`
- Line 33: Remove `artifacts: Artifact[]` from `ReplyState`
- Lines 86-91: Delete the entire `Artifact` interface

**Step 2: Remove artifact handling from stores/chat.ts**

In `apps/seecrab/src/stores/chat.ts`:
- Line 29: Remove `artifacts: [],` from `startNewReply()`
- Lines 92-93: Remove the `case 'artifact': reply.artifacts.push(event as any); break` block
- Line 189: Remove `artifacts: [],` from `_mapHistoryMessages()` assistant reply construction
- Line 196: Also remove the `Artifact` from the import if it was imported (check line 6 — it imports `type { Message, ReplyState, StepCard, PlanStep, SSEEvent }` — `Artifact` is not imported, so nothing to change there)

**Step 3: Verify no TypeScript errors**

Run: `cd apps/seecrab && npx vue-tsc --noEmit 2>&1 | head -30` (or `npx tsc --noEmit` if vue-tsc not available)
Expected: No errors related to Artifact

**Step 4: Commit**

```bash
git add apps/seecrab/src/types/index.ts apps/seecrab/src/stores/chat.ts
git commit -m "fix(seecrab): remove Artifact type and event handling from frontend

Artifact events were synthesized from deliver_artifacts which is not
applicable to the webapp. Removes Artifact interface, SSEEventType
entry, and dispatch handling."
```

---

### Task 3: Backend — collect reply_state during SSE stream and persist

**Files:**
- Modify: `src/seeagent/api/routes/seecrab.py:176-192` (generate() function)

**Step 1: Add reply_state collection in generate()**

In `src/seeagent/api/routes/seecrab.py`, inside the `generate()` function, replace the block from line 176 (`full_reply = ""`) through line 192 (the `session.add_message` block) with:

```python
            full_reply = ""
            reply_state = {
                "thinking": "",
                "step_cards": [],
                "plan_checklist": None,
                "timer": {"ttft": None, "total": None},
            }

            async for event in adapter.transform(raw_stream, reply_id=reply_id):
                if disconnect_event.is_set():
                    break
                payload = json.dumps(event, ensure_ascii=False)
                yield f"data: {payload}\n\n"

                # Collect reply_state for persistence
                etype = event.get("type")
                if etype == "ai_text":
                    full_reply += event.get("content", "")
                elif etype == "thinking":
                    reply_state["thinking"] += event.get("content", "")
                elif etype == "step_card":
                    _upsert_step_card(reply_state["step_cards"], event)
                elif etype == "plan_checklist":
                    reply_state["plan_checklist"] = event.get("steps")
                elif etype == "timer_update":
                    phase = event.get("phase")
                    if phase in reply_state["timer"] and event.get("state") == "done":
                        reply_state["timer"][phase] = event.get("value")

            # Save assistant reply with reply_state to session
            if session and full_reply:
                try:
                    session.add_message(
                        "assistant", full_reply, reply_state=reply_state
                    )
                    if session_manager:
                        session_manager.mark_dirty()
                except Exception:
                    pass
```

**Step 2: Add _upsert_step_card helper function**

Add this function at module level in `src/seeagent/api/routes/seecrab.py` (before the `_busy_locks` dict, around line 27):

```python
def _upsert_step_card(cards: list[dict], event: dict) -> None:
    """Upsert a step_card event into the cards list by step_id."""
    step_id = event.get("step_id")
    card = {k: v for k, v in event.items() if k != "type"}
    for i, c in enumerate(cards):
        if c.get("step_id") == step_id:
            cards[i] = card
            return
    cards.append(card)
```

**Step 3: Run backend lint check**

Run: `ruff check src/seeagent/api/routes/seecrab.py`
Expected: No errors

**Step 4: Commit**

```bash
git add src/seeagent/api/routes/seecrab.py
git commit -m "feat(seecrab): collect reply_state during SSE stream and persist to session

Accumulates thinking, step_cards, plan_checklist, and timer data from
the SSE event stream and passes it to session.add_message() as a
reply_state kwarg. This data is persisted via the existing JSON
session storage."
```

---

### Task 4: Backend — return reply_state in GET session endpoint

**Files:**
- Modify: `src/seeagent/api/routes/seecrab.py:279-284` (GET /sessions/{session_id} message serialization)

**Step 1: Add reply_state to message response**

In `src/seeagent/api/routes/seecrab.py`, in the `get_session()` endpoint, modify the message dict construction (around line 279) to include `reply_state`:

Change:
```python
                messages.append({
                    "role": m.get("role", ""),
                    "content": m.get("content", ""),
                    "timestamp": m.get("timestamp", 0),
                    "metadata": m.get("metadata", {}),
                })
```

To:
```python
                msg_dict = {
                    "role": m.get("role", ""),
                    "content": m.get("content", ""),
                    "timestamp": m.get("timestamp", 0),
                    "metadata": m.get("metadata", {}),
                }
                if m.get("reply_state"):
                    msg_dict["reply_state"] = m["reply_state"]
                messages.append(msg_dict)
```

**Step 2: Commit**

```bash
git add src/seeagent/api/routes/seecrab.py
git commit -m "feat(seecrab): return reply_state in GET /sessions/{id} response

Includes reply_state field in message dicts when available, enabling
the frontend to restore step cards and thinking after page refresh."
```

---

### Task 5: Frontend — restore reply_state from session history

**Files:**
- Modify: `apps/seecrab/src/stores/chat.ts:172-201` (_mapHistoryMessages)

**Step 1: Enhance _mapHistoryMessages to use reply_state**

In `apps/seecrab/src/stores/chat.ts`, replace the `_mapHistoryMessages` function with:

```typescript
  function _mapHistoryMessages(rawMessages: any[]): Message[] {
    return rawMessages.map((m: any, i: number) => {
      const ts = _parseTimestamp(m.timestamp)
      const msg: Message = {
        id: `${m.role}_${ts}_${i}`,
        role: m.role,
        content: m.content || '',
        timestamp: ts,
      }
      if (m.role === 'assistant' && m.content) {
        const rs = m.reply_state
        msg.reply = {
          replyId: msg.id,
          agentId: 'main',
          agentName: 'SeeAgent',
          thinking: rs?.thinking ?? '',
          thinkingDone: true,
          planChecklist: rs?.plan_checklist ?? null,
          stepCards: (rs?.step_cards ?? []).map(_mapStepCard),
          summaryText: m.content,
          timer: {
            ttft: { state: 'done' as const, value: rs?.timer?.ttft ?? null },
            total: { state: 'done' as const, value: rs?.timer?.total ?? null },
          },
          askUser: null,
          isDone: true,
        }
      }
      return msg
    })
  }

  function _mapStepCard(raw: any): StepCard {
    return {
      stepId: raw.step_id,
      title: raw.title,
      status: raw.status,
      sourceType: raw.source_type,
      cardType: raw.card_type,
      duration: raw.duration ?? null,
      planStepIndex: raw.plan_step_index ?? null,
      agentId: raw.agent_id ?? 'main',
      input: raw.input ?? null,
      output: raw.output ?? null,
      absorbedCalls: raw.absorbed_calls ?? [],
    }
  }
```

**Step 2: Verify no TypeScript errors**

Run: `cd apps/seecrab && npx vue-tsc --noEmit 2>&1 | head -30`
Expected: No errors

**Step 3: Commit**

```bash
git add apps/seecrab/src/stores/chat.ts
git commit -m "feat(seecrab): restore reply_state from session history on page load

_mapHistoryMessages now reads reply_state from backend response to
reconstruct thinking, step_cards, plan_checklist, and timer data.
Adds _mapStepCard helper for snake_case → camelCase mapping."
```

---

### Task 6: Full test suite verification

**Step 1: Run all unit tests**

Run: `pytest tests/unit/ -x -v`
Expected: All pass

**Step 2: Run ruff on all modified files**

Run: `ruff check src/seeagent/api/adapters/ src/seeagent/api/routes/seecrab.py`
Expected: No errors

**Step 3: Run frontend build check**

Run: `cd apps/seecrab && npm run build 2>&1 | tail -10`
Expected: Build succeeds

---

## File Change Summary

| File | Change | Task |
|------|--------|------|
| `src/seeagent/api/adapters/seecrab_models.py` | Remove `deliver_artifacts` from whitelist | 1 |
| `src/seeagent/api/adapters/card_builder.py` | Remove `deliver_artifacts` from CARD_TYPE_MAP | 1 |
| `src/seeagent/api/adapters/title_generator.py` | Remove `deliver_artifacts` from HUMANIZE_MAP | 1 |
| `src/seeagent/api/adapters/seecrab_adapter.py` | Remove artifact synthesis + `_extract_artifact()` | 1 |
| `tests/unit/test_step_filter.py` | Remove deliver_artifacts whitelist assertion | 1 |
| `tests/unit/test_card_builder.py` | Update deliver_artifacts card_type expectation | 1 |
| `tests/unit/test_title_generator.py` | Remove deliver_artifacts test | 1 |
| `apps/seecrab/src/types/index.ts` | Remove Artifact interface + SSE event type | 2 |
| `apps/seecrab/src/stores/chat.ts` | Remove artifact handling + enhance _mapHistoryMessages | 2, 5 |
| `src/seeagent/api/routes/seecrab.py` | Collect reply_state + return in GET endpoint | 3, 4 |

**Total: 10 files modified, 0 new files.**
