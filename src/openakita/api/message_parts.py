"""Ordered message-parts projection for the chat UI.

This mirrors the frontend ``MessagePart`` discriminated union
(``apps/setup-center/src/types.ts``) and the client-side projection in
``apps/setup-center/src/views/chat/utils/messageParts.ts``. It is the
server-side half of the "single rendering model" that lets rich cards
(reasoning / plan / answered ask_user / attachments / …) re-display
losslessly after a reload or window switch — closing the gap where the
live SSE stream and the persisted flat history used to diverge.

Design:
  - Heavy text blocks (``reasoning`` / ``text``) are *markers*: the
    renderer reads their payload from the corresponding flat field that the
    history endpoint already returns, so this projection never re-inlines
    (and thus never doubles) the answer text or thinking chain on the wire.
  - Small blocks (``plan`` / ``attachment`` / ``ask_user`` / ``error``)
    inline their data so a part is self-describing.

The projection is derived from the stored message dict, so it is NOT
persisted itself — it cannot bloat ``sessions.json`` and is never trimmed.
"""

from __future__ import annotations

import copy
from typing import Any

PROGRESS_EVENT_TYPES = {
    "todo_created",
    "todo_step_updated",
    "todo_completed",
    "todo_cancelled",
}


def serialize_plan_to_chat_todo(plan: dict | None) -> dict | None:
    """Convert a backend plan dict (snake_case) into the frontend ChatTodo
    shape (camelCase ``taskSummary``). Mirrors the SSE ``todo_created`` payload
    in ``reasoning_engine.py`` so the persisted snapshot and the live event
    look identical to the UI.
    """
    if not isinstance(plan, dict):
        return None
    steps_src = plan.get("steps") or []
    steps: list[dict] = []
    for s in steps_src:
        if not isinstance(s, dict):
            continue
        steps.append(
            {
                "id": s.get("id", ""),
                "description": s.get("description", ""),
                "status": s.get("status", "pending"),
                **({"result": s.get("result")} if s.get("result") else {}),
            }
        )
    return {
        "id": plan.get("id", ""),
        "taskSummary": plan.get("task_summary", plan.get("taskSummary", "")),
        "steps": steps,
        "status": plan.get("status", "in_progress"),
    }


def normalize_progress_event(event: dict | None, *, seq: int | None = None) -> dict | None:
    """Normalize a live progress SSE event into the persisted event journal shape.

    The journal intentionally stores small, self-contained events instead of
    only the folded final plan snapshot. History can project the latest state
    from these events while still keeping the causal progress trail available
    for future replay/timeline UI.
    """
    if not isinstance(event, dict):
        return None
    event_type = event.get("type")
    if event_type not in PROGRESS_EVENT_TYPES:
        return None

    out: dict[str, Any] = {"type": event_type}
    if seq is not None:
        out["seq"] = seq

    if event_type == "todo_created":
        plan = serialize_plan_to_chat_todo(event.get("plan"))
        if not (plan and plan.get("steps")):
            return None
        out["plan"] = plan
    elif event_type == "todo_step_updated":
        step_id = event.get("stepId") or event.get("step_id")
        if step_id:
            out["stepId"] = step_id
        step_idx = event.get("stepIdx")
        if isinstance(step_idx, int):
            out["stepIdx"] = step_idx
        status = event.get("status")
        if status:
            out["status"] = status
        if "result" in event:
            out["result"] = event.get("result")
    return out


def normalize_progress_events(events: Any) -> list[dict]:
    """Return a sanitized progress-event journal with stable sequence numbers."""
    if not isinstance(events, list):
        return []
    out: list[dict] = []
    for raw in events:
        item = normalize_progress_event(raw, seq=len(out) + 1)
        if item is not None:
            out.append(item)
    return out


def append_progress_event(events: list[dict] | None, event: dict | None) -> list[dict]:
    """Append one normalized progress event to a journal copy."""
    out = list(events or [])
    item = normalize_progress_event(event, seq=len(out) + 1)
    if item is not None:
        out.append(item)
    return out


