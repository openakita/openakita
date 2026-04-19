"""
OrgInbox — Unified Message Inbox

Aggregates various organizational events (task completion, progress changes,
approval requests, etc.) with priority sorting and inline approval support.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .runtime import OrgRuntime

from .models import InboxMessage, InboxPriority, _new_id, _now_iso

logger = logging.getLogger(__name__)


class OrgInbox:
    """In-memory inbox aggregating organizational messages per user."""

    MAX_MESSAGES_PER_ORG = 500
    APPROVAL_PREFIX = "#A"

    def __init__(self, runtime: OrgRuntime) -> None:
        self._runtime = runtime
        self._messages: dict[str, list[InboxMessage]] = defaultdict(list)
        self._next_approval_seq: dict[str, int] = {}
        self._listeners: dict[str, list[asyncio.Queue]] = defaultdict(list)

    # ------------------------------------------------------------------
    # Push messages
    # ------------------------------------------------------------------

    def push(
        self,
        org_id: str,
        title: str,
        body: str,
        *,
        priority: InboxPriority = InboxPriority.INFO,
        source_node: str = "",
        category: str = "general",
        requires_approval: bool = False,
        approval_options: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> InboxMessage:
        approval_id: str | None = None
        if requires_approval:
            seq = self._next_approval_seq.get(org_id, 1)
            approval_id = f"{self.APPROVAL_PREFIX}{seq}"
            self._next_approval_seq[org_id] = seq + 1

        org_name = ""
        org = self._runtime.get_org(org_id)
        if org:
            org_name = org.name

        source_display = source_node
        if org and source_node:
            for n in org.nodes:
                if n.id == source_node:
                    source_display = n.role_title or source_node
                    break

        msg = InboxMessage(
            id=_new_id("inbox_"),
            org_id=org_id,
            org_name=org_name,
            title=title,
            body=body,
            priority=priority,
            source_node=source_display,
            category=category,
            requires_approval=requires_approval,
            approval_options=approval_options or (["approve", "reject"] if requires_approval else []),
            approval_id=approval_id,
            metadata=metadata or {},
        )

        bucket = self._messages[org_id]
        bucket.append(msg)

        if len(bucket) > self.MAX_MESSAGES_PER_ORG:
            bucket.sort(key=lambda m: (
                0 if m.requires_approval and m.status != "acted" else 1,
                m.created_at,
            ))
            bucket[:] = bucket[-self.MAX_MESSAGES_PER_ORG:]

        self._notify_listeners(org_id, msg)
        return msg

    def push_task_complete(
        self, org_id: str, node_id: str, task_name: str, result_summary: str,
    ) -> InboxMessage:
        return self.push(
            org_id,
            title=f"Task completed: {task_name}",
            body=result_summary[:500],
            source_node=node_id,
            category="task_complete",
        )

    def push_approval_request(
        self, org_id: str, node_id: str,
        title: str, body: str,
        options: list[str] | None = None,
        metadata: dict | None = None,
    ) -> InboxMessage:
        return self.push(
            org_id,
            title=title,
            body=body,
            priority=InboxPriority.APPROVAL,
            source_node=node_id,
            category="approval",
            requires_approval=True,
            approval_options=options,
            metadata=metadata,
        )

    def push_progress(
        self, org_id: str, node_id: str, title: str, body: str,
    ) -> InboxMessage:
        return self.push(
            org_id, title=title, body=body,
            priority=InboxPriority.INFO,
            source_node=node_id, category="progress",
        )

    def push_warning(
        self, org_id: str, node_id: str, title: str, body: str,
    ) -> InboxMessage:
        return self.push(
            org_id, title=title, body=body,
            priority=InboxPriority.WARNING,
            source_node=node_id, category="warning",
        )

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def list_messages(
        self,
        org_id: str,
        *,
        unread_only: bool = False,
        category: str | None = None,
        pending_approval_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[InboxMessage]:
        bucket = self._messages.get(org_id, [])
        result = list(bucket)

        if unread_only:
            result = [m for m in result if m.status == "unread"]
        if category:
            result = [m for m in result if m.category == category]
        if pending_approval_only:
            result = [m for m in result if m.requires_approval and m.status != "acted"]

        priority_order = {
            InboxPriority.ALERT: 5,
            InboxPriority.APPROVAL: 4,
            InboxPriority.ACTION: 3,
            InboxPriority.WARNING: 2,
            InboxPriority.NOTICE: 1,
            InboxPriority.INFO: 0,
        }

        result.sort(key=lambda m: (
            -priority_order.get(m.priority, 0),
            0 if m.status != "acted" else 1,
            m.created_at,
        ))

        return result[offset : offset + limit]

    def get_message(self, org_id: str, msg_id: str) -> InboxMessage | None:
        for m in self._messages.get(org_id, []):
            if m.id == msg_id:
                return m
        return None

    def find_by_approval_id(self, org_id: str, approval_id: str) -> InboxMessage | None:
        for m in self._messages.get(org_id, []):
            if m.approval_id == approval_id:
                return m
        return None

    def mark_read(self, org_id: str, msg_id: str) -> bool:
        msg = self.get_message(org_id, msg_id)
        if msg and msg.status == "unread":
            msg.status = "read"
            return True
        return False

    def mark_all_read(self, org_id: str) -> int:
        count = 0
        for msg in self._messages.get(org_id, []):
            if msg.status == "unread":
                msg.status = "read"
                count += 1
        return count

    def unread_count(self, org_id: str) -> int:
        return sum(1 for m in self._messages.get(org_id, []) if m.status == "unread")

    def pending_approval_count(self, org_id: str) -> int:
        return sum(
            1 for m in self._messages.get(org_id, [])
            if m.requires_approval and m.status != "acted"
        )

    # ------------------------------------------------------------------
    # Approve / Reject
    # ------------------------------------------------------------------

    def resolve_approval(
        self, org_id: str, msg_id: str, decision: str, by: str = "user"
    ) -> InboxMessage | None:
        msg = self.get_message(org_id, msg_id)
        if not msg or not msg.requires_approval:
            return None
        if msg.status == "acted":
            return None

        msg.status = "acted"
        msg.acted_by = by
        msg.acted_result = decision
        msg.acted_at = _now_iso()

        if decision == "approve":
            self._execute_approval_side_effects(org_id, msg)

        self._runtime.get_event_store(org_id).emit(
            "approval_resolved", by,
            {"msg_id": msg.id, "approval_id": msg.approval_id, "decision": decision},
        )

        self._notify_listeners(org_id, msg)
        return msg

    def _execute_approval_side_effects(self, org_id: str, msg: InboxMessage) -> None:
        meta = msg.metadata
        if meta.get("policy_filename") and meta.get("policy_content"):
            try:
                org_dir = self._runtime._manager._org_dir(org_id)
                policies_dir = org_dir / "policies"
                policies_dir.mkdir(parents=True, exist_ok=True)
                policy_path = policies_dir / meta["policy_filename"]
                policy_path.write_text(meta["policy_content"], encoding="utf-8")
                logger.info(f"[OrgInbox] Wrote approved policy: {meta['policy_filename']}")
            except Exception as e:
                logger.error(f"[OrgInbox] Failed to write approved policy: {e}")

        if meta.get("action_type") == "create_schedule":
            try:
                from .models import NodeSchedule, ScheduleType
                params = meta.get("schedule_params", {})
                target_node = meta.get("node_id", "")
                if params and target_node:
                    sched = NodeSchedule(
                        name=params.get("name", ""),
                        schedule_type=ScheduleType(params.get("schedule_type", "interval")),
                        cron=params.get("cron"),
                        interval_s=params.get("interval_s"),
                        run_at=params.get("run_at"),
                        prompt=params.get("prompt", ""),
                        report_to=params.get("report_to"),
                        report_condition=params.get("report_condition", "on_issue"),
                        enabled=True,
                    )
                    self._runtime._manager.add_node_schedule(org_id, target_node, sched)
                    logger.info(f"[OrgInbox] Created approved schedule: {sched.name}")
            except Exception as e:
                logger.error(f"[OrgInbox] Failed to create approved schedule: {e}")

    def resolve_by_approval_id(
        self, org_id: str, approval_id: str, decision: str, by: str = "user"
    ) -> InboxMessage | None:
        msg = self.find_by_approval_id(org_id, approval_id)
        if not msg:
            return None
        return self.resolve_approval(org_id, msg.id, decision, by)

    # ------------------------------------------------------------------
    # Realtime subscriptions
    # ------------------------------------------------------------------

    def subscribe(self, org_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._listeners[org_id].append(q)
        return q

    def unsubscribe(self, org_id: str, q: asyncio.Queue) -> None:
        if q in self._listeners.get(org_id, []):
            self._listeners[org_id].remove(q)

    def _notify_listeners(self, org_id: str, msg: InboxMessage) -> None:
        for q in self._listeners.get(org_id, []):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass
