"""
Session Todo state management + lifecycle functions

Split from plan.py, responsible for:
- Module-level dict management (_session_active_todos / _session_todo_required / _session_handlers)
- Register, unregister, query, cleanup functions
- Lifecycle functions like auto_close_todo / cancel_todo / force_close_plan

ADR: Persistence Architecture (TD3)
------------------------------------
Single-worker deployment:
  - Module-level dicts (_session_active_todos, _session_todo_required,
    _session_handlers) are the PRIMARY source of truth for in-flight state.
  - TodoStore (todo_store.json) is a durable backup; on restart the store
    is loaded back into module-level dicts to recover active plans.

Multi-worker deployment (future):
  - Module-level dicts will be REPLACED by a shared backend (Redis or
    equivalent) so that all workers share the same in-flight state.
  - TodoStore can remain as a file-based audit log / cold backup.
  - Migration path: introduce a TodoStateBackend interface, make the
    module-level dict the default implementation, add Redis impl later.
"""

import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .todo_handler import PlanHandler

logger = logging.getLogger(__name__)

__all__ = [
    # Public API
    "require_todo_for_session",
    "is_todo_required",
    "has_active_todo",
    "get_active_plan_id",
    "register_active_todo",
    "unregister_active_todo",
    "clear_session_todo_state",
    "cleanup_session",
    "auto_close_todo",
    "cancel_todo",
    "force_close_plan",
    "register_plan_handler",
    "get_todo_handler_for_session",
    "get_active_todo_prompt",
    "get_active_todo_sessions",
    "iter_active_todo_sessions",
    # Private but depended on externally (transition period)
    "_session_active_todos",
    "_session_todo_required",
    "_session_handlers",
    "_emit_todo_lifecycle_event",
    # Backward-compatible aliases
    "has_active_plan",
    "get_active_plan_prompt",
    "require_plan_for_session",
    "is_plan_required",
    "register_active_plan",
    "unregister_active_plan",
    "clear_session_plan_state",
    "auto_close_plan",
    "cancel_plan",
    "get_plan_handler_for_session",
]

# ============================================
# Session Todo state management (module level)
# ============================================

_MAX_SESSIONS = 100

# Record which sessions are marked as requiring Todo (compound tasks)
_session_todo_required: dict[str, bool] = {}

# Record active Todos for sessions (session_id -> plan_id)
_session_active_todos: dict[str, str] = {}

# Store mapping of session -> PlanHandler instances (for querying Plan state during task completion)
_session_handlers: dict[str, "PlanHandler"] = {}


def _prune_oldest_sessions() -> None:
    """Prune oldest entries when any module-level dict exceeds _MAX_SESSIONS."""
    all_ids = set(_session_todo_required) | set(_session_active_todos) | set(_session_handlers)
    if len(all_ids) <= _MAX_SESSIONS:
        return
    excess = len(all_ids) - _MAX_SESSIONS
    stale = set(_session_todo_required) - set(_session_active_todos)
    victims = list(stale)[:excess]
    if len(victims) < excess:
        remaining = [sid for sid in all_ids if sid not in set(victims)]
        victims.extend(remaining[: excess - len(victims)])
    for sid in victims:
        cleanup_session(sid)
    if victims:
        logger.debug(f"[Todo] Pruned {len(victims)} stale sessions (max={_MAX_SESSIONS})")


def cleanup_session(session_id: str) -> None:
    """Remove all entries for the given session_id from all module-level dicts."""
    _session_todo_required.pop(session_id, None)
    _session_active_todos.pop(session_id, None)
    _session_handlers.pop(session_id, None)


def require_todo_for_session(session_id: str, required: bool) -> None:
    """Mark whether session requires Todo (called by Prompt Compiler)"""
    _prune_oldest_sessions()
    _session_todo_required[session_id] = required
    logger.info(f"[Plan] Session {session_id} todo_required={required}")


