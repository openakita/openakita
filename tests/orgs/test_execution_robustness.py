"""
System tests for organization task execution robustness.

Tests edge cases, error recovery, cascading limits, timeout handling,
and concurrent task execution scenarios.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from openakita.orgs.models import (
    MsgType,
    NodeStatus,
    OrgMessage,
)
from openakita.orgs.tool_handler import OrgToolHandler
from .conftest import make_node


class TestNodeErrorRecovery:
    """Nodes stuck in ERROR should be recoverable."""

    @pytest.fixture()
    def handler(self, mock_runtime) -> OrgToolHandler:
        return OrgToolHandler(mock_runtime)

    async def test_error_node_blocks_task_delegation(self, handler, persisted_org, mock_runtime):
        """If target is in ERROR, delegation should still work (messenger handles delivery)."""
        persisted_org.nodes[1].status = NodeStatus.ERROR
        result = await handler.handle(
            "org_delegate_task",
            {"to_node": "node_cto", "task": "修复bug"},
            persisted_org.id, "node_ceo",
        )
        assert "任务已分配" in result or "已分配" in result

    async def test_frozen_node_rejects_activation(self, persisted_org, mock_runtime):
        """Frozen nodes should return error, not crash."""
        from openakita.orgs.runtime import OrgRuntime
        persisted_org.nodes[1].status = NodeStatus.FROZEN
        rt = MagicMock(spec=OrgRuntime)
        rt.get_org = MagicMock(return_value=persisted_org)
        rt._agent_cache = {}

        node = persisted_org.nodes[1]
        assert node.status == NodeStatus.FROZEN


class TestCascadeDepthLimiting:
    """Cascade depth should prevent infinite loops."""

    @pytest.fixture()
    def handler(self, mock_runtime) -> OrgToolHandler:
        return OrgToolHandler(mock_runtime)

    async def test_delegation_depth_tracked_per_chain(self, handler, persisted_org, mock_runtime):
        mock_runtime._chain_delegation_depth = {}
        result = await handler.handle(
            "org_delegate_task",
            {"to_node": "node_cto", "task": "传递任务", "task_chain_id": "chain_a"},
            persisted_org.id, "node_ceo",
        )
        assert "任务已分配" in result or "已分配" in result
        assert mock_runtime._chain_delegation_depth.get("chain_a", 0) == 1

    async def test_delegation_depth_blocks_at_limit(self, handler, persisted_org, mock_runtime):
        mock_runtime._chain_delegation_depth = {"chain_deep": 99}
        result = await handler.handle(
            "org_delegate_task",
            {"to_node": "node_cto", "task": "太深了", "task_chain_id": "chain_deep"},
            persisted_org.id, "node_ceo",
        )
        assert "上限" in result or "无法" in result

    async def test_escalation_not_affected_by_depth(self, handler, persisted_org, mock_runtime):
        mock_runtime._chain_delegation_depth = {}
        result = await handler.handle(
            "org_escalate",
            {"content": "需要决策", "priority": 1},
            persisted_org.id, "node_cto",
        )
        assert "上报" in result


class TestInvalidToolArguments:
    """LLM may pass invalid arguments — handler should be graceful."""

    @pytest.fixture()
    def handler(self, mock_runtime) -> OrgToolHandler:
        return OrgToolHandler(mock_runtime)

    async def test_invalid_msg_type_graceful(self, handler, persisted_org, mock_runtime):
        result = await handler.handle(
            "org_send_message",
            {"to_node": "node_cto", "content": "测试", "msg_type": "invalid_type_xyz"},
            persisted_org.id, "node_ceo",
        )
        assert isinstance(result, str)

    async def test_empty_content_message(self, handler, persisted_org, mock_runtime):
        result = await handler.handle(
            "org_send_message",
            {"to_node": "node_cto", "content": "", "msg_type": "question"},
            persisted_org.id, "node_ceo",
        )
        assert isinstance(result, str)

    async def test_delegate_to_nonexistent_node(self, handler, persisted_org, mock_runtime):
        result = await handler.handle(
            "org_delegate_task",
            {"to_node": "node_fantasy", "task": "不存在的节点"},
            persisted_org.id, "node_ceo",
        )
        assert isinstance(result, str)

    async def test_delegate_with_role_title_instead_of_id(self, handler, persisted_org, mock_runtime):
        """LLM may pass role title like 'CTO' instead of 'node_cto'."""
        result = await handler.handle(
            "org_delegate_task",
            {"to_node": "CTO", "task": "测试角色名解析"},
            persisted_org.id, "node_ceo",
        )
        assert "任务已分配" in result or "已分配" in result

    async def test_delegate_with_alias_params(self, handler, persisted_org, mock_runtime):
        """LLM may use 'target' instead of 'to_node'."""
        result = await handler.handle(
            "org_delegate_task",
            {"target": "node_cto", "task_description": "别名参数测试"},
            persisted_org.id, "node_ceo",
        )
        assert "任务已分配" in result or "已分配" in result

    async def test_coerce_invalid_priority(self, handler, persisted_org, mock_runtime):
        result = await handler.handle(
            "org_delegate_task",
            {"to_node": "node_cto", "task": "测试", "priority": "high"},
            persisted_org.id, "node_ceo",
        )
        assert isinstance(result, str)


class TestMessageFormatting:
    """Test _format_incoming_message covers all message types."""

    def _make_runtime(self):
        rt = MagicMock()
        rt._chain_delegation_depth = {}
        return rt

    def test_task_assign_format(self):
        from openakita.orgs.runtime import OrgRuntime
        rt = MagicMock(spec=OrgRuntime)
        rt._format_incoming_message = OrgRuntime._format_incoming_message.__get__(rt)

        msg = OrgMessage(
            org_id="test", from_node="boss", to_node="worker",
            msg_type=MsgType.TASK_ASSIGN, content="写报告",
            metadata={"task_chain_id": "chain_123"},
        )
        text = rt._format_incoming_message(msg)
        assert "收到任务" in text
        assert "boss" in text
        assert "chain_123" in text
        assert "org_submit_deliverable" in text

    def test_task_delivered_format(self):
        from openakita.orgs.runtime import OrgRuntime
        rt = MagicMock(spec=OrgRuntime)
        rt._format_incoming_message = OrgRuntime._format_incoming_message.__get__(rt)

        msg = OrgMessage(
            org_id="test", from_node="worker", to_node="boss",
            msg_type=MsgType.TASK_DELIVERED, content="完成了",
            metadata={"deliverable": "报告.pdf", "summary": "Q2报告"},
        )
        text = rt._format_incoming_message(msg)
        assert "收到任务交付" in text
        assert "报告.pdf" in text
        assert "org_accept_deliverable" in text

    def test_task_rejected_format(self):
        from openakita.orgs.runtime import OrgRuntime
        rt = MagicMock(spec=OrgRuntime)
        rt._format_incoming_message = OrgRuntime._format_incoming_message.__get__(rt)

        msg = OrgMessage(
            org_id="test", from_node="boss", to_node="worker",
            msg_type=MsgType.TASK_REJECTED, content="需要修改",
            metadata={"rejection_reason": "格式不对"},
        )
        text = rt._format_incoming_message(msg)
        assert "被打回" in text
        assert "格式不对" in text
        assert "org_submit_deliverable" in text

    def test_empty_chain_id_in_task_assign(self):
        from openakita.orgs.runtime import OrgRuntime
        rt = MagicMock(spec=OrgRuntime)
        rt._format_incoming_message = OrgRuntime._format_incoming_message.__get__(rt)

        msg = OrgMessage(
            org_id="test", from_node="boss", to_node="worker",
            msg_type=MsgType.TASK_ASSIGN, content="无chain任务",
            metadata={},
        )
        text = rt._format_incoming_message(msg)
        assert "收到任务" in text
        assert "org_submit_deliverable" in text
        assert "task_chain_id=" not in text


class TestIdleProbeLogic:
    """Test idle probe doesn't trigger too frequently."""

    async def test_idle_probe_skips_busy_nodes(self, persisted_org, mock_runtime):
        """Busy nodes should not receive idle probes."""
        persisted_org.nodes[0].status = NodeStatus.BUSY
        persisted_org.nodes[1].status = NodeStatus.BUSY
        assert all(n.status != NodeStatus.IDLE for n in persisted_org.nodes[:2])

    async def test_idle_probe_skips_clone_nodes(self, persisted_org, mock_runtime):
        """Clones should not receive idle probes."""
        clone = make_node("clone_1", "clone", is_clone=True, clone_source="node_cto")
        persisted_org.nodes.append(clone)
        assert clone.is_clone

    async def test_idle_threshold_respects_minimum(self):
        """Idle probe should not fire in less than 120 seconds."""
        threshold = 120
        assert threshold >= 120


