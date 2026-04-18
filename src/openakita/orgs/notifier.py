"""
OrgNotifier — IM push + tracking numbers + natural-language approval parsing

Pushes org messages to users via configured IM channels
(e.g. Feishu / DingTalk / WeChat Work / generic webhook)
and parses approval replies sent in natural language.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .runtime import OrgRuntime

from .models import InboxMessage, InboxPriority

logger = logging.getLogger(__name__)


APPROVAL_PATTERN = re.compile(
    r"[#＃]A\s*(\d+)\s*(批准|同意|approve|通过|拒绝|reject|驳回|否决)",
    re.IGNORECASE,
)

POSITIVE_DECISIONS = {"批准", "同意", "approve", "通过"}
NEGATIVE_DECISIONS = {"拒绝", "reject", "驳回", "否决"}


class OrgNotifier:
    """Push notifications to IM channels and parse approval replies."""

    def __init__(self, runtime: OrgRuntime) -> None:
        self._runtime = runtime

    async def notify(self, org_id: str, msg: InboxMessage) -> bool:
        """Send a notification for an inbox message via configured IM channel."""
        org = self._runtime.get_org(org_id)
        if not org:
            return False

        if not org.notify_enabled:
            return False

        channel = org.notify_channel
        webhook_url = org.notify_webhook_url or ""

        if not channel or not webhook_url:
            logger.debug(f"[Notifier] No channel configured for org {org_id}")
            return False

        text = self._format_message(msg)

        try:
            if channel == "feishu":
                return await self._send_feishu(webhook_url, text)
            elif channel == "dingtalk":
                return await self._send_dingtalk(webhook_url, text)
            elif channel == "wechat_work":
                return await self._send_wechat_work(webhook_url, text)
            elif channel == "webhook":
                return await self._send_generic_webhook(webhook_url, text, msg)
            else:
                logger.warning(f"[Notifier] Unknown channel: {channel}")
                return False
        except Exception as e:
            logger.error(f"[Notifier] Failed to send: {e}")
            return False

    def parse_approval_reply(self, text: str) -> tuple[str | None, str | None]:
        """
        Parse a natural language approval reply.
        Returns (approval_id, decision) or (None, None).
        Examples:
            "#A12 批准" -> ("#A12", "approve")
            "#A5 拒绝"  -> ("#A5", "reject")
        """
        match = APPROVAL_PATTERN.search(text)
        if not match:
            return None, None

        seq = match.group(1)
        raw_decision = match.group(2).lower()
        approval_id = f"#A{seq}"

        if raw_decision in {d.lower() for d in POSITIVE_DECISIONS}:
            decision = "approve"
        elif raw_decision in {d.lower() for d in NEGATIVE_DECISIONS}:
            decision = "reject"
        else:
            return None, None

        return approval_id, decision

    async def handle_im_reply(
        self, org_id: str, reply_text: str, sender: str = "user"
    ) -> dict[str, Any]:
        """Process an incoming IM message that may contain approval decisions."""
        approval_id, decision = self.parse_approval_reply(reply_text)
        if not approval_id or not decision:
            return {"matched": False, "reason": "No approval pattern found"}

        inbox = self._runtime.get_inbox(org_id)
        if not inbox:
            return {"matched": True, "error": "Inbox not available"}

        msg = inbox.resolve_by_approval_id(org_id, approval_id, decision, by=sender)
        if not msg:
            return {
                "matched": True,
                "error": f"Approval {approval_id} not found or already resolved",
            }

        return {
            "matched": True,
            "approval_id": approval_id,
            "decision": decision,
            "msg_id": msg.id,
            "title": msg.title,
        }

    # ------------------------------------------------------------------
    # Message formatting
    # ------------------------------------------------------------------

    def _format_message(self, msg: InboxMessage) -> str:
        priority_labels = {
            InboxPriority.ALERT: "Urgent",
            InboxPriority.APPROVAL: "Pending Approval",
            InboxPriority.ACTION: "Action Required",
            InboxPriority.WARNING: "Warning",
            InboxPriority.NOTICE: "Notice",
            InboxPriority.INFO: "Message",
        }
        label = priority_labels.get(msg.priority, "Message")

        org_prefix = f"[{msg.org_name}] " if msg.org_name else ""
        lines = [f"{org_prefix}[{label}] {msg.title}"]
        if msg.body:
            body_preview = msg.body[:300]
            if len(msg.body) > 300:
                body_preview += "..."
            lines.append(body_preview)

        if msg.requires_approval and msg.approval_id:
            lines.append(
                f"\nReply \"{msg.approval_id} approve\" or \"{msg.approval_id} reject\" to handle this item"
            )

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Channel adapters
    # ------------------------------------------------------------------

    async def _send_feishu(self, webhook_url: str, text: str) -> bool:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(webhook_url, json={
                "msg_type": "text",
                "content": {"text": text},
            })
            return resp.status_code == 200

    async def _send_dingtalk(self, webhook_url: str, text: str) -> bool:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(webhook_url, json={
                "msgtype": "text",
                "text": {"content": text},
            })
            return resp.status_code == 200

    async def _send_wechat_work(self, webhook_url: str, text: str) -> bool:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(webhook_url, json={
                "msgtype": "text",
                "text": {"content": text},
            })
            return resp.status_code == 200

    async def _send_generic_webhook(
        self, webhook_url: str, text: str, msg: InboxMessage
    ) -> bool:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(webhook_url, json={
                "event": "org_notification",
                "org_id": msg.org_id,
                "msg_id": msg.id,
                "title": msg.title,
                "body": text,
                "priority": msg.priority.value,
                "requires_approval": msg.requires_approval,
                "approval_id": msg.approval_id,
                "created_at": msg.created_at,
            })
            return resp.status_code == 200
