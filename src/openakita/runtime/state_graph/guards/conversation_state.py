"""Conversation-state guards.

Extracted from ``core/reasoning_engine.py`` (P-RC-5 P5.7) as the
post-Decision validators that decide whether the engine should
keep running tools or hand back to the user.

* :func:`looks_like_waiting_for_user_response` -- whether a final
  answer is a genuine user-handoff (需要你/请手动/浏览器被关闭)
  rather than a task promise; the completion-verification path
  uses this to avoid pushing back into tool execution after the
  model already reported a blocker.
* :func:`has_recoverable_tool_issue` -- whether the latest blocker
  is a tool-call shape issue the model can repair (unknown tool,
  required argument missing) versus a hard user blocker (browser
  closed, captcha, permission denied).

Both functions are byte-identical to their legacy counterparts;
the four supporting word-list tuples (``USER_BLOCKED_MARKERS``,
``USER_BLOCKED_ACTIONS``, ``RECOVERABLE_TOOL_ERROR_MARKERS``,
``HARD_USER_BLOCKER_TOOL_MARKERS``) are also byte-identical.
``core/reasoning_engine.py`` re-imports all six symbols under
their legacy private names for backward compatibility.
"""

from __future__ import annotations

__all__ = [
    "HARD_USER_BLOCKER_TOOL_MARKERS",
    "RECOVERABLE_TOOL_ERROR_MARKERS",
    "USER_BLOCKED_ACTIONS",
    "USER_BLOCKED_MARKERS",
    "has_recoverable_tool_issue",
    "looks_like_waiting_for_user_response",
]


USER_BLOCKED_MARKERS = (
    "无法继续",
    "不能继续",
    "没法继续",
    "需要用户",
    "需要你",
    "请手动",
    "等待用户",
    "卡住",
    "卡在",
    "遇到技术障碍",
    "需要人工",
    "需要协助",
    "需要帮助",
    "需要登录",
    "验证码",
    "权限不足",
    "浏览器已关闭",
    "浏览器被关闭",
    "被用户关闭",
)

USER_BLOCKED_ACTIONS = (
    "无法",
    "不能",
    "没法",
    "失败",
    "超时",
    "卡住",
    "卡在",
    "阻塞",
    "需要",
    "等待",
)

RECOVERABLE_TOOL_ERROR_MARKERS = (
    "未知工具",
    "unknown_tool",
    "No handler mapped for tool",
    "is deferred",
    "must first call tool_search",
    "selector and text is required",
    "selector or text is required",
)

HARD_USER_BLOCKER_TOOL_MARKERS = (
    "浏览器连接已断开",
    "浏览器已被用户关闭",
    "浏览器被用户关闭",
    "验证码",
    "需要用户确认",
    "权限不足",
)


def looks_like_waiting_for_user_response(text: str) -> bool:
    """Whether a post-tool final answer is a real user handoff, not a task promise.

    This protects long ReAct tasks from being pushed back into tool execution by
    completion verification after the model has already reported a blocker such
    as "需要你截图/请手动确认/浏览器被关闭". Those replies are valid stopping
    points: the next step must come from the user, not another forced tool call.
    """
    normalized = (text or "").strip()
    if not normalized:
        return False

    lowered = normalized.lower()
    if any(
        marker in lowered
        for marker in (
            "waiting for user",
            "need your help",
            "need you to",
            "please provide",
            "please confirm",
            "manual confirmation",
            "cannot continue",
            "can't continue",
            "blocked",
        )
    ):
        return True

    if any(marker in normalized for marker in USER_BLOCKED_MARKERS):
        return True

    if "请" in normalized and any(
        marker in normalized
        for marker in (
            "手动",
            "确认",
            "提供",
            "截图",
            "验证码",
            "登录",
            "权限",
        )
    ):
        return True

    # More conservative composite check for phrases that split the blocker and
    # the requested user action across a sentence.
    has_blocker = any(marker in normalized for marker in USER_BLOCKED_ACTIONS)
    asks_user = any(
        marker in normalized
        for marker in (
            "你",
            "用户",
            "手动",
            "确认",
            "提供",
            "截图",
            "验证码",
            "登录",
            "权限",
        )
    )
    return has_blocker and asks_user


def has_recoverable_tool_issue(tool_results: list[dict] | None) -> bool:
    """Whether the latest blocker is a tool-call shape issue the model can repair."""
    for result in tool_results or []:
        content = str(result.get("content") or "")
        if not content:
            continue
        if any(marker in content for marker in HARD_USER_BLOCKER_TOOL_MARKERS):
            return False
        is_error = result.get("is_error")
        if is_error and any(marker in content for marker in RECOVERABLE_TOOL_ERROR_MARKERS):
            return True
    return False


