"""LLM integration tests — real Agent execution within an organization.

These tests require valid API keys and are skipped by default.
Run with: pytest tests/orgs/test_llm_integration.py --api-keys

They validate that:
1. Agents can be created from org profiles and respond coherently
2. The prompt injection (org context, blackboard, identity) reaches the LLM
3. Inter-agent messaging works end-to-end
4. Tool calls (org_* tools) are triggered and handled
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from openakita.orgs.manager import OrgManager
from openakita.orgs.runtime import OrgRuntime
from openakita.orgs.models import NodeStatus
from .conftest import make_org

_SKIP_REASON = "LLM tests require --api-keys flag or OPENAKITA_LLM_TESTS=1 env"


def _should_skip() -> bool:
    if os.environ.get("OPENAKITA_LLM_TESTS", "0") == "1":
        return False
    return True


pytestmark = [
    pytest.mark.api_keys,
    pytest.mark.skipif(_should_skip(), reason=_SKIP_REASON),
]


@pytest.fixture()
async def live_runtime(tmp_data_dir: Path):
    """A real OrgRuntime backed by a real data dir."""
    manager = OrgManager(tmp_data_dir)
    runtime = OrgRuntime(manager)
    await runtime.start()
    yield runtime, manager
    await runtime.shutdown()


class TestLLMAgentCreation:
    """Verify that an Agent can be created for an org node and respond."""

    async def test_agent_responds_to_command(self, live_runtime):
        runtime, manager = live_runtime
        org = manager.create(make_org(name="LLM测试公司").to_dict())
        await runtime.start_org(org.id)

        result = await asyncio.wait_for(
            runtime.send_command(org.id, "node_ceo", "简单回复：你是谁？"),
            timeout=60.0,
        )
        assert "result" in result
        assert len(result["result"]) > 5

    async def test_agent_knows_org_context(self, live_runtime):
        runtime, manager = live_runtime
        org = manager.create(make_org(name="上下文测试公司").to_dict())
        await runtime.start_org(org.id)

        bb = runtime.get_blackboard(org.id)
        bb.write_org("公司目标：构建下一代AI产品", "node_ceo")

        result = await asyncio.wait_for(
            runtime.send_command(org.id, "node_ceo", "请描述你的组织目标"),
            timeout=60.0,
        )
        assert "result" in result
        response = result["result"].lower()
        assert "ai" in response or "产品" in response or "目标" in response


class TestLLMInterAgentCommunication:
    """Test that agents can delegate tasks to subordinates."""

    async def test_ceo_delegates_to_cto(self, live_runtime):
        runtime, manager = live_runtime
        org = manager.create(make_org(name="委派测试").to_dict())
        await runtime.start_org(org.id)

        result = await asyncio.wait_for(
            runtime.send_command(
                org.id, "node_ceo",
                "请给CTO分配一个简单任务：说出今天的日期。仅使用org_send_message工具。",
            ),
            timeout=90.0,
        )
        assert result is not None


class TestLLMOrgTemplate:
    """Test starting an org from a template with full LLM interaction."""

    async def test_startup_company_template(self, live_runtime):
        runtime, manager = live_runtime
        from openakita.orgs.templates import ensure_builtin_templates
        ensure_builtin_templates(manager._templates_dir)

        org = manager.create_from_template("software-team", {"name": "LLM软件团队"})
        await runtime.start_org(org.id)

        result = await asyncio.wait_for(
            runtime.send_command(org.id, "tech-lead", "简要总结你负责的团队结构"),
            timeout=60.0,
        )
        assert "result" in result
        assert len(result["result"]) > 10


class TestLLMBlackboardIntegration:
    """Verify agents can read/write shared memory via tools."""

    async def test_agent_reads_blackboard(self, live_runtime):
        runtime, manager = live_runtime
        org = manager.create(make_org(name="记忆测试").to_dict())
        await runtime.start_org(org.id)

        bb = runtime.get_blackboard(org.id)
        bb.write_org("Q1目标：完成产品原型", "system")
        bb.write_org("技术栈已确定为Python+React", "node_cto")

        result = await asyncio.wait_for(
            runtime.send_command(org.id, "node_ceo", "查看组织记忆并总结当前情况"),
            timeout=60.0,
        )
        assert "result" in result


class TestLLMHeartbeat:
    """Test manual heartbeat trigger with LLM execution."""

    async def test_heartbeat_trigger(self, live_runtime):
        runtime, manager = live_runtime
        org_data = make_org(name="心跳测试").to_dict()
        org_data["heartbeat_enabled"] = True
        org = manager.create(org_data)
        await runtime.start_org(org.id)

        heartbeat = runtime.get_heartbeat()
        result = await asyncio.wait_for(
            heartbeat.trigger_heartbeat(org.id),
            timeout=90.0,
        )
        assert result is not None


class TestLLMFreezeNode:
    """Test that frozen nodes reject commands."""

    async def test_frozen_node_rejects_command(self, live_runtime):
        runtime, manager = live_runtime
        org = manager.create(make_org(name="冻结测试").to_dict())
        await runtime.start_org(org.id)

        node = org.get_node("node_dev")
        node.status = NodeStatus.FROZEN
        node.frozen_by = "admin"
        node.frozen_reason = "测试冻结"
        manager.update(org.id, org.to_dict())
        manager.invalidate_cache(org.id)

        result = await runtime.send_command(org.id, "node_dev", "你好")
        assert "error" in result
        assert "冻结" in result["error"]

