"""Organization event routing and output filtering.

The org console keeps receiving full `org:*` events. External surfaces such as
desktop chat and IM only receive summarized events for the command they started.
"""

from __future__ import annotations

import logging
from typing import Any

from .command_service import OrgOutputScope, get_command_service

logger = logging.getLogger(__name__)

_PUBLIC_EVENT_TYPES = {
    "org:node_status",
    "org:task_delegated",
    "org:task_delivered",
    "org:task_accepted",
    "org:task_rejected",
    "org:task_complete",
    "org:task_failed",
    "org:task_timeout",
    "org:command_stuck_warning",
    "org:command_done",
}


def _node_name(data: dict[str, Any]) -> str:
    return str(data.get("node_id") or data.get("from_node") or data.get("root_node_id") or "组织")


def summarize_org_event(event: str, data: dict[str, Any]) -> str | None:
    """Convert an internal org event into a short user-facing progress line."""
    node = _node_name(data)
    if event == "org:node_status":
        status = str(data.get("status") or "")
        task = str(data.get("current_task") or "")
        if status == "busy":
            return f"{node} 开始处理" + (f"：{task[:80]}" if task else "")
        if status == "idle":
            return f"{node} 已完成当前步骤"
        if status == "error":
            return f"{node} 执行出错"
        return None
    if event == "org:task_delegated":
        to_node = str(data.get("to_node") or "下级")
        task = str(data.get("task") or "")
        return f"{node} 已向 {to_node} 分配任务" + (f"：{task[:80]}" if task else "")
    if event == "org:task_delivered":
        summary = str(data.get("summary") or "")
        return f"{node} 已提交交付物" + (f"：{summary[:80]}" if summary else "")
    if event == "org:task_accepted":
        return f"{node} 的交付物已验收"
    if event == "org:task_rejected":
        reason = str(data.get("reason") or data.get("feedback") or "")
        return f"{node} 的交付物被打回" + (f"：{reason[:80]}" if reason else "")
    if event == "org:task_complete":
        preview = str(data.get("result_preview") or "")
        return f"{node} 任务完成" + (f"：{preview[:80]}" if preview else "")
    if event == "org:task_failed":
        return f"{node} 任务未完成"
    if event == "org:task_timeout":
        return f"{node} 任务超时"
    if event == "org:command_stuck_warning":
        idle_secs = data.get("idle_secs")
        return "组织一段时间无新进展，仍在等待收口" if not idle_secs else f"组织 {round(float(idle_secs))} 秒无新进展，仍在等待收口"
    return None


async def route_org_event(event: str, data: dict[str, Any] | None) -> None:
    """Publish filtered summaries for external command subscribers.

    Full WebSocket broadcasting remains owned by `OrgRuntime._broadcast_ws`.
    This router only sends per-command summaries into queues consumed by chat/IM.
    """
    if not data or not event.startswith("org:"):
        return

    service = get_command_service()
    if service is None:
        return

    org_id = str(data.get("org_id") or "")
    if not org_id:
        return

    command = service.find_command_for_event(org_id, data)
    if not command:
        return

    command_id = str(command.get("command_id") or "")
    output_scope = command.get("output_scope") or OrgOutputScope.CONSOLE_FULL.value
    if output_scope in (OrgOutputScope.CONSOLE_FULL.value, OrgOutputScope.INTERNAL.value):
        return
    if output_scope == OrgOutputScope.FINAL_ONLY.value and event != "org:command_done":
        return
    if event not in _PUBLIC_EVENT_TYPES:
        return

    summary = summarize_org_event(event, data)
    if not summary and event != "org:command_done":
        return

    payload: dict[str, Any] = {
        "type": "org_progress",
        "org_id": org_id,
        "command_id": command_id,
        "event": event,
        "summary": summary or "",
        "data": {
            "node_id": data.get("node_id") or data.get("from_node"),
            "to_node": data.get("to_node"),
            "status": data.get("status"),
        },
    }
    try:
        await service.publish_summary(command_id, payload)
    except Exception:
        logger.debug("[OrgEventRouter] publish failed for %s", event, exc_info=True)
