/**
 * Canonical SSE event type definitions.
 *
 * KEEP IN SYNC with src/openakita/events.py — StreamEventType enum.
 * This file is the frontend Single Source of Truth for event type strings.
 */

export const STREAM_PROTOCOL_VERSION = 1;

export const StreamEventType = {
  // ── Lifecycle ──
  HEARTBEAT: "heartbeat",
  ITERATION_START: "iteration_start",
  DONE: "done",
  ERROR: "error",

  // ── Thinking / Reasoning ──
  THINKING_START: "thinking_start",
  THINKING_DELTA: "thinking_delta",
  THINKING_END: "thinking_end",
  CHAIN_TEXT: "chain_text",

  // ── Text output ──
  TEXT_DELTA: "text_delta",
  TEXT_REPLACE: "text_replace",

  // ── Tool execution ──
  TOOL_CALL_START: "tool_call_start",
  TOOL_CALL_END: "tool_call_end",
  SOURCE_USED: "source_used",
  MCP_CALL: "mcp_call",

  // ── Context management ──
  CONTEXT_COMPRESSED: "context_compressed",

  // ── Resource budget (soft warning + hard limit) ──
  BUDGET_WARNING: "budget_warning",
  BUDGET_EXCEEDED: "budget_exceeded",

  // ── Security / Interaction ──
  SECURITY_CONFIRM: "security_confirm",
  DEATH_SWITCH: "death_switch",
  ASK_USER: "ask_user",

  // ── Todo / Plan ──
  TODO_CREATED: "todo_created",
  TODO_STEP_UPDATED: "todo_step_updated",
  TODO_COMPLETED: "todo_completed",
  TODO_CANCELLED: "todo_cancelled",
  PLAN_READY_FOR_APPROVAL: "plan_ready_for_approval",

  // ── Task continuity (checkpoint for resume / timeline) ──
  TASK_CHECKPOINT: "task_checkpoint",

  // ── Agent orchestration ──
  AGENT_HANDOFF: "agent_handoff",
  AGENT_SWITCH: "agent_switch",
  USER_INSERT: "user_insert",
  SUB_AGENT_STATE: "sub_agent_state",

  // ── UI enrichment (injected by API layer) ──
  ARTIFACT: "artifact",
  UI_PREFERENCE: "ui_preference",
  ENDPOINT_NOTICE: "endpoint_notice",
} as const;

export type StreamEventTypeValue =
  (typeof StreamEventType)[keyof typeof StreamEventType];