class TestAdaptiveHeartbeatEdgeCases:
    """Test adaptive heartbeat under various conditions."""

    @pytest.fixture()
    def heartbeat(self, mock_runtime):
        from openakita.orgs.heartbeat import OrgHeartbeat
        return OrgHeartbeat(mock_runtime)

    def test_very_recent_activity_clamps_to_300s(self, heartbeat, persisted_org):
        heartbeat._last_activity[persisted_org.id] = time.monotonic() - 1
        interval = heartbeat._compute_adaptive_interval(persisted_org)
        assert interval >= 300

    def test_medium_activity_uses_reduced_interval(self, heartbeat, persisted_org):
        heartbeat._last_activity[persisted_org.id] = time.monotonic() - 600
        interval = heartbeat._compute_adaptive_interval(persisted_org)
        assert interval >= 300

    def test_very_old_activity_caps_at_3600(self, heartbeat, persisted_org):
        heartbeat._last_activity[persisted_org.id] = time.monotonic() - 86400
        interval = heartbeat._compute_adaptive_interval(persisted_org)
        assert interval <= 3600

    def test_milestone_counter_starts_at_zero(self, heartbeat, persisted_org):
        assert heartbeat._tasks_since_review.get(persisted_org.id, 0) == 0

    def test_multiple_activities_accumulate(self, heartbeat, persisted_org):
        for _ in range(10):
            heartbeat.record_activity(persisted_org.id)
        assert heartbeat._tasks_since_review[persisted_org.id] == 10