def is_todo_required(session_id: str) -> bool:
    """Check whether session is marked as requiring Todo"""
    return _session_todo_required.get(session_id, False)


def has_active_todo(session_id: str) -> bool:
    """Check whether session has active Todo"""
    return session_id in _session_active_todos


def get_active_plan_id(session_id: str) -> str | None:
    """Get plan_id of session's currently active Todo (for SSE event sync)"""
    return _session_active_todos.get(session_id)


def register_active_todo(session_id: str, plan_id: str) -> None:
    """Register active Todo"""
    _prune_oldest_sessions()
    _session_active_todos[session_id] = plan_id
    logger.info(f"[Plan] Registered active todo {plan_id} for session {session_id}")


def unregister_active_todo(session_id: str) -> None:
    """Unregister active Todo (keep handler to support future todos)"""
    if session_id in _session_active_todos:
        todo_id = _session_active_todos.pop(session_id)
        logger.info(f"[Todo] Unregistered todo {todo_id} for session {session_id}")
    if session_id in _session_todo_required:
        del _session_todo_required[session_id]


def clear_session_todo_state(session_id: str) -> None:
    """Clear all Todo state for session (called when session ends)"""
    _session_todo_required.pop(session_id, None)
    _session_active_todos.pop(session_id, None)
    _session_handlers.pop(session_id, None)


def get_active_todo_sessions() -> dict[str, str]:
    """Return read-only copy of all active todos as {session_id: plan_id}"""
    return dict(_session_active_todos)


# Backward-compatible alias
iter_active_todo_sessions = get_active_todo_sessions


def _emit_todo_lifecycle_event(session_id: str, event_type: str, plan: dict | None = None) -> None:
    """Broadcast todo lifecycle event via WebSocket (for non-streaming paths)"""
    try:
        from ...api.routes.websocket import broadcast_event
        from ...core.engine_bridge import fire_in_api

        data: dict = {"sessionId": session_id, "type": event_type}
        if plan:
            data["planId"] = plan.get("id", "")
            data["status"] = plan.get("status", "")
        fire_in_api(broadcast_event(f"todo:{event_type}", data))
    except Exception as e:
        logger.debug(f"[Todo] Failed to emit lifecycle event {event_type}: {e}")


def auto_close_todo(session_id: str) -> bool:
    """
    Auto-close the active Todo for a session (called when task ends).

    When a ReAct loop ends but LLM doesn't explicitly call complete_todo,
    this function ensures the Todo is properly finalized.

    **Multi-turn plan protection**: If the plan still has pending steps (not yet executed),
    it's a multi-turn plan and this turn only completed some steps. In this case, don't close
    the plan, just mark in_progress steps as completed, and keep pending steps
    for the next turn to continue.

    Returns:
        True if a Todo was closed, False if no active Todo (or plan was preserved)
    """
    if not has_active_todo(session_id):
        return False

    handler = get_todo_handler_for_session(session_id)
    plan = handler.get_plan_for(session_id) if handler else None
    if not handler or not plan:
        unregister_active_todo(session_id)
        return True

    steps = plan.get("steps", [])
    has_pending = any(s.get("status") == "pending" for s in steps)

    if has_pending:
        # Multi-turn plan: keep plan alive, just snapshot in_progress steps.
        # TD2: Protect steps marked in_progress during the CURRENT turn —
        # if a step has a last_updated_turn matching the current turn_id,
        # leave it in_progress for the next turn to avoid the race condition
        # where auto_close runs right after the LLM sets a step in_progress.
        from datetime import datetime as _dt

        _now = _dt.now().isoformat()
        current_turn = plan.get("_current_turn_id", "")
        for step in steps:
            if step.get("status") == "in_progress":
                step_turn = step.get("_last_updated_turn", "")
                if current_turn and step_turn == current_turn:
                    continue
                step["status"] = "completed"
                step["result"] = step.get("result") or "(auto-marked completed this turn)"
                step["completed_at"] = _now
        # Persist intermediate state
        if hasattr(handler, "_store"):
            handler._store.upsert(session_id, plan)
            handler._store.save()
        logger.info(
            f"[Todo] Plan for {session_id} has {sum(1 for s in steps if s.get('status') == 'pending')} "
            f"pending steps, keeping alive for next turn"
        )
        return False

    handler.finalize_plan(plan, session_id, action="auto_close")
    logger.info(f"[Todo] Auto-closed todo for session {session_id}")

    unregister_active_todo(session_id)
    _emit_todo_lifecycle_event(session_id, "todo_completed", plan)
    return True


