"""
Tests for Phase 1-3 features:
  - WebSocket event broadcasting (transparency)
  - Task completion hooks & idle probe (autonomy)
  - Adaptive heartbeat & milestone review (autonomy)
  - AI time calibration (efficiency)
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from openakita.orgs.heartbeat import OrgHeartbeat
from openakita.orgs.identity import OrgIdentity
from openakita.orgs.models import (
    MemoryType,
    NodeStatus,
)
from openakita.orgs.tool_handler import OrgToolHandler
from .conftest import make_org


# ═══════════════════════════════════════════════════════════════════════
# Phase 1: WebSocket event broadcast completeness
# ═══════════════════════════════════════════════════════════════════════


class TestWSBroadcastOnToolExecution:
    """Every key tool action should broadcast a WebSocket event."""

    @pytest.fixture()
    def handler(self, mock_runtime) -> OrgToolHandler:
        return OrgToolHandler(mock_runtime)

    async def test_delegate_task_broadcasts(self, handler, persisted_org, mock_runtime):
        await handler.handle(
            "org_delegate_task",
            {"to_node": "node_cto", "task": "设计架构"},
            persisted_org.id, "node_ceo",
        )
        calls = [c for c in mock_runtime._broadcast_ws.call_args_list
                 if c.args[0] == "org:task_delegated"]
        assert len(calls) >= 1
        data = calls[0].args[1]
        assert data["from_node"] == "node_ceo"
        assert data["to_node"] == "node_cto"
        assert "设计架构" in data["task"]

    async def test_send_message_broadcasts(self, handler, persisted_org, mock_runtime):
        await handler.handle(
            "org_send_message",
            {"to_node": "node_cto", "content": "进展如何", "msg_type": "question"},
            persisted_org.id, "node_ceo",
        )
        calls = [c for c in mock_runtime._broadcast_ws.call_args_list
                 if c.args[0] == "org:message"]
        assert len(calls) >= 1
        assert calls[0].args[1]["from_node"] == "node_ceo"

    async def test_escalate_broadcasts(self, handler, persisted_org, mock_runtime):
        await handler.handle(
            "org_escalate",
            {"content": "需要更多资源", "priority": 1},
            persisted_org.id, "node_cto",
        )
        calls = [c for c in mock_runtime._broadcast_ws.call_args_list
                 if c.args[0] == "org:escalation"]
        assert len(calls) >= 1

    async def test_submit_deliverable_broadcasts(self, handler, persisted_org, mock_runtime):
        await handler.handle(
            "org_submit_deliverable",
            {"to_node": "node_ceo", "deliverable": "架构文档完成", "summary": "完成"},
            persisted_org.id, "node_cto",
        )
        calls = [c for c in mock_runtime._broadcast_ws.call_args_list
                 if c.args[0] == "org:task_delivered"]
        assert len(calls) >= 1

    async def test_accept_deliverable_broadcasts(self, handler, persisted_org, mock_runtime):
        await handler.handle(
            "org_accept_deliverable",
            {"from_node": "node_cto", "task_chain_id": "chain_001", "feedback": "很好"},
            persisted_org.id, "node_ceo",
        )
        calls = [c for c in mock_runtime._broadcast_ws.call_args_list
                 if c.args[0] == "org:task_accepted"]
        assert len(calls) >= 1
        data = calls[0].args[1]
        assert data["accepted_by"] == "node_ceo"
        assert data["from_node"] == "node_cto"

    async def test_reject_deliverable_broadcasts(self, handler, persisted_org, mock_runtime):
        await handler.handle(
            "org_reject_deliverable",
            {"from_node": "node_cto", "task_chain_id": "chain_002", "reason": "不够完善"},
            persisted_org.id, "node_ceo",
        )
        calls = [c for c in mock_runtime._broadcast_ws.call_args_list
                 if c.args[0] == "org:task_rejected"]
        assert len(calls) >= 1
        assert "不够完善" in calls[0].args[1]["reason"]

    async def test_write_blackboard_broadcasts(self, handler, persisted_org, mock_runtime):
        await handler.handle(
            "org_write_blackboard",
            {"content": "Q2 目标确定", "memory_type": "decision", "tags": ["目标"]},
            persisted_org.id, "node_ceo",
        )
        calls = [c for c in mock_runtime._broadcast_ws.call_args_list
                 if c.args[0] == "org:blackboard_update"]
        assert len(calls) >= 1
        data = calls[0].args[1]
        assert data["scope"] == "org"
        assert data["node_id"] == "node_ceo"

    async def test_write_dept_memory_broadcasts(self, handler, persisted_org, mock_runtime):
        await handler.handle(
            "org_write_dept_memory",
            {"content": "技术栈选型完成", "memory_type": "decision"},
            persisted_org.id, "node_cto",
        )
        calls = [c for c in mock_runtime._broadcast_ws.call_args_list
                 if c.args[0] == "org:blackboard_update"]
        assert len(calls) >= 1
        data = calls[0].args[1]
        assert data["scope"] == "department"
        assert data["department"] == "技术部"

    async def test_duplicate_blackboard_write_no_broadcast(self, handler, persisted_org, mock_runtime):
        """Duplicate writes should not broadcast."""
        await handler.handle(
            "org_write_blackboard",
            {"content": "重复内容测试" * 10},
            persisted_org.id, "node_ceo",
        )
        mock_runtime._broadcast_ws.reset_mock()

        await handler.handle(
            "org_write_blackboard",
            {"content": "重复内容测试" * 10},
            persisted_org.id, "node_ceo",
        )
        bb_calls = [c for c in mock_runtime._broadcast_ws.call_args_list
                    if c.args[0] == "org:blackboard_update"]
        assert len(bb_calls) == 0


# ═══════════════════════════════════════════════════════════════════════
# Phase 2: Autonomy features
# ═══════════════════════════════════════════════════════════════════════


class TestAdaptiveHeartbeat:
    @pytest.fixture()
    def heartbeat(self, mock_runtime) -> OrgHeartbeat:
        return OrgHeartbeat(mock_runtime)

    def test_no_activity_returns_base_interval(self, heartbeat, persisted_org):
        interval = heartbeat._compute_adaptive_interval(persisted_org)
        assert interval == persisted_org.heartbeat_interval_s

    def test_recent_activity_shortens_interval(self, heartbeat, persisted_org):
        heartbeat._last_activity[persisted_org.id] = time.monotonic() - 60
        interval = heartbeat._compute_adaptive_interval(persisted_org)
        assert interval < persisted_org.heartbeat_interval_s
        assert interval >= 300

    def test_old_activity_lengthens_interval(self, heartbeat, persisted_org):
        heartbeat._last_activity[persisted_org.id] = time.monotonic() - 7200
        interval = heartbeat._compute_adaptive_interval(persisted_org)
        assert interval >= persisted_org.heartbeat_interval_s

    def test_record_activity_updates_counter(self, heartbeat, persisted_org):
        assert heartbeat._tasks_since_review.get(persisted_org.id, 0) == 0
        heartbeat.record_activity(persisted_org.id)
        assert heartbeat._tasks_since_review[persisted_org.id] == 1
        heartbeat.record_activity(persisted_org.id)
        assert heartbeat._tasks_since_review[persisted_org.id] == 2


class TestMilestoneReview:
    @pytest.fixture()
    def heartbeat(self, mock_runtime) -> OrgHeartbeat:
        return OrgHeartbeat(mock_runtime)

    async def test_heartbeat_resets_task_counter(self, heartbeat, persisted_org, mock_runtime):
        mock_runtime.send_command = AsyncMock(return_value={"result": "复盘完成"})
        heartbeat._tasks_since_review[persisted_org.id] = 10
        await heartbeat.trigger_heartbeat(persisted_org.id)
        assert heartbeat._tasks_since_review.get(persisted_org.id, 0) == 0

    async def test_heartbeat_broadcasts_start_and_done(self, heartbeat, persisted_org, mock_runtime):
        mock_runtime.send_command = AsyncMock(return_value={"result": "OK"})
        await heartbeat.trigger_heartbeat(persisted_org.id)

        starts = [c for c in mock_runtime._broadcast_ws.call_args_list
                  if c.args[0] == "org:heartbeat_start"]
        dones = [c for c in mock_runtime._broadcast_ws.call_args_list
                 if c.args[0] == "org:heartbeat_done"]
        assert len(starts) >= 1
        assert len(dones) >= 1
        assert starts[0].args[1]["type"] == "heartbeat"


class TestStandupBroadcasts:
    @pytest.fixture()
    def heartbeat(self, mock_runtime) -> OrgHeartbeat:
        return OrgHeartbeat(mock_runtime)

    async def test_standup_broadcasts_events(self, heartbeat, persisted_org, mock_runtime, org_dir):
        mock_runtime.send_command = AsyncMock(return_value={"result": "晨会完成"})
        mock_runtime._manager._org_dir = MagicMock(return_value=org_dir)
        (org_dir / "reports").mkdir(parents=True, exist_ok=True)

        await heartbeat.trigger_standup(persisted_org.id)

        starts = [c for c in mock_runtime._broadcast_ws.call_args_list
                  if c.args[0] == "org:heartbeat_start"]
        assert any(c.args[1].get("type") == "standup" for c in starts)


# ═══════════════════════════════════════════════════════════════════════
# Phase 3: AI time calibration
# ═══════════════════════════════════════════════════════════════════════


class TestAITimeCalibration:
    def _make_resolved(self, role: str = "负责人"):
        from openakita.orgs.identity import ResolvedIdentity
        return ResolvedIdentity(soul="", agent="", role=role, level=0)

    def test_identity_prompt_contains_ai_efficiency(self, tmp_path):
        org = make_org(core_business="电商运营")
        identity = OrgIdentity(tmp_path)
        prompt = identity.build_org_context_prompt(
            node=org.nodes[0], org=org, identity=self._make_resolved("运营负责人"),
        )
        assert "AI" in prompt
        assert "分钟" in prompt
        assert "不受人类" in prompt or "分钟级" in prompt

    def test_identity_prompt_has_immediate_action(self, tmp_path):
        org = make_org(core_business="软件开发")
        identity = OrgIdentity(tmp_path)
        prompt = identity.build_org_context_prompt(
            node=org.nodes[0], org=org, identity=self._make_resolved("技术负责人"),
        )
        assert "立即执行" in prompt or "不要等待" in prompt

    def test_non_root_node_also_gets_ai_efficiency(self, tmp_path):
        org = make_org(core_business="内容创作")
        identity = OrgIdentity(tmp_path)
        prompt = identity.build_org_context_prompt(
            node=org.nodes[1], org=org, identity=self._make_resolved("执行者"),
        )
        assert "分钟" in prompt


class TestDeadlineCalibration:
    def test_delegate_tool_deadline_description_mentions_minutes(self):
        from openakita.orgs.tools import ORG_NODE_TOOLS
        delegate_tool = next(t for t in ORG_NODE_TOOLS if t["name"] == "org_delegate_task")
        schema = delegate_tool.get("parameters") or delegate_tool.get("input_schema", {})
        deadline_desc = schema["properties"]["deadline"]["description"]
        assert "分钟" in deadline_desc or "5-30" in deadline_desc


# ═══════════════════════════════════════════════════════════════════════
# Full flow integration test
# ═══════════════════════════════════════════════════════════════════════


class TestFullFlowWSCoverage:
    """Simulate a complete delegation -> delivery -> acceptance flow
    and verify all expected WS events fire in sequence."""

    @pytest.fixture()
    def handler(self, mock_runtime) -> OrgToolHandler:
        return OrgToolHandler(mock_runtime)

    async def test_full_delegation_cycle_events(self, handler, persisted_org, mock_runtime):
        ws = mock_runtime._broadcast_ws

        # Step 1: CEO delegates to CTO
        await handler.handle(
            "org_delegate_task",
            {"to_node": "node_cto", "task": "实现登录功能"},
            persisted_org.id, "node_ceo",
        )

        # Step 2: CTO submits deliverable
        await handler.handle(
            "org_submit_deliverable",
            {"to_node": "node_ceo", "deliverable": "登录模块已完成", "summary": "完成"},
            persisted_org.id, "node_cto",
        )

        # Step 3: CEO accepts
        await handler.handle(
            "org_accept_deliverable",
            {"from_node": "node_cto", "task_chain_id": "test_chain", "feedback": "合格"},
            persisted_org.id, "node_ceo",
        )

        # Step 4: CEO writes to blackboard
        await handler.handle(
            "org_write_blackboard",
            {"content": "登录功能开发完毕", "memory_type": "progress"},
            persisted_org.id, "node_ceo",
        )

        event_types = [c.args[0] for c in ws.call_args_list]
        assert "org:task_delegated" in event_types
        assert "org:task_delivered" in event_types
        assert "org:task_accepted" in event_types
        assert "org:blackboard_update" in event_types

    async def test_rejection_flow(self, handler, persisted_org, mock_runtime):
        ws = mock_runtime._broadcast_ws

        await handler.handle(
            "org_delegate_task",
            {"to_node": "node_cto", "task": "设计API"},
            persisted_org.id, "node_ceo",
        )
        await handler.handle(
            "org_submit_deliverable",
            {"to_node": "node_ceo", "deliverable": "初稿", "summary": "草稿"},
            persisted_org.id, "node_cto",
        )
        await handler.handle(
            "org_reject_deliverable",
            {"from_node": "node_cto", "task_chain_id": "rej_chain", "reason": "需补充错误处理"},
            persisted_org.id, "node_ceo",
        )

        event_types = [c.args[0] for c in ws.call_args_list]
        assert "org:task_delegated" in event_types
        assert "org:task_delivered" in event_types
        assert "org:task_rejected" in event_types


# ═══════════════════════════════════════════════════════════════════════
# Phase 4: Enhanced stats endpoint & anomaly detection
# ═══════════════════════════════════════════════════════════════════════


def _make_test_app(mock_runtime):
    """Helper to create a test FastAPI app with org routes."""
    from openakita.api.routes.orgs import router as org_router
    from fastapi import FastAPI
    app = FastAPI()
    app.state.org_manager = mock_runtime._manager
    app.state.org_runtime = mock_runtime
    app.include_router(org_router)
    return app


def _mock_stats_deps(mock_runtime, persisted_org):
    mock_runtime.get_org = MagicMock(return_value=persisted_org)
    mock_runtime.get_inbox = MagicMock(return_value=MagicMock(
        unread_count=MagicMock(return_value=0),
        pending_approval_count=MagicMock(return_value=0),
    ))
    mock_runtime.get_scaler = MagicMock(return_value=MagicMock(
        get_pending_requests=MagicMock(return_value=[]),
    ))
    mock_runtime._node_last_activity = {}
    mock_runtime._node_busy_since = {}
    mock_runtime._agent_cache = {}
    mock_runtime._running_tasks = {}
    mock_runtime._anomaly_inbox_ts = {}


class TestStatsEndpointEnhancements:
    """Test the enhanced /stats endpoint with per-node data and anomaly detection."""

    async def test_stats_returns_per_node_data(self, persisted_org, mock_runtime):
        try:
            from httpx import ASGITransport, AsyncClient
        except ImportError:
            pytest.skip("httpx not installed")

        _mock_stats_deps(mock_runtime, persisted_org)
        app = _make_test_app(mock_runtime)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(f"/api/orgs/{persisted_org.id}/stats")
            assert resp.status_code == 200
            data = resp.json()
            assert "per_node" in data
            assert len(data["per_node"]) == len(persisted_org.nodes)
            assert "health" in data
            assert data["health"] in ("healthy", "attention", "warning", "critical")
            assert "anomalies" in data
            assert isinstance(data["anomalies"], list)

    async def test_stats_anomaly_detection_error_node(self, persisted_org, mock_runtime):
        try:
            from httpx import ASGITransport, AsyncClient
        except ImportError:
            pytest.skip("httpx not installed")

        persisted_org.nodes[1].status = NodeStatus.ERROR
        _mock_stats_deps(mock_runtime, persisted_org)
        app = _make_test_app(mock_runtime)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(f"/api/orgs/{persisted_org.id}/stats")
            data = resp.json()
            assert data["health"] == "critical"
            assert any(a["type"] == "error" for a in data["anomalies"])
            assert any(a["node_id"] == persisted_org.nodes[1].id for a in data["anomalies"])

    async def test_stats_includes_recent_blackboard(self, persisted_org, mock_runtime):
        try:
            from httpx import ASGITransport, AsyncClient
        except ImportError:
            pytest.skip("httpx not installed")

        bb = mock_runtime.get_blackboard(persisted_org.id)
        bb.write_org("测试黑板条目", source_node="node_ceo", memory_type=MemoryType.DECISION)

        _mock_stats_deps(mock_runtime, persisted_org)
        app = _make_test_app(mock_runtime)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(f"/api/orgs/{persisted_org.id}/stats")
            data = resp.json()
            assert "recent_blackboard" in data
            assert len(data["recent_blackboard"]) >= 1
            assert "测试黑板条目" in data["recent_blackboard"][0]["content"]


class TestThinkingEndpoint:
    """Test the /nodes/{id}/thinking endpoint."""

    async def test_thinking_returns_timeline(self, persisted_org, mock_runtime):
        try:
            from httpx import ASGITransport, AsyncClient
        except ImportError:
            pytest.skip("httpx not installed")

        mock_runtime.get_org = MagicMock(return_value=persisted_org)
        app = _make_test_app(mock_runtime)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(f"/api/orgs/{persisted_org.id}/nodes/node_ceo/thinking")
            assert resp.status_code == 200
            data = resp.json()
            assert data["node_id"] == "node_ceo"
            assert "timeline" in data
            assert isinstance(data["timeline"], list)

    async def test_thinking_404_for_missing_node(self, persisted_org, mock_runtime):
        try:
            from httpx import ASGITransport, AsyncClient
        except ImportError:
            pytest.skip("httpx not installed")

        mock_runtime.get_org = MagicMock(return_value=persisted_org)
        app = _make_test_app(mock_runtime)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(f"/api/orgs/{persisted_org.id}/nodes/nonexistent/thinking")
            assert resp.status_code == 404

    async def test_thinking_includes_messages(self, persisted_org, mock_runtime, org_dir):
        """If communication log exists, messages should appear in timeline."""
        import json
        try:
            from httpx import ASGITransport, AsyncClient
        except ImportError:
            pytest.skip("httpx not installed")

        logs_dir = org_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        comm_log = logs_dir / "communications.jsonl"
        comm_log.write_text(
            json.dumps({
                "from_node": "node_ceo", "to_node": "node_cto",
                "content": "开始执行任务", "msg_type": "task_assign",
                "timestamp": "2026-03-08T12:00:00",
            }) + "\n",
            encoding="utf-8",
        )

        mock_runtime.get_org = MagicMock(return_value=persisted_org)
        app = _make_test_app(mock_runtime)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(f"/api/orgs/{persisted_org.id}/nodes/node_ceo/thinking")
            data = resp.json()
            msg_items = [t for t in data["timeline"] if t["type"] == "message"]
            assert len(msg_items) >= 1
            assert msg_items[0]["direction"] == "out"
            assert "开始执行任务" in msg_items[0]["content"]
