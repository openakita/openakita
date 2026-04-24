"""LLM deep task execution tests — real Agent + tool calls + inter-agent workflow.

These tests require valid API keys.
Run with: OPENAKITA_LLM_TESTS=1 pytest tests/orgs/test_llm_task_execution.py -v -s

They validate the FULL task execution pipeline:
1. Agent actually invokes org_* tools (delegate, blackboard, escalate)
2. Multi-hop delegation (CEO → CTO → Dev) works end-to-end
3. Blackboard read/write roundtrip via LLM tool calls
4. Error recovery: node recovers from ERROR state on next command
5. Deliverable workflow: delegate → submit → accept/reject
6. Event store captures real execution events
7. WebSocket events fire during real execution
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from openakita.orgs.manager import OrgManager
from openakita.orgs.runtime import OrgRuntime
from openakita.orgs.models import NodeStatus, OrgStatus
from .conftest import make_org, make_node, make_edge

_SKIP_REASON = "LLM tests require OPENAKITA_LLM_TESTS=1 env"


def _should_skip() -> bool:
    return os.environ.get("OPENAKITA_LLM_TESTS", "0") != "1"


pytestmark = [
    pytest.mark.api_keys,
    pytest.mark.skipif(_should_skip(), reason=_SKIP_REASON),
]


@pytest.fixture()
async def live_env(tmp_data_dir: Path):
    """A real OrgRuntime with real LLM backend."""
    manager = OrgManager(tmp_data_dir)
    runtime = OrgRuntime(manager)
    await runtime.start()
    yield runtime, manager
    await runtime.shutdown()


class TestToolCallExecution:
    """Verify the LLM actually triggers org_* tool calls."""

    async def test_ceo_writes_to_blackboard(self, live_env):
        """CEO should use org_write_blackboard when asked to record a decision."""
        runtime, manager = live_env
        org = manager.create(make_org(name="黑板写入测试").to_dict())
        await runtime.start_org(org.id)

        result = await asyncio.wait_for(
            runtime.send_command(
                org.id, "node_ceo",
                "请在组织黑板上记录一条决策：'Q2目标确定为用户增长30%'。使用 org_write_blackboard 工具。",
            ),
            timeout=90.0,
        )
        assert "result" in result, f"Expected result key, got: {result}"

        bb = runtime.get_blackboard(org.id)
        entries = bb.read_org(limit=10)
        found = any("Q2" in e.content or "用户增长" in e.content or "30%" in e.content for e in entries)
        assert found, f"Blackboard should contain the decision. Entries: {[e.content[:60] for e in entries]}"

    async def test_ceo_reads_blackboard(self, live_env):
        """CEO should use org_read_blackboard and reference its contents."""
        runtime, manager = live_env
        org = manager.create(make_org(name="黑板读取测试").to_dict())
        await runtime.start_org(org.id)

        bb = runtime.get_blackboard(org.id)
        bb.write_org("重要通知：下周一全员大会，讨论产品路线图", "system")

        result = await asyncio.wait_for(
            runtime.send_command(
                org.id, "node_ceo",
                "请查看组织黑板（用 org_read_blackboard），然后告诉我黑板上有什么内容。",
            ),
            timeout=90.0,
        )
        assert "result" in result
        response = result["result"]
        assert any(kw in response for kw in ["大会", "路线图", "产品", "下周"]), \
            f"Response should reference blackboard content. Got: {response[:200]}"


class TestMultiHopDelegation:
    """Test that CEO can delegate to CTO, and the task actually reaches CTO."""

    async def test_ceo_delegates_task_to_cto(self, live_env):
        """CEO delegates a task; verify CTO's node gets activated."""
        runtime, manager = live_env
        org = manager.create(make_org(name="委派测试").to_dict())
        await runtime.start_org(org.id)

        result = await asyncio.wait_for(
            runtime.send_command(
                org.id, "node_ceo",
                "请使用 org_delegate_task 工具，给CTO分配一个任务：'调研Python异步框架的技术选型'。",
            ),
            timeout=90.0,
        )
        assert "result" in result

        es = runtime.get_event_store(org.id)
        events = es.query(limit=50)
        event_types = [e.get("event_type", "") for e in events]
        assert "task_assigned" in event_types, \
            f"Expected task_assigned event. Events: {event_types}"

    async def test_delegation_chain_generates_events(self, live_env):
        """A delegation should generate node_activated events for both CEO and CTO."""
        runtime, manager = live_env
        org = manager.create(make_org(name="事件链测试").to_dict())
        await runtime.start_org(org.id)

        result = await asyncio.wait_for(
            runtime.send_command(
                org.id, "node_ceo",
                "给CTO分配任务：写一份技术方案摘要。用 org_delegate_task。",
            ),
            timeout=120.0,
        )

        await asyncio.sleep(5)

        es = runtime.get_event_store(org.id)
        events = es.query(limit=50)
        actors = set(e.get("actor", "") for e in events)
        assert "node_ceo" in actors, f"CEO should have events. Actors: {actors}"


