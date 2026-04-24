"""Tests for OrgNotifier — message formatting, approval reply parsing."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from openakita.orgs.notifier import OrgNotifier
from openakita.orgs.models import InboxMessage, InboxPriority


@pytest.fixture()
def notifier(mock_runtime) -> OrgNotifier:
    return OrgNotifier(mock_runtime)


class TestFormatMessage:
    def test_info_message(self, notifier: OrgNotifier):
        msg = InboxMessage(
            org_name="测试公司", priority=InboxPriority.INFO,
            title="任务完成", body="节点A完成了任务X",
        )
        text = notifier._format_message(msg)
        assert "[测试公司]" in text
        assert "任务完成" in text

    def test_approval_message_includes_id(self, notifier: OrgNotifier):
        msg = InboxMessage(
            org_name="测试公司", priority=InboxPriority.APPROVAL,
            title="克隆审批", body="请批准",
            requires_approval=True,
            approval_id="#A1",
            approval_options=["approve", "reject"],
        )
        text = notifier._format_message(msg)
        assert "#A1" in text

    def test_alert_label(self, notifier: OrgNotifier):
        msg = InboxMessage(
            priority=InboxPriority.ALERT,
            title="系统异常",
            body="节点出错",
        )
        text = notifier._format_message(msg)
        assert "紧急" in text

    def test_no_org_name(self, notifier: OrgNotifier):
        msg = InboxMessage(
            org_name="", priority=InboxPriority.NOTICE,
            title="测试", body="内容",
        )
        text = notifier._format_message(msg)
        assert "测试" in text


class TestParseApprovalReply:
    """Pattern is: #A<seq> <decision> (id before decision)."""

    def test_approve_by_id(self, notifier: OrgNotifier):
        aid, decision = notifier.parse_approval_reply("#A3 批准")
        assert aid == "#A3"
        assert decision == "approve"

    def test_reject_by_id(self, notifier: OrgNotifier):
        aid, decision = notifier.parse_approval_reply("#A5 拒绝")
        assert aid == "#A5"
        assert decision == "reject"

    def test_english_approve(self, notifier: OrgNotifier):
        aid, decision = notifier.parse_approval_reply("#A1 approve")
        assert aid == "#A1"
        assert decision == "approve"

    def test_no_match(self, notifier: OrgNotifier):
        aid, decision = notifier.parse_approval_reply("你好世界")
        assert aid is None
        assert decision is None


class TestNotify:
    async def test_notify_disabled(self, notifier: OrgNotifier, persisted_org):
        persisted_org.notify_enabled = False
        msg = InboxMessage(title="test", body="body")
        result = await notifier.notify(persisted_org.id, msg)
        assert result is False

    async def test_notify_no_channel(self, notifier: OrgNotifier, persisted_org):
        persisted_org.notify_enabled = True
        persisted_org.notify_channel = ""
        msg = InboxMessage(title="test", body="body")
        result = await notifier.notify(persisted_org.id, msg)
        assert result is False

    async def test_notify_org_not_found(self, notifier: OrgNotifier, mock_runtime):
        mock_runtime.get_org = MagicMock(return_value=None)
        msg = InboxMessage(title="test", body="body")
        result = await notifier.notify("fake", msg)
        assert result is False


class TestHandleImReply:
    async def test_no_approval_pattern(self, notifier: OrgNotifier, persisted_org):
        result = await notifier.handle_im_reply(persisted_org.id, "你好")
        assert result["matched"] is False

    async def test_valid_approval(self, notifier: OrgNotifier, persisted_org, mock_runtime):
        from openakita.orgs.inbox import OrgInbox
        inbox = OrgInbox(mock_runtime)
        mock_runtime.get_inbox = MagicMock(return_value=inbox)
        msg = inbox.push(persisted_org.id, "审批", "body", requires_approval=True)

        result = await notifier.handle_im_reply(
            persisted_org.id, f"{msg.approval_id} 批准", sender="admin"
        )
        assert result["matched"] is True
        assert result["decision"] == "approve"

    async def test_inbox_not_available(self, notifier: OrgNotifier, persisted_org, mock_runtime):
        mock_runtime.get_inbox = MagicMock(return_value=None)
        result = await notifier.handle_im_reply(persisted_org.id, "#A1 批准")
        assert result["matched"] is True
        assert "error" in result

