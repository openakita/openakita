"""
Canonical stream event definitions and protocol normalisation.

This module is the Single Source of Truth for event types used between the
reasoning engine, API layer, frontend, and other adapters.

Frontend TypeScript types should be kept in sync — see
apps/setup-center/src/streamEvents.ts
"""

from enum import Enum

STREAM_PROTOCOL_VERSION = 1


class StreamEventType(str, Enum):
    """All event types that may appear in the SSE stream to clients."""

    # ── Lifecycle ──
    HEARTBEAT = "heartbeat"
    ITERATION_START = "iteration_start"
    DONE = "done"
    ERROR = "error"

    # ── Thinking / Reasoning ──
    THINKING_START = "thinking_start"
    THINKING_DELTA = "thinking_delta"
    THINKING_END = "thinking_end"
    CHAIN_TEXT = "chain_text"

    # ── Text output ──
    TEXT_DELTA = "text_delta"
    TEXT_REPLACE = "text_replace"

    # ── Tool execution ──
    TOOL_CALL_START = "tool_call_start"
    TOOL_CALL_END = "tool_call_end"

    # ── Context management ──
    CONTEXT_COMPRESSED = "context_compressed"

    # ── Security / Interaction ──
    SECURITY_CONFIRM = "security_confirm"
    ASK_USER = "ask_user"

    # ── Todo / Plan ──
    TODO_CREATED = "todo_created"
    TODO_STEP_UPDATED = "todo_step_updated"
    TODO_COMPLETED = "todo_completed"
    TODO_CANCELLED = "todo_cancelled"
    PLAN_READY_FOR_APPROVAL = "plan_ready_for_approval"

    # ── Agent orchestration ──
    AGENT_HANDOFF = "agent_handoff"
    AGENT_SWITCH = "agent_switch"
    USER_INSERT = "user_insert"
    SUB_AGENT_STATE = "sub_agent_state"

    # ── UI enrichment (injected by API layer) ──
    ARTIFACT = "artifact"
    UI_PREFERENCE = "ui_preference"


def normalize_stream_event(event: dict | None) -> dict:
    """Attach protocol metadata and stable aliases for frontend consumption."""
    payload = dict(event or {})
    event_type = str(payload.get("type", ""))
    payload.setdefault("protocol_version", STREAM_PROTOCOL_VERSION)

    if event_type in (StreamEventType.TOOL_CALL_START.value, StreamEventType.TOOL_CALL_END.value):
        payload.setdefault("tool_name", payload.get("tool", ""))
        payload.setdefault("call_id", payload.get("id", ""))

    if event_type == StreamEventType.SECURITY_CONFIRM.value:
        payload.setdefault("tool_name", payload.get("tool", ""))
        payload.setdefault("confirm_id", payload.get("id", ""))
        payload.setdefault("call_id", payload.get("id", ""))

    if event_type == StreamEventType.TODO_CREATED.value and isinstance(payload.get("plan"), dict):
        plan = dict(payload["plan"])
        plan.setdefault("task_summary", plan.get("taskSummary", ""))
        for step in plan.get("steps", []) or []:
            if isinstance(step, dict):
                step.setdefault("step_id", step.get("id", ""))
        payload["plan"] = plan

    if event_type == StreamEventType.TODO_STEP_UPDATED.value:
        if "stepId" in payload and "step_id" not in payload:
            payload["step_id"] = payload["stepId"]
        if "step_id" in payload and "stepId" not in payload:
            payload["stepId"] = payload["step_id"]

    if event_type == StreamEventType.PLAN_READY_FOR_APPROVAL.value and isinstance(payload.get("data"), dict):
        data = dict(payload["data"])
        payload.setdefault("conversation_id", data.get("conversation_id", ""))
        payload.setdefault("plan_id", data.get("plan_id", ""))
        payload.setdefault("plan_file", data.get("plan_file", ""))
        payload["data"] = data

    if event_type == StreamEventType.SUB_AGENT_STATE.value:
        payload.setdefault("agent_id", payload.get("agentId", ""))
        payload.setdefault("session_id", payload.get("sessionId", ""))

    if event_type == StreamEventType.TEXT_REPLACE.value:
        payload.setdefault("content", "")

    if event_type == StreamEventType.AGENT_SWITCH.value:
        payload.setdefault("agent_id", payload.get("agentId", ""))
        payload.setdefault("session_id", payload.get("sessionId", ""))

    return payload
