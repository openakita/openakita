"""
Session Todo 状态管理 + 生命周期函数

从 plan.py 拆分而来，负责：
- 模块级字典管理（_session_active_todos / _session_todo_required / _session_handlers）
- 注册、注销、查询、清理函数
- auto_close_todo / cancel_todo / force_close_plan 等生命周期函数
"""

import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .todo_handler import PlanHandler

logger = logging.getLogger(__name__)

__all__ = [
    # Public API
    "require_todo_for_session", "is_todo_required",
    "has_active_todo", "get_active_plan_id",
    "register_active_todo", "unregister_active_todo",
    "clear_session_todo_state",
    "auto_close_todo", "cancel_todo", "force_close_plan",
    "register_plan_handler", "get_todo_handler_for_session",
    "get_active_todo_prompt",
    "iter_active_todo_sessions",
    # Private but depended on externally (transition period)
    "_session_active_todos", "_session_todo_required", "_session_handlers",
    "_emit_todo_lifecycle_event",
    # Backward-compatible aliases
    "has_active_plan", "get_active_plan_prompt",
    "require_plan_for_session", "is_plan_required",
    "register_active_plan", "unregister_active_plan",
    "clear_session_plan_state",
    "auto_close_plan", "cancel_plan",
    "get_plan_handler_for_session",
]

# ============================================
# Session Todo 状态管理（模块级别）
# ============================================

# 记录哪些 session 被标记为需要 Todo（compound 任务）
_session_todo_required: dict[str, bool] = {}

# 记录 session 的活跃 Todo（session_id -> plan_id）
_session_active_todos: dict[str, str] = {}

# 存储 session -> PlanHandler 实例的映射（用于任务完成判断时查询 Plan 状态）
_session_handlers: dict[str, "PlanHandler"] = {}


def require_todo_for_session(session_id: str, required: bool) -> None:
    """标记 session 是否需要 Todo（由 Prompt Compiler 调用）"""
    _session_todo_required[session_id] = required
    logger.info(f"[Plan] Session {session_id} todo_required={required}")


def is_todo_required(session_id: str) -> bool:
    """检查 session 是否被标记为需要 Todo"""
    return _session_todo_required.get(session_id, False)


def has_active_todo(session_id: str) -> bool:
    """检查 session 是否有活跃的 Todo"""
    return session_id in _session_active_todos


def get_active_plan_id(session_id: str) -> str | None:
    """获取 session 当前活跃 Todo 的 plan_id（供 SSE 事件同步用）"""
    return _session_active_todos.get(session_id)


def register_active_todo(session_id: str, plan_id: str) -> None:
    """注册活跃的 Todo"""
    _session_active_todos[session_id] = plan_id
    logger.info(f"[Plan] Registered active todo {plan_id} for session {session_id}")


def unregister_active_todo(session_id: str) -> None:
    """注销活跃的 Todo（保留 handler 以支持后续新建 todo）"""
    if session_id in _session_active_todos:
        todo_id = _session_active_todos.pop(session_id)
        logger.info(f"[Todo] Unregistered todo {todo_id} for session {session_id}")
    if session_id in _session_todo_required:
        del _session_todo_required[session_id]


def clear_session_todo_state(session_id: str) -> None:
    """清除 session 的所有 Todo 状态（会话结束时调用）"""
    _session_todo_required.pop(session_id, None)
    _session_active_todos.pop(session_id, None)
    _session_handlers.pop(session_id, None)


def iter_active_todo_sessions() -> dict[str, str]:
    """返回所有活跃 todo 的 {session_id: plan_id} 副本（只读）"""
    return dict(_session_active_todos)


def _emit_todo_lifecycle_event(session_id: str, event_type: str, plan: dict | None = None) -> None:
    """通过 WebSocket 广播 todo 生命周期事件（供非流式路径使用）"""
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
    自动关闭指定 session 的活跃 Todo（任务结束时调用）。

    当一轮 ReAct 循环结束但 LLM 未显式调用 complete_todo 时，
    此函数确保 Todo 被正确收尾：
    - in_progress 步骤 -> completed（已开始执行，视为完成）
    - pending 步骤 -> skipped（未执行到）
    - Todo 状态设为 completed，保存并注销

    Returns:
        True 如果有 Todo 被关闭，False 如果没有活跃 Todo
    """
    if not has_active_todo(session_id):
        return False

    handler = get_todo_handler_for_session(session_id)
    plan = handler.get_plan_for(session_id) if handler else None
    if not handler or not plan:
        unregister_active_todo(session_id)
        return True

    handler.finalize_plan(plan, session_id, action="auto_close")
    logger.info(f"[Todo] Auto-closed todo for session {session_id}")

    unregister_active_todo(session_id)
    _emit_todo_lifecycle_event(session_id, "todo_completed", plan)
    return True


def cancel_todo(session_id: str) -> bool:
    """
    用户主动取消时关闭活跃 Todo。

    与 auto_close_todo 不同，此函数将计划和未完成步骤标记为 cancelled。

    Returns:
        True 如果有 Todo 被取消，False 如果没有活跃 Todo
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
    强制关闭指定 session 的 Plan 状态（死锁恢复用）。

    无条件清除所有与该 session 关联的 Plan 模块级状态，
    无论 handler 实例或 plan 数据是否可达。
    用于打破 todo_required=True + has_active_todo=False 的死锁。

    Returns:
        True 如果清理了任何状态
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
        had_state = True
    if had_state:
        logger.warning(f"[Plan] Force-closed all plan state for session {session_id}")
    return had_state


def register_plan_handler(session_id: str, handler: "PlanHandler") -> None:
    """注册 PlanHandler 实例"""
    _session_handlers[session_id] = handler
    logger.debug(f"[Plan] Registered handler for session {session_id}")


def get_todo_handler_for_session(session_id: str) -> Optional["PlanHandler"]:
    """获取 session 对应的 PlanHandler 实例"""
    return _session_handlers.get(session_id)


def get_active_todo_prompt(session_id: str) -> str:
    """
    获取 session 对应的活跃 Todo 提示词段落（注入 system_prompt 用）。

    返回紧凑格式的计划摘要，包含所有步骤及其当前状态。
    如果没有活跃 Todo 或 Todo 已完成，返回空字符串。
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