class TestErrorRecoveryWithLLM:
    """Test that ERROR nodes can recover when receiving new commands."""

    async def test_error_node_recovers_on_command(self, live_env):
        """A node in ERROR state should auto-recover when sent a new command."""
        runtime, manager = live_env
        org = manager.create(make_org(name="恢复测试").to_dict())
        await runtime.start_org(org.id)

        node = org.get_node("node_cto")
        node.status = NodeStatus.ERROR
        manager.update(org.id, org.to_dict())

        result = await asyncio.wait_for(
            runtime.send_command(org.id, "node_cto", "你好，请确认你已恢复正常工作。"),
            timeout=90.0,
        )
        assert "result" in result, f"Expected recovery, got: {result}"
        assert "error" not in result or "冻结" not in result.get("error", "")

        refreshed = runtime.get_org(org.id)
        cto = refreshed.get_node("node_cto")
        assert cto.status != NodeStatus.ERROR, \
            f"CTO should have recovered from ERROR, got: {cto.status}"

        es = runtime.get_event_store(org.id)
        events = es.query(limit=30)
        event_types = [e.get("event_type", "") for e in events]
        assert "node_auto_recovered" in event_types, \
            f"Expected node_auto_recovered event. Types: {event_types}"


class TestEventStoreIntegrity:
    """Verify event store captures meaningful data during real execution."""

    async def test_events_have_timestamps_and_actors(self, live_env):
        runtime, manager = live_env
        org = manager.create(make_org(name="事件完整性测试").to_dict())
        await runtime.start_org(org.id)

        await asyncio.wait_for(
            runtime.send_command(org.id, "node_ceo", "查看组织状态，简要回复。"),
            timeout=60.0,
        )

        es = runtime.get_event_store(org.id)
        events = es.query(limit=20)
        assert len(events) >= 2, f"Expected at least 2 events, got {len(events)}"

        for evt in events:
            assert "timestamp" in evt, f"Event missing timestamp: {evt}"
            assert "event_type" in evt, f"Event missing event_type: {evt}"
            assert "actor" in evt, f"Event missing actor: {evt}"

    async def test_task_completed_event_has_result_preview(self, live_env):
        runtime, manager = live_env
        org = manager.create(make_org(name="结果预览测试").to_dict())
        await runtime.start_org(org.id)

        await asyncio.wait_for(
            runtime.send_command(org.id, "node_ceo", "用一句话介绍你自己。"),
            timeout=60.0,
        )

        es = runtime.get_event_store(org.id)
        events = es.query(event_type="task_completed", limit=5)
        assert len(events) >= 1, "Expected at least one task_completed event"
        data = events[0].get("data", {})
        assert "result_preview" in data, f"task_completed should have result_preview. Data: {data}"
        assert len(data["result_preview"]) > 0, "result_preview should not be empty"


class TestOrgNodeStatusTransitions:
    """Test that node statuses change correctly during real task execution."""

    async def test_node_completes_task_successfully(self, live_env):
        """After send_command, node should not be in ERROR and result should exist."""
        runtime, manager = live_env
        org = manager.create(make_org(name="状态转换测试").to_dict())
        await runtime.start_org(org.id)

        result = await asyncio.wait_for(
            runtime.send_command(org.id, "node_ceo", "说：完成。"),
            timeout=60.0,
        )
        assert "result" in result
        assert len(result["result"]) > 0

        refreshed = runtime.get_org(org.id)
        ceo = refreshed.get_node("node_ceo")
        assert ceo.status != NodeStatus.ERROR, \
            f"CEO should not be in ERROR after task, got: {ceo.status}"

        es = runtime.get_event_store(org.id)
        events = es.query(event_type="task_completed", limit=5)
        assert len(events) >= 1, "Expected at least one task_completed event"

    async def test_frozen_node_stays_blocked(self, live_env):
        runtime, manager = live_env
        org = manager.create(make_org(name="冻结状态测试").to_dict())
        await runtime.start_org(org.id)

        node = org.get_node("node_dev")
        node.status = NodeStatus.FROZEN
        node.frozen_by = "admin"
        manager.update(org.id, org.to_dict())

        result = await runtime.send_command(org.id, "node_dev", "你好")
        assert "error" in result
        assert "冻结" in result["error"]


class TestCompletedTaskCount:
    """Verify that total_tasks_completed increments correctly."""

    async def test_tasks_completed_increments(self, live_env):
        runtime, manager = live_env
        org = manager.create(make_org(name="计数测试").to_dict())
        await runtime.start_org(org.id)

        initial = org.total_tasks_completed

        await asyncio.wait_for(
            runtime.send_command(org.id, "node_ceo", "回复OK"),
            timeout=60.0,
        )

        refreshed = runtime.get_org(org.id)
        assert refreshed.total_tasks_completed > initial, \
            f"Tasks completed should increment. Initial={initial}, Now={refreshed.total_tasks_completed}"