def cancel_todo(session_id: str) -> bool:
    """
    Close active Todo when user cancels.

    Unlike auto_close_todo, this function marks plan and incomplete steps as cancelled.

    Returns:
        True if a Todo was cancelled, False if no active Todo
    """
    if not has_active_todo(session_id):
        return False

    handler = get_todo_handler_for_session(session_id)
    plan = handler.get_plan_for(session_id) if handler else None
    if not handler or not plan:
        unregister_active_todo(session_id)
        return True

    handler.finalize_plan(plan, session_id, action="cancel")
    logger.info(f"[Todo] Cancelled todo for session {session_id}")

    unregister_active_todo(session_id)
    _emit_todo_lifecycle_event(session_id, "todo_cancelled", plan)
    return True


def force_close_plan(session_id: str) -> bool:
    """
    Force-close Plan state for a session (for deadlock recovery).

    Unconditionally clear all Plan module-level state associated with the session,
    whether handler instances or plan data are reachable or not.
    Used to break deadlock where todo_required=True + has_active_todo=False.

    Returns:
        True if any state was cleaned up
    """
    had_state = False
    if session_id in _session_active_todos:
        plan_id = _session_active_todos.pop(session_id)
        logger.warning(f"[Plan] Force-closed active todo {plan_id} for {session_id}")
        had_state = True
    if session_id in _session_todo_required:
        del _session_todo_required[session_id]
        had_state = True
    handler = _session_handlers.get(session_id)
    if handler:
        handler._todos_by_session.pop(session_id, None)
        if handler.current_todo and handler._get_conversation_id() == session_id:
            handler.current_todo = None
        try:
            handler._store.remove(session_id)
            handler._store.save()
        except Exception:
            pass
        had_state = True
    _session_handlers.pop(session_id, None)
    if had_state:
        logger.warning(f"[Plan] Force-closed all plan state for session {session_id}")
    return had_state


def register_plan_handler(session_id: str, handler: "PlanHandler") -> None:
    """Register a PlanHandler instance"""
    _prune_oldest_sessions()
    _session_handlers[session_id] = handler
    logger.debug(f"[Plan] Registered handler for session {session_id}")


def get_todo_handler_for_session(session_id: str) -> Optional["PlanHandler"]:
    """Get the PlanHandler instance for the given session"""
    return _session_handlers.get(session_id)


def get_active_todo_prompt(session_id: str) -> str:
    """
    Get the active Todo prompt section for the session (for injection into system_prompt).

    Returns a compact plan summary including all steps and their current status.
    Returns an empty string if there is no active Todo or the Todo is completed.
    """
    handler = get_todo_handler_for_session(session_id)
    if handler:
        return handler.get_plan_prompt_section(conversation_id=session_id)
    return ""


# Backward-compatible aliases (deprecated — use the *_todo variants)
unregister_active_plan = unregister_active_todo
clear_session_plan_state = clear_session_todo_state
auto_close_plan = auto_close_todo
cancel_plan = cancel_todo
get_plan_handler_for_session = get_todo_handler_for_session
get_active_plan_prompt = get_active_todo_prompt
has_active_plan = has_active_todo
register_active_plan = register_active_todo
require_plan_for_session = require_todo_for_session
is_plan_required = is_todo_required