def project_progress_events_to_todo(events: Any) -> dict | None:
    """Fold a persisted progress-event journal into the latest ChatTodo state."""
    todo: dict | None = None
    for event in normalize_progress_events(events):
        event_type = event.get("type")
        if event_type == "todo_created":
            plan = serialize_plan_to_chat_todo(event.get("plan"))
            todo = copy.deepcopy(plan) if plan else todo
            continue
        if not isinstance(todo, dict):
            continue
        if event_type == "todo_step_updated":
            step_id = event.get("stepId")
            step_idx = event.get("stepIdx")
            steps = todo.get("steps") or []
            for i, step in enumerate(steps):
                if (step_id and step.get("id") == step_id) or (
                    isinstance(step_idx, int) and i == step_idx
                ):
                    if event.get("status"):
                        step["status"] = event["status"]
                    if "result" in event:
                        step["result"] = event.get("result")
                    break
            if steps and all(
                step.get("status") in {"completed", "skipped", "failed", "cancelled"}
                for step in steps
            ):
                if any(step.get("status") == "failed" for step in steps):
                    todo["status"] = "failed"
                elif any(step.get("status") == "cancelled" for step in steps):
                    todo["status"] = "cancelled"
                else:
                    todo["status"] = "completed"
        elif event_type == "todo_completed":
            todo["status"] = "completed"
        elif event_type == "todo_cancelled":
            todo["status"] = "cancelled"
    return todo


def build_message_parts(
    msg: dict,
    *,
    todo: dict | None = None,
    progress_events: list[dict] | None = None,
) -> list[dict]:
    """Build the ordered parts projection for one stored assistant message.

    ``todo`` overrides ``msg['todo']`` (used to attach a live in-flight plan
    snapshot during hydration). Returns ``[]`` for non-assistant messages —
    user / system messages render directly, not via parts.
    """
    if msg.get("role") != "assistant":
        return []

    parts: list[dict] = []

    if msg.get("chain_summary") or msg.get("chain_timeline"):
        parts.append({"kind": "reasoning", "id": "reasoning"})
    if msg.get("org_timeline"):
        parts.append({"kind": "org_timeline", "id": "org_timeline"})
    if msg.get("sources"):
        parts.append({"kind": "sources", "id": "sources"})
    if msg.get("mcp_calls"):
        parts.append({"kind": "mcp", "id": "mcp"})

    events = (
        normalize_progress_events(progress_events)
        if progress_events is not None
        else normalize_progress_events(msg.get("progress_events"))
    )
    plan = (
        todo
        if todo is not None
        else project_progress_events_to_todo(events) or msg.get("todo")
    )
    plan_todo = serialize_plan_to_chat_todo(plan) if isinstance(plan, dict) else plan
    if isinstance(plan_todo, dict) and plan_todo.get("steps"):
        part = {"kind": "plan", "id": f"plan:{plan_todo.get('id', '')}", "todo": plan_todo}
        if events:
            part["progressEvents"] = events
        parts.append(part)

    content = msg.get("content")
    if isinstance(content, str) and content.strip():
        parts.append({"kind": "text", "id": "text"})

    artifacts = msg.get("artifacts")
    if isinstance(artifacts, list):
        for i, art in enumerate(artifacts):
            parts.append({"kind": "attachment", "id": f"attachment:{i}", "artifact": art})

    ask_user = msg.get("ask_user")
    if isinstance(ask_user, dict):
        parts.append({"kind": "ask_user", "id": "ask_user", "ask": ask_user})

    if msg.get("is_truncated") or msg.get("stream_error"):
        parts.append({"kind": "error", "id": "error"})

    return parts


def normalize_chat_todo(todo: Any) -> dict | None:
    """Accept either a frontend ChatTodo dict or a backend plan dict and return
    the ChatTodo shape, or ``None``."""
    if not isinstance(todo, dict):
        return None
    if "taskSummary" in todo and "steps" in todo:
        return todo
    return serialize_plan_to_chat_todo(todo)