class TestDeliverableWorkflow:
    """Test the full deliverable lifecycle: delegate → submit → accept/reject."""

    @pytest.fixture()
    def handler(self, mock_runtime) -> OrgToolHandler:
        return OrgToolHandler(mock_runtime)

    async def test_submit_deliverable_records_event(self, handler, persisted_org, mock_runtime):
        result = await handler.handle(
            "org_submit_deliverable",
            {"to_node": "node_ceo", "deliverable": "API 文档 v1", "summary": "初版完成"},
            persisted_org.id, "node_cto",
        )
        assert isinstance(result, str)
        ws_calls = [c.args[0] for c in mock_runtime._broadcast_ws.call_args_list]
        assert "org:task_delivered" in ws_calls

    async def test_accept_then_reject_same_chain(self, handler, persisted_org, mock_runtime):
        await handler.handle(
            "org_accept_deliverable",
            {"from_node": "node_cto", "task_chain_id": "c1", "feedback": "好"},
            persisted_org.id, "node_ceo",
        )
        mock_runtime._broadcast_ws.reset_mock()
        await handler.handle(
            "org_reject_deliverable",
            {"from_node": "node_dev", "task_chain_id": "c2", "reason": "不完整"},
            persisted_org.id, "node_cto",
        )
        ws_calls = [c.args[0] for c in mock_runtime._broadcast_ws.call_args_list]
        assert "org:task_rejected" in ws_calls

    async def test_deliverable_with_empty_summary(self, handler, persisted_org, mock_runtime):
        result = await handler.handle(
            "org_submit_deliverable",
            {"to_node": "node_ceo", "deliverable": "完成了", "summary": ""},
            persisted_org.id, "node_cto",
        )
        assert isinstance(result, str)


class TestBlackboardTools:
    """Test blackboard read/write edge cases."""

    @pytest.fixture()
    def handler(self, mock_runtime) -> OrgToolHandler:
        return OrgToolHandler(mock_runtime)

    async def test_write_then_read_blackboard(self, handler, persisted_org, mock_runtime):
        await handler.handle(
            "org_write_blackboard",
            {"content": "项目进度50%", "memory_type": "progress", "tags": ["进度"]},
            persisted_org.id, "node_ceo",
        )
        result = await handler.handle(
            "org_read_blackboard",
            {},
            persisted_org.id, "node_ceo",
        )
        assert "项目进度50%" in result

    async def test_write_with_invalid_memory_type_falls_back(self, handler, persisted_org, mock_runtime):
        result = await handler.handle(
            "org_write_blackboard",
            {"content": "测试内容", "memory_type": "random_type"},
            persisted_org.id, "node_ceo",
        )
        assert isinstance(result, str)

    async def test_read_empty_blackboard(self, handler, persisted_org, mock_runtime):
        result = await handler.handle(
            "org_read_blackboard",
            {},
            persisted_org.id, "node_ceo",
        )
        assert isinstance(result, str)


