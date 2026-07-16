"""Proactive IM delivery for unattended approval requests."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from openakita.scheduler.delivery import is_im_delivery_channel

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ApprovalNotificationTarget:
    channel: str
    chat_id: str
    user_id: str
    task_name: str = ""


def build_pending_approval_event_hook(
    *,
    loop: asyncio.AbstractEventLoop,
    fire_event: Callable[[str, dict[str, Any]], None],
    notify_owner: Callable[[dict[str, Any]], Awaitable[None]],
) -> Callable[[str, dict[str, Any]], None]:
    """Build the Store's sync hook while keeping async IM work on the API loop."""

    notification_tasks: set[asyncio.Task[None]] = set()

    def _hook(event_type: str, payload: dict[str, Any]) -> None:
        fire_event(event_type, payload)
        if event_type != "pending_approval_created":
            return

        payload_copy = dict(payload)

        def _schedule_notification() -> None:
            task = asyncio.create_task(notify_owner(payload_copy))
            notification_tasks.add(task)
            task.add_done_callback(notification_tasks.discard)

        try:
            loop.call_soon_threadsafe(_schedule_notification)
        except RuntimeError:
            logger.debug("[PendingApprovals] API loop unavailable; skipped IM notification")

    return _hook


def resolve_approval_notification_target(
    payload: dict[str, Any],
    *,
    scheduler: Any | None,
    gateway: Any | None,
) -> ApprovalNotificationTarget | None:
    """Resolve only the task owner or the exact originating IM session."""

    task_id = str(payload.get("task_id") or "").strip()
    if task_id and scheduler is not None and hasattr(scheduler, "get_task"):
        task = scheduler.get_task(task_id)
        if task is not None:
            channel = str(getattr(task, "channel_id", "") or "").strip()
            chat_id = str(getattr(task, "chat_id", "") or "").strip()
            if channel and chat_id and is_im_delivery_channel(channel):
                return ApprovalNotificationTarget(
                    channel=channel,
                    chat_id=chat_id,
                    user_id=str(getattr(task, "user_id", "") or "system"),
                    task_name=str(getattr(task, "name", "") or ""),
                )
            return None

    session_id = str(payload.get("session_id") or "").strip()
    session_manager = getattr(gateway, "session_manager", None) if gateway else None
    if session_id and session_manager is not None and hasattr(session_manager, "get_session_by_id"):
        session = session_manager.get_session_by_id(session_id)
        if session is not None:
            channel = str(getattr(session, "channel", "") or "").strip()
            chat_id = str(getattr(session, "chat_id", "") or "").strip()
            if channel and chat_id and is_im_delivery_channel(channel):
                return ApprovalNotificationTarget(
                    channel=channel,
                    chat_id=chat_id,
                    user_id=str(getattr(session, "user_id", "") or "system"),
                )

    return None


def format_pending_approval_notification(
    payload: dict[str, Any], target: ApprovalNotificationTarget
) -> str:
    approval_id = str(payload.get("id") or "").strip()
    tool_name = str(payload.get("tool_name") or "unknown").strip()
    reason = str(payload.get("reason") or "owner approval required").strip()
    task_line = f"后台任务：{target.task_name}\n" if target.task_name else ""
    return (
        "⚠️ **需要审批**\n\n"
        f"{task_line}"
        "任务已暂停，等待你确认工具调用。\n"
        f"工具：`{tool_name}`\n"
        f"原因：{reason}\n"
        f"审批编号：`{approval_id}`\n\n"
        "请在 OpenAkita 控制台的「待审批」页面批准或拒绝。"
    )


async def notify_pending_approval_im(
    payload: dict[str, Any],
    *,
    scheduler: Any | None,
    gateway: Any | None,
) -> bool:
    """Deliver one approval notification without affecting approval persistence."""

    if gateway is None:
        return False

    try:
        target = resolve_approval_notification_target(
            payload,
            scheduler=scheduler,
            gateway=gateway,
        )
    except Exception:
        logger.warning(
            "[PendingApprovals] failed to resolve IM target for approval %s",
            payload.get("id"),
            exc_info=True,
        )
        return False
    if target is None:
        logger.info(
            "[PendingApprovals] no owner-scoped IM target for approval %s",
            payload.get("id"),
        )
        return False

    text = format_pending_approval_notification(payload, target)
    try:
        if hasattr(gateway, "send_text_reliably"):
            delivered = bool(
                await gateway.send_text_reliably(
                    channel=target.channel,
                    chat_id=target.chat_id,
                    text=text,
                    user_id=target.user_id,
                    metadata={
                        "event": "pending_approval_created",
                        "approval_id": str(payload.get("id") or ""),
                    },
                )
            )
        else:
            delivered = (
                await gateway.send(
                    channel=target.channel,
                    chat_id=target.chat_id,
                    text=text,
                    user_id=target.user_id,
                    metadata={
                        "event": "pending_approval_created",
                        "approval_id": str(payload.get("id") or ""),
                    },
                )
                is not None
            )
    except Exception:
        logger.warning(
            "[PendingApprovals] IM notification failed for approval %s target=%s/%s",
            payload.get("id"),
            target.channel,
            target.chat_id,
            exc_info=True,
        )
        return False

    if not delivered:
        logger.warning(
            "[PendingApprovals] IM notification was not delivered for approval %s target=%s/%s",
            payload.get("id"),
            target.channel,
            target.chat_id,
        )
    return delivered
