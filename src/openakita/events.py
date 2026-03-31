"""
Canonical SSE event type definitions.

This module is the Single Source of Truth for all streaming event types
used between the reasoning engine, agent, API layer, and frontend.

Frontend TypeScript types should be kept in sync — see
apps/setup-center/src/streamEvents.ts
"""

from enum import Enum


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

    # ── Agent orchestration ──
    AGENT_HANDOFF = "agent_handoff"
    USER_INSERT = "user_insert"

    # ── UI enrichment (injected by API layer) ──
    ARTIFACT = "artifact"
    UI_PREFERENCE = "ui_preference"