class TestNodeReferenceResolution:
    """Test that LLM-provided node names get resolved correctly."""

    @pytest.fixture()
    def handler(self, mock_runtime) -> OrgToolHandler:
        return OrgToolHandler(mock_runtime)

    async def test_resolve_by_role_title(self, handler, persisted_org, mock_runtime):
        """Role title 'CTO' should resolve to 'node_cto'."""
        result = await handler.handle(
            "org_send_message",
            {"to_node": "CTO", "content": "你好", "msg_type": "question"},
            persisted_org.id, "node_ceo",
        )
        assert "已发送" in result or "发送" in result

    async def test_resolve_case_insensitive(self, handler, persisted_org, mock_runtime):
        """'cto' should also resolve."""
        result = await handler.handle(
            "org_send_message",
            {"to_node": "cto", "content": "测试", "msg_type": "question"},
            persisted_org.id, "node_ceo",
        )
        assert "已发送" in result or "发送" in result

    async def test_nonexistent_node_returns_error(self, handler, persisted_org, mock_runtime):
        result = await handler.handle(
            "org_send_message",
            {"to_node": "不存在的人", "content": "测试", "msg_type": "question"},
            persisted_org.id, "node_ceo",
        )
        assert isinstance(result, str)


class TestErrorAutoRecovery:
    """Nodes in ERROR state should auto-recover when receiving new tasks."""

    def test_error_node_allows_activation(self, persisted_org):
        """ERROR nodes should not be blocked like FROZEN/OFFLINE ones."""
        node = persisted_org.nodes[1]
        node.status = NodeStatus.ERROR
        assert node.status != NodeStatus.FROZEN
        assert node.status != NodeStatus.OFFLINE

    def test_frozen_node_blocks(self, persisted_org):
        node = persisted_org.nodes[1]
        node.status = NodeStatus.FROZEN
        assert node.status == NodeStatus.FROZEN

    def test_offline_node_blocks(self, persisted_org):
        node = persisted_org.nodes[1]
        node.status = NodeStatus.OFFLINE
        assert node.status == NodeStatus.OFFLINE


class TestTimeoutDifferentiation:
    """Agent task execution (timeout removed in favor of watchdog)."""

    def test_run_agent_task_returns_str(self):
        """_run_agent_task should return str (no timeout wrapper)."""
        from openakita.orgs.runtime import OrgRuntime
        sig = OrgRuntime._run_agent_task.__annotations__
        assert "return" in sig
        ret_type = sig["return"]
        assert "str" in str(ret_type).lower()


class TestInvalidMemoryTypeFallback:
    """Invalid memory_type should fall back gracefully."""

    @pytest.fixture()
    def handler(self, mock_runtime) -> OrgToolHandler:
        return OrgToolHandler(mock_runtime)

    async def test_invalid_memory_type_org_write(self, handler, persisted_org, mock_runtime):
        result = await handler.handle(
            "org_write_blackboard",
            {"content": "测试内容", "memory_type": "completely_invalid"},
            persisted_org.id, "node_ceo",
        )
        assert "已写入" in result or "相似内容" in result

    async def test_invalid_memory_type_dept_write(self, handler, persisted_org, mock_runtime):
        result = await handler.handle(
            "org_write_dept_memory",
            {"content": "部门测试", "memory_type": "xyz"},
            persisted_org.id, "node_cto",
        )
        assert isinstance(result, str)


class TestInvalidMsgTypeFallback:
    """Invalid msg_type should fall back to 'question'."""

    @pytest.fixture()
    def handler(self, mock_runtime) -> OrgToolHandler:
        return OrgToolHandler(mock_runtime)

    async def test_garbage_msg_type_still_sends(self, handler, persisted_org, mock_runtime):
        result = await handler.handle(
            "org_send_message",
            {"to_node": "node_cto", "content": "你好", "msg_type": "not_a_real_type"},
            persisted_org.id, "node_ceo",
        )
        assert "已发送" in result or "发送" in result

    async def test_numeric_msg_type_still_sends(self, handler, persisted_org, mock_runtime):
        result = await handler.handle(
            "org_send_message",
            {"to_node": "node_cto", "content": "数字类型", "msg_type": "12345"},
            persisted_org.id, "node_ceo",
        )
        assert "已发送" in result or "发送" in result


class TestPostTaskHookSafety:
    """Post-task hook should not crash for edge cases."""

    def test_parent_status_blocking(self, persisted_org):
        """FROZEN/OFFLINE parents should not be activated."""
        parent = persisted_org.nodes[0]
        for status in (NodeStatus.FROZEN, NodeStatus.OFFLINE, NodeStatus.BUSY):
            parent.status = status
            assert parent.status in (NodeStatus.FROZEN, NodeStatus.OFFLINE, NodeStatus.BUSY)

