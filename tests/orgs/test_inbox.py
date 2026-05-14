"""Tests for OrgInbox — push, filter, approval, subscription."""

from __future__ import annotations

import asyncio

import pytest

from openakita.orgs.inbox import OrgInbox
from openakita.orgs.models import InboxPriority


@pytest.fixture()
def inbox(mock_runtime) -> OrgInbox:
    return OrgInbox(mock_runtime)


class TestPush:
    def test_push_basic(self, inbox: OrgInbox, persisted_org):
        msg = inbox.push(persisted_org.id, "任务完成", "节点A完成了任务X")
        assert msg.id.startswith("inbox_")
        assert msg.title == "任务完成"
        assert msg.status == "unread"

    def test_push_with_approval(self, inbox: OrgInbox, persisted_org):
        msg = inbox.push(
            persisted_org.id, "扩编审批", "请求克隆节点",
            requires_approval=True,
            priority=InboxPriority.APPROVAL,
        )
        assert msg.requires_approval is True
        assert msg.approval_id is not None
        assert msg.approval_id.startswith("#A")

    def test_push_populates_org_name(self, inbox: OrgInbox, persisted_org):
        msg = inbox.push(persisted_org.id, "test", "body")
        assert msg.org_name == persisted_org.name

    def test_push_task_complete_helper(self, inbox: OrgInbox, persisted_org):
        msg = inbox.push_task_complete(persisted_org.id, "node_ceo", "构建任务", "成功")
        assert msg is not None
        assert msg.id.startswith("inbox_")

    def test_push_approval_request_helper(self, inbox: OrgInbox, persisted_org):
        msg = inbox.push_approval_request(
            persisted_org.id, "node_ceo", "克隆请求", "需要更多人手",
        )
        assert msg.requires_approval is True
        assert msg.priority == InboxPriority.APPROVAL


class TestListAndFilter:
    def test_list_all(self, inbox: OrgInbox, persisted_org):
        inbox.push(persisted_org.id, "A", "body_a")
        inbox.push(persisted_org.id, "B", "body_b")
        messages = inbox.list_messages(persisted_org.id)
        assert len(messages) == 2

    def test_list_unread_only(self, inbox: OrgInbox, persisted_org):
        msg = inbox.push(persisted_org.id, "A", "body")
        inbox.mark_read(persisted_org.id, msg.id)
        inbox.push(persisted_org.id, "B", "body")

        unread = inbox.list_messages(persisted_org.id, unread_only=True)
        assert len(unread) == 1
        assert unread[0].title == "B"

    def test_list_pending_approvals(self, inbox: OrgInbox, persisted_org):
        inbox.push(persisted_org.id, "普通", "x")
        inbox.push(persisted_org.id, "审批", "y", requires_approval=True)

        pending = inbox.list_messages(persisted_org.id, pending_approval_only=True)
        assert len(pending) == 1
        assert pending[0].requires_approval is True


class TestMarkRead:
    def test_mark_read(self, inbox: OrgInbox, persisted_org):
        msg = inbox.push(persisted_org.id, "X", "body")
        inbox.mark_read(persisted_org.id, msg.id)
        got = inbox.get_message(persisted_org.id, msg.id)
        assert got is not None
        assert got.status == "read"

    def test_mark_all_read(self, inbox: OrgInbox, persisted_org):
        inbox.push(persisted_org.id, "A", "body")
        inbox.push(persisted_org.id, "B", "body")
        inbox.mark_all_read(persisted_org.id)

        count = inbox.unread_count(persisted_org.id)
        assert count == 0


class TestUnreadCount:
    def test_unread_count(self, inbox: OrgInbox, persisted_org):
        inbox.push(persisted_org.id, "A", "body")
        inbox.push(persisted_org.id, "B", "body")
        assert inbox.unread_count(persisted_org.id) == 2

    def test_pending_approval_count(self, inbox: OrgInbox, persisted_org):
        inbox.push(persisted_org.id, "普通", "x")
        inbox.push(persisted_org.id, "审批1", "y", requires_approval=True)
        inbox.push(persisted_org.id, "审批2", "z", requires_approval=True)
        assert inbox.pending_approval_count(persisted_org.id) == 2


class TestApproval:
    def test_resolve_approval(self, inbox: OrgInbox, persisted_org):
        msg = inbox.push(
            persisted_org.id, "审批", "请批准",
            requires_approval=True,
        )
        result = inbox.resolve_approval(persisted_org.id, msg.id, "approve", by="user")
        assert result is not None
        assert result.acted_result == "approve"
        assert result.acted_by == "user"
        assert result.status == "acted"

    def test_resolve_by_approval_id(self, inbox: OrgInbox, persisted_org):
        msg = inbox.push(
            persisted_org.id, "审批", "body",
            requires_approval=True,
        )
        result = inbox.resolve_by_approval_id(
            persisted_org.id, msg.approval_id, "reject", by="admin",
        )
        assert result is not None
        assert result.acted_result == "reject"

    def test_resolve_non_approval_returns_none(self, inbox: OrgInbox, persisted_org):
        msg = inbox.push(persisted_org.id, "普通", "body")
        result = inbox.resolve_approval(persisted_org.id, msg.id, "approve")
        assert result is None

    def test_resolve_already_acted_returns_none(self, inbox: OrgInbox, persisted_org):
        msg = inbox.push(persisted_org.id, "审批", "body", requires_approval=True)
        inbox.resolve_approval(persisted_org.id, msg.id, "approve")
        result2 = inbox.resolve_approval(persisted_org.id, msg.id, "reject")
        assert result2 is None


class TestPushHelpers:
    def test_push_progress(self, inbox: OrgInbox, persisted_org):
        msg = inbox.push_progress(persisted_org.id, "node_ceo", "进度更新", "已完成50%")
        assert msg.category == "progress"
        assert msg.priority == InboxPriority.INFO

    def test_push_warning(self, inbox: OrgInbox, persisted_org):
        msg = inbox.push_warning(persisted_org.id, "node_dev", "磁盘告警", "磁盘使用率超过90%")
        assert msg.category == "warning"
        assert msg.priority == InboxPriority.WARNING


class TestSubscription:
    async def test_subscribe_receives_messages(self, inbox: OrgInbox, persisted_org):
        q = inbox.subscribe(persisted_org.id)
        inbox.push(persisted_org.id, "新消息", "body")
        try:
            msg = await asyncio.wait_for(q.get(), timeout=1.0)
            assert msg.title == "新消息"
        finally:
            inbox.unsubscribe(persisted_org.id, q)

