"""Tests for OrgToolHandler — tool routing and dispatch."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from openakita.orgs.models import NodeStatus
from openakita.orgs.tool_handler import OrgToolHandler


@pytest.fixture()
def handler(mock_runtime) -> OrgToolHandler:
    async def _set_node_status(org, node, new_status, reason="", *, current_task=""):
        node.status = new_status
        await mock_runtime._save_org(org)
        await mock_runtime._broadcast_ws("org:node_status", {
            "org_id": org.id,
            "node_id": node.id,
            "status": new_status.value,
            "current_task": current_task,
        })

    mock_runtime.set_node_status = AsyncMock(side_effect=_set_node_status)
    return OrgToolHandler(mock_runtime)


class TestToolRouting:
    async def test_unknown_tool_returns_error(self, handler: OrgToolHandler):
        result = await handler.handle("org_nonexistent", {}, "org_test", "node_ceo")
        assert isinstance(result, str)

    async def test_send_message(self, handler: OrgToolHandler, persisted_org):
        result = await handler.handle(
            "org_send_message",
            {"to_node": "node_cto", "content": "你好", "msg_type": "task_assign"},
            persisted_org.id, "node_ceo",
        )
        assert "已发送" in result or "发送" in result

    async def test_delegate_task(self, handler: OrgToolHandler, persisted_org):
        result = await handler.handle(
            "org_delegate_task",
            {"to_node": "node_cto", "task": "编写测试"},
            persisted_org.id, "node_ceo",
        )
        assert "已分配" in result or "任务" in result

    async def test_escalate(self, handler: OrgToolHandler, persisted_org):
        result = await handler.handle(
            "org_escalate",
            {"content": "遇到阻塞", "priority": 1},
            persisted_org.id, "node_cto",
        )
        assert "上报" in result or "上级" in result

    async def test_broadcast(self, handler: OrgToolHandler, persisted_org):
        result = await handler.handle(
            "org_broadcast",
            {"content": "全员通知", "scope": "organization"},
            persisted_org.id, "node_ceo",
        )
        assert isinstance(result, str)

    async def test_reply_message_unknown_target(self, handler: OrgToolHandler, persisted_org):
        result = await handler.handle(
            "org_reply_message",
            {"reply_to": "msg_fake", "content": "收到"},
            persisted_org.id, "node_cto",
        )
        assert "未找到" in result

    async def test_reply_message_with_pending(self, handler: OrgToolHandler, persisted_org):
        messenger = handler._runtime.get_messenger()
        msg = await messenger.send_task("node_ceo", "node_cto", "做个任务")
        result = await handler.handle(
            "org_reply_message",
            {"reply_to": msg.id, "content": "完成了"},
            persisted_org.id, "node_cto",
        )
        assert "已回复" in result


class TestOrgAwarenessTools:
    async def test_get_org_chart(self, handler: OrgToolHandler, persisted_org):
        result = await handler.handle(
            "org_get_org_chart", {}, persisted_org.id, "node_ceo",
        )
        data = json.loads(result) if isinstance(result, str) else result
        assert "departments" in data

    async def test_find_colleague(self, handler: OrgToolHandler, persisted_org):
        result = await handler.handle(
            "org_find_colleague", {"need": "技术"},
            persisted_org.id, "node_ceo",
        )
        assert isinstance(result, (str, list))

    async def test_get_node_status(self, handler: OrgToolHandler, persisted_org):
        result = await handler.handle(
            "org_get_node_status", {"node_id": "node_cto"},
            persisted_org.id, "node_ceo",
        )
        data = json.loads(result) if isinstance(result, str) else result
        assert "status" in data

    async def test_get_org_status(self, handler: OrgToolHandler, persisted_org):
        result = await handler.handle(
            "org_get_org_status", {}, persisted_org.id, "node_ceo",
        )
        data = json.loads(result) if isinstance(result, str) else result
        assert "org_name" in data or "status" in data


class TestMemoryTools:
    async def test_read_blackboard_empty(self, handler: OrgToolHandler, persisted_org):
        result = await handler.handle(
            "org_read_blackboard", {}, persisted_org.id, "node_ceo",
        )
        assert "暂无" in result or isinstance(result, str)

    async def test_write_and_read_blackboard(self, handler: OrgToolHandler, persisted_org):
        await handler.handle(
            "org_write_blackboard",
            {"content": "测试决策", "memory_type": "decision"},
            persisted_org.id, "node_ceo",
        )
        result = await handler.handle(
            "org_read_blackboard", {}, persisted_org.id, "node_ceo",
        )
        assert "测试决策" in result


class TestHRTools:
    async def test_freeze_node(self, handler: OrgToolHandler, persisted_org):
        result = await handler.handle(
            "org_freeze_node",
            {"node_id": "node_dev", "reason": "测试冻结"},
            persisted_org.id, "node_ceo",
        )
        assert "冻结" in result
        node = persisted_org.get_node("node_dev")
        assert node is not None
        assert node.status == NodeStatus.FROZEN
        handler._runtime.set_node_status.assert_awaited()
        handler._runtime._broadcast_ws.assert_awaited()

    async def test_unfreeze_node(self, handler: OrgToolHandler, persisted_org):
        await handler.handle(
            "org_freeze_node",
            {"node_id": "node_dev", "reason": "暂停"},
            persisted_org.id, "node_ceo",
        )
        result = await handler.handle(
            "org_unfreeze_node",
            {"node_id": "node_dev"},
            persisted_org.id, "node_ceo",
        )
        assert "解冻" in result
        node = persisted_org.get_node("node_dev")
        assert node is not None
        assert node.status == NodeStatus.IDLE


class TestDeptMemoryTools:
    async def test_read_dept_memory(self, handler: OrgToolHandler, persisted_org):
        result = await handler.handle(
            "org_read_dept_memory", {}, persisted_org.id, "node_cto",
        )
        assert "暂无" in result or "技术部" in result or isinstance(result, str)

    async def test_write_and_read_dept_memory(self, handler: OrgToolHandler, persisted_org):
        await handler.handle(
            "org_write_dept_memory",
            {"content": "部门决策X", "memory_type": "decision"},
            persisted_org.id, "node_cto",
        )
        result = await handler.handle(
            "org_read_dept_memory", {}, persisted_org.id, "node_cto",
        )
        assert "部门决策X" in result


class TestPolicyTools:
    async def test_list_policies_empty(self, handler: OrgToolHandler, persisted_org):
        result = await handler.handle(
            "org_list_policies", {}, persisted_org.id, "node_ceo",
        )
        assert "暂无" in result or isinstance(result, str)

    async def test_read_policy_not_found(self, handler: OrgToolHandler, persisted_org):
        result = await handler.handle(
            "org_read_policy", {"filename": "no-such.md"},
            persisted_org.id, "node_ceo",
        )
        assert "不存在" in result

    async def test_search_policy_no_results(self, handler: OrgToolHandler, persisted_org):
        result = await handler.handle(
            "org_search_policy", {"query": "不存在的制度"},
            persisted_org.id, "node_ceo",
        )
        assert "未找到" in result


class TestHRScalingTools:
    async def test_request_clone(self, handler: OrgToolHandler, persisted_org, mock_runtime):
        from openakita.orgs.scaler import OrgScaler
        scaler = OrgScaler(mock_runtime)
        mock_runtime.get_scaler = MagicMock(return_value=scaler)
        mock_runtime._save_org = AsyncMock()
        result = await handler.handle(
            "org_request_clone",
            {"source_node_id": "node_dev", "reason": "工作量大", "ephemeral": True},
            persisted_org.id, "node_cto",
        )
        assert "克隆" in result

    async def test_request_recruit(self, handler: OrgToolHandler, persisted_org, mock_runtime):
        from openakita.orgs.scaler import OrgScaler
        scaler = OrgScaler(mock_runtime)
        mock_runtime.get_scaler = MagicMock(return_value=scaler)
        result = await handler.handle(
            "org_request_recruit",
            {
                "role_title": "安全专员", "role_goal": "安全审计",
                "department": "技术部", "parent_node_id": "node_cto",
                "reason": "缺少安全人才",
            },
            persisted_org.id, "node_ceo",
        )
        assert "招募" in result or "申请" in result

    async def test_dismiss_node(self, handler: OrgToolHandler, persisted_org, mock_runtime):
        from openakita.orgs.scaler import OrgScaler
        scaler = OrgScaler(mock_runtime)
        mock_runtime.get_scaler = MagicMock(return_value=scaler)
        mock_runtime._save_org = AsyncMock()
        result = await handler.handle(
            "org_dismiss_node", {"node_id": "node_dev"},
            persisted_org.id, "node_ceo",
        )
        assert "裁撤" in result or "失败" in result


class TestMeetingTools:
    async def test_request_meeting_too_many_participants(self, handler: OrgToolHandler, persisted_org):
        result = await handler.handle(
            "org_request_meeting",
            {"participants": [f"p{i}" for i in range(7)], "topic": "全体会"},
            persisted_org.id, "node_ceo",
        )
        assert "上限" in result or "6" in result


class TestScheduleTools:
    async def test_list_my_schedules_empty(self, handler: OrgToolHandler, persisted_org):
        result = await handler.handle(
            "org_list_my_schedules", {},
            persisted_org.id, "node_ceo",
        )
        assert "暂无" in result or isinstance(result, str)

    async def test_create_schedule(self, handler: OrgToolHandler, persisted_org, mock_runtime):
        from openakita.orgs.inbox import OrgInbox
        inbox = OrgInbox(mock_runtime)
        mock_runtime.get_inbox = MagicMock(return_value=inbox)
        result = await handler.handle(
            "org_create_schedule",
            {"name": "巡检", "prompt": "检查服务器状态", "interval_s": 3600},
            persisted_org.id, "node_dev",
        )
        assert "巡检" in result
        assert "已提交审批" in result

    async def test_assign_schedule(self, handler: OrgToolHandler, persisted_org):
        result = await handler.handle(
            "org_assign_schedule",
            {"target_node_id": "node_dev", "name": "监控", "prompt": "查看日志"},
            persisted_org.id, "node_cto",
        )
        assert "监控" in result or "定时任务" in result


class TestPolicyProposal:
    async def test_propose_policy(self, handler: OrgToolHandler, persisted_org, mock_runtime):
        from openakita.orgs.inbox import OrgInbox
        inbox = OrgInbox(mock_runtime)
        mock_runtime.get_inbox = MagicMock(return_value=inbox)
        result = await handler.handle(
            "org_propose_policy",
            {
                "filename": "code-review.md", "title": "代码审查流程",
                "content": "所有代码需经过两人审查", "reason": "提高质量",
            },
            persisted_org.id, "node_cto",
        )
        assert "提交审批" in result or "制度" in result

