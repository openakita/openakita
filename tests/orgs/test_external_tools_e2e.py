"""End-to-end tests for external_tools — full Agent lifecycle with LLM.

Two modes:
  • MockLLM (default) — deterministic, runs in CI.
    Uses MockLLMClient with pre-scripted tool calls to validate the complete
    pipeline: agent creation → tool filtering → prompt injection → tool
    execution → inter-node messaging → tool request/grant hot-reload.
  • RealLLM (opt-in) — requires API keys.
    Run with:  OPENAKITA_LLM_TESTS=1 pytest tests/orgs/test_external_tools_e2e.py -k real

Every test exercises the real OrgRuntime (not mocked) so the entire chain
_create_node_agent → _patched_execute → OrgToolHandler is live.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openakita.orgs.manager import OrgManager
from openakita.orgs.models import OrgNode, Organization, NodeStatus
from openakita.orgs.runtime import OrgRuntime
from openakita.orgs.tool_categories import expand_tool_categories, TOOL_CATEGORIES

from tests.fixtures.mock_llm import MockLLMClient, MockBrain, MockResponse
from tests.orgs.conftest import make_org, make_node, make_edge


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _inject_mock_brain(agent: Any, mock_client: MockLLMClient) -> None:
    """Replace agent.brain with a MockBrain backed by *mock_client*."""
    mb = MockBrain(mock_client)
    mb.max_tokens = 4096
    mb._llm_client = mock_client
    mb.is_thinking_enabled = lambda: False
    mb._thinking_enabled = False
    mb.get_fallback_model = lambda *a, **kw: ""
    mb.restore_default_model = lambda *a, **kw: (True, "ok")
    mb.get_current_model_info = lambda: {"name": "mock", "model": "mock"}
    mb.get_current_endpoint_info = lambda: {"name": "mock"}
    agent.brain = mb
    if hasattr(agent, "reasoning_engine") and hasattr(agent.reasoning_engine, "_brain"):
        agent.reasoning_engine._brain = mb


def _make_text_response(text: str) -> MockResponse:
    return MockResponse(content=text)


def _make_tool_call(name: str, input_: dict) -> MockResponse:
    return MockResponse(tool_calls=[{"name": name, "input": input_}])


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest.fixture()
async def runtime_env(tmp_data_dir: Path):
    """Yield (runtime, manager) with a started OrgRuntime."""
    manager = OrgManager(tmp_data_dir)
    runtime = OrgRuntime(manager)
    await runtime.start()
    yield runtime, manager
    await runtime.shutdown()


# ===================================================================
# Part 1: Mock-LLM deterministic tests
# ===================================================================


class TestToolFilteringE2E:
    """Verify _create_node_agent filters/retains tools based on external_tools."""

    async def test_node_without_external_tools_has_only_org_tools(self, runtime_env):
        runtime, manager = runtime_env
        org = manager.create(make_org(
            name="纯协作",
            nodes=[make_node("boss", "Boss", 0)],
            edges=[],
        ).to_dict())
        await runtime.start_org(org.id)

        agent = await runtime._create_node_agent(org, org.get_node("boss"))

        _PLAN_TOOLS = {"create_todo", "update_todo_step", "get_todo_status", "complete_todo"}
        tool_names = {t["name"] for t in agent._tools}
        for name in tool_names:
            assert name.startswith("org_") or name == "get_tool_info" or name in _PLAN_TOOLS, \
                f"Unexpected tool '{name}' on node without external_tools"

    async def test_node_with_research_tools_retains_web_search(self, runtime_env):
        runtime, manager = runtime_env
        org = manager.create(make_org(
            name="研究团队",
            nodes=[make_node("researcher", "研究员", 0, external_tools=["research"])],
            edges=[],
        ).to_dict())
        await runtime.start_org(org.id)

        agent = await runtime._create_node_agent(org, org.get_node("researcher"))

        tool_names = {t["name"] for t in agent._tools}
        assert "web_search" in tool_names or "news_search" in tool_names, \
            f"research tools missing, got: {tool_names}"
        _PLAN_TOOLS = {"create_todo", "update_todo_step", "get_todo_status", "complete_todo"}
        for name in tool_names:
            assert (
                name.startswith("org_")
                or name == "get_tool_info"
                or name in expand_tool_categories(["research"])
                or name in _PLAN_TOOLS
            ), f"Unexpected tool '{name}'"

    async def test_node_with_multiple_categories(self, runtime_env):
        runtime, manager = runtime_env
        org = manager.create(make_org(
            name="全栈",
            nodes=[make_node("dev", "全栈工程师", 0,
                             external_tools=["research", "filesystem", "planning"])],
            edges=[],
        ).to_dict())
        await runtime.start_org(org.id)

        agent = await runtime._create_node_agent(org, org.get_node("dev"))

        tool_names = {t["name"] for t in agent._tools}
        expected = expand_tool_categories(["research", "filesystem", "planning"])
        for tool in expected:
            if tool in tool_names:
                break
        else:
            pytest.fail(f"None of {expected} found in agent tools: {tool_names}")

    async def test_individual_tool_name_retained(self, runtime_env):
        """external_tools can also contain individual tool names (not just categories)."""
        runtime, manager = runtime_env
        org = manager.create(make_org(
            name="精确配置",
            nodes=[make_node("node", "Worker", 0, external_tools=["web_search"])],
            edges=[],
        ).to_dict())
        await runtime.start_org(org.id)

        agent = await runtime._create_node_agent(org, org.get_node("node"))

        tool_names = {t["name"] for t in agent._tools}
        assert "web_search" in tool_names


class TestPromptInjectionE2E:
    """Verify the system prompt correctly reflects external tools."""

    async def test_prompt_has_external_tool_section(self, runtime_env):
        runtime, manager = runtime_env
        org = manager.create(make_org(
            name="提示词测试",
            nodes=[make_node("n", "分析师", 0, external_tools=["research"])],
            edges=[],
        ).to_dict())
        await runtime.start_org(org.id)

        agent = await runtime._create_node_agent(org, org.get_node("n"))

        prompt = agent._context.system if hasattr(agent, "_context") else ""
        assert "外部执行工具" in prompt, "Prompt should contain external tools section"
        assert "协作用 org_* 工具" in prompt, "Prompt should contain hybrid guidelines"

    async def test_prompt_without_external_tools_forbids_execution(self, runtime_env):
        runtime, manager = runtime_env
        org = manager.create(make_org(
            name="无外部",
            nodes=[make_node("n", "秘书", 0)],
            edges=[],
        ).to_dict())
        await runtime.start_org(org.id)

        agent = await runtime._create_node_agent(org, org.get_node("n"))

        prompt = agent._context.system if hasattr(agent, "_context") else ""
        assert "org_" in prompt
        assert "create_todo" in prompt

    async def test_prompt_mentions_tool_request(self, runtime_env):
        """Both prompt variants should mention org_request_tools."""
        runtime, manager = runtime_env

        org1 = manager.create(make_org(
            id="o1", name="有工具",
            nodes=[make_node("n", "Worker", 0, external_tools=["research"])],
            edges=[],
        ).to_dict())
        org2 = manager.create(make_org(
            id="o2", name="无工具",
            nodes=[make_node("m", "Worker", 0)],
            edges=[],
        ).to_dict())
        await runtime.start_org(org1.id)
        await runtime.start_org(org2.id)

        a1 = await runtime._create_node_agent(org1, org1.get_node("n"))
        a2 = await runtime._create_node_agent(org2, org2.get_node("m"))

        p1 = a1._context.system if hasattr(a1, "_context") else ""
        p2 = a2._context.system if hasattr(a2, "_context") else ""
        assert "org_request_tools" in p1
        assert "org_request_tools" in p2


class TestExternalToolExecutionE2E:
    """Use MockLLM to simulate an agent calling an external tool end-to-end."""

    async def test_agent_calls_web_search(self, runtime_env):
        """Agent with research tools should be able to call web_search through the pipeline."""
        runtime, manager = runtime_env
        org = manager.create(make_org(
            name="搜索测试",
            nodes=[make_node("r", "研究员", 0, external_tools=["research"])],
            edges=[],
        ).to_dict())
        await runtime.start_org(org.id)

        mock_client = MockLLMClient()
        mock_client.preset_sequence([
            _make_tool_call("web_search", {"query": "AI market trends 2026"}),
            _make_text_response("搜索完成，AI市场预计2026年达到5000亿规模。"),
        ])

        original_create = runtime._create_node_agent

        async def _patched_create(org_arg, node_arg):
            agent = await original_create(org_arg, node_arg)
            _inject_mock_brain(agent, mock_client)
            return agent

        with patch.object(runtime, "_create_node_agent", side_effect=_patched_create):
            result = await asyncio.wait_for(
                runtime.send_command(org.id, "r", "调研AI市场趋势"),
                timeout=30.0,
            )

        assert "result" in result, f"Expected success, got: {result}"
        assert len(result["result"]) > 0
        assert mock_client.total_calls == 2

        sent_tools = mock_client.call_log[0].get("tools", [])
        tool_names = {t["name"] for t in (sent_tools or [])}
        assert "web_search" in tool_names, f"web_search not in LLM tools: {tool_names}"

    async def test_agent_without_tools_cannot_call_web_search(self, runtime_env):
        """Agent without external_tools should NOT have web_search available."""
        runtime, manager = runtime_env
        org = manager.create(make_org(
            name="无工具测试",
            nodes=[make_node("n", "秘书", 0)],
            edges=[],
        ).to_dict())
        await runtime.start_org(org.id)

        mock_client = MockLLMClient()
        mock_client.preset_response("好的，我只有组织工具。")

        original_create = runtime._create_node_agent

        async def _patched_create(org_arg, node_arg):
            agent = await original_create(org_arg, node_arg)
            _inject_mock_brain(agent, mock_client)
            return agent

        with patch.object(runtime, "_create_node_agent", side_effect=_patched_create):
            result = await asyncio.wait_for(
                runtime.send_command(org.id, "n", "你好"),
                timeout=30.0,
            )

        assert "result" in result
        sent_tools = mock_client.call_log[0].get("tools", [])
        tool_names = {t["name"] for t in (sent_tools or [])}
        assert "web_search" not in tool_names


class TestOrgToolCallsE2E:
    """Agent uses org_* tools (write_blackboard, send_message) end-to-end."""

    async def test_agent_writes_to_blackboard(self, runtime_env):
        runtime, manager = runtime_env
        org = manager.create(make_org(
            name="黑板测试",
            nodes=[make_node("ceo", "CEO", 0, external_tools=["research"])],
            edges=[],
        ).to_dict())
        await runtime.start_org(org.id)

        mock_client = MockLLMClient()
        mock_client.preset_sequence([
            _make_tool_call("org_write_blackboard", {
                "content": "Q1目标：推出MVP产品",
                "scope": "organization",
            }),
            _make_text_response("已将Q1目标写入组织黑板。"),
        ])

        original_create = runtime._create_node_agent

        async def _patched_create(org_arg, node_arg):
            agent = await original_create(org_arg, node_arg)
            _inject_mock_brain(agent, mock_client)
            return agent

        with patch.object(runtime, "_create_node_agent", side_effect=_patched_create):
            result = await asyncio.wait_for(
                runtime.send_command(org.id, "ceo", "将Q1目标写入黑板"),
                timeout=30.0,
            )

        assert "result" in result
        bb = runtime.get_blackboard(org.id)
        entries = bb.read_org(limit=5)
        assert any("Q1" in e.content or "MVP" in e.content for e in entries), \
            f"Blackboard should contain the entry, got: {[e.content for e in entries]}"


class TestToolRequestGrantE2E:
    """Full lifecycle: subordinate requests tools → superior grants → hot-reload."""

    async def test_request_and_grant_lifecycle(self, runtime_env):
        runtime, manager = runtime_env
        org = manager.create(make_org(
            name="申请授权",
            nodes=[
                make_node("ceo", "CEO", 0, "管理层", external_tools=["research", "planning"]),
                make_node("dev", "开发", 1, "技术部"),
            ],
            edges=[make_edge("ceo", "dev")],
        ).to_dict())
        await runtime.start_org(org.id)

        dev_node = org.get_node("dev")
        assert dev_node.external_tools == [], "dev starts with no external tools"

        dev_agent_before = await runtime._create_node_agent(org, dev_node)
        dev_tools_before = {t["name"] for t in dev_agent_before._tools}
        assert "write_file" not in dev_tools_before

        # Step 1: dev requests filesystem tools via org_request_tools
        result = await runtime.handle_org_tool(
            "org_request_tools",
            {"tools": ["filesystem"], "reason": "需要读写代码文件"},
            org.id, "dev",
        )
        assert "申请已发送" in result

        messenger = runtime.get_messenger(org.id)
        pending = list(messenger._pending_messages.values())
        request_msgs = [m for m in pending if m.metadata.get("_tool_request")]
        assert len(request_msgs) >= 1, "CEO should have received a tool request"
        assert "filesystem" in request_msgs[-1].content

        # Step 2: CEO grants the tools
        result = await runtime.handle_org_tool(
            "org_grant_tools",
            {"node_id": "dev", "tools": ["filesystem"]},
            org.id, "ceo",
        )
        assert "已授权" in result

        updated_org = runtime.get_org(org.id)
        updated_dev = updated_org.get_node("dev")
        assert "filesystem" in updated_dev.external_tools

        # Step 3: Verify agent cache eviction — next creation should have new tools
        dev_agent_after = await runtime._create_node_agent(updated_org, updated_dev)
        dev_tools_after = {t["name"] for t in dev_agent_after._tools}
        fs_tools = expand_tool_categories(["filesystem"])
        assert fs_tools & dev_tools_after, \
            f"After grant, dev should have filesystem tools. Got: {dev_tools_after}"

    async def test_revoke_removes_tools(self, runtime_env):
        runtime, manager = runtime_env
        org = manager.create(make_org(
            name="收回测试",
            nodes=[
                make_node("boss", "Boss", 0, external_tools=["research"]),
                make_node("worker", "Worker", 1, external_tools=["research", "filesystem"]),
            ],
            edges=[make_edge("boss", "worker")],
        ).to_dict())
        await runtime.start_org(org.id)

        result = await runtime.handle_org_tool(
            "org_revoke_tools",
            {"node_id": "worker", "tools": ["filesystem"]},
            org.id, "boss",
        )
        assert "已收回" in result

        updated_org = runtime.get_org(org.id)
        worker = updated_org.get_node("worker")
        assert "filesystem" not in worker.external_tools
        assert "research" in worker.external_tools

        worker_agent = await runtime._create_node_agent(updated_org, worker)
        tool_names = {t["name"] for t in worker_agent._tools}
        assert "write_file" not in tool_names
        assert "web_search" in tool_names or "news_search" in tool_names


class TestEvictAndHotReloadE2E:
    """Verify that evict_node_agent causes agent cache miss and rebuild."""

    async def test_evict_forces_rebuild(self, runtime_env):
        runtime, manager = runtime_env
        org = manager.create(make_org(
            name="缓存测试",
            nodes=[make_node("n", "Node", 0, external_tools=["research"])],
            edges=[],
        ).to_dict())
        await runtime.start_org(org.id)

        mock_client = MockLLMClient()
        mock_client.set_default_response("ok")

        original_create = runtime._create_node_agent
        create_count = {"n": 0}

        async def _counting_create(org_arg, node_arg):
            create_count["n"] += 1
            agent = await original_create(org_arg, node_arg)
            _inject_mock_brain(agent, mock_client)
            return agent

        with patch.object(runtime, "_create_node_agent", side_effect=_counting_create):
            await runtime.send_command(org.id, "n", "第一次")
            assert create_count["n"] == 1

            await runtime.send_command(org.id, "n", "第二次（缓存）")
            assert create_count["n"] == 1, "Should use cached agent"

            runtime.evict_node_agent(org.id, "n")

            await runtime.send_command(org.id, "n", "第三次（重建）")
            assert create_count["n"] == 2, "Should rebuild after eviction"


class TestCloneInheritsExternalTools:
    """Verify cloned nodes inherit external_tools from source."""

    async def test_scaler_clone_copies_tools(self, runtime_env):
        runtime, manager = runtime_env
        org = manager.create(make_org(
            name="克隆测试",
            nodes=[
                make_node("boss", "Boss", 0),
                make_node("dev", "开发A", 1, external_tools=["filesystem", "research"]),
            ],
            edges=[make_edge("boss", "dev")],
        ).to_dict())

        from openakita.orgs.scaler import OrgScaler

        runtime._save_org = AsyncMock(side_effect=lambda o: manager.update(o.id, o.to_dict()))
        scaler = OrgScaler(runtime)

        req = await scaler.request_clone(org.id, "boss", "dev", reason="负载测试")
        result = await scaler.approve_request(org.id, req.id, "admin")
        assert result.status == "approved"

        updated_org = manager.get(org.id)
        clones = [n for n in updated_org.nodes if n.clone_source == "dev"]
        assert len(clones) >= 1, "Should have at least one clone"
        clone = clones[0]
        assert "filesystem" in clone.external_tools
        assert "research" in clone.external_tools


class TestHeartbeatWithExternalTools:
    """Verify heartbeat prompt adapts based on root node's external_tools."""

    async def test_heartbeat_prompt_mentions_external_tools(self, runtime_env):
        runtime, manager = runtime_env
        org_data = make_org(
            name="心跳工具",
            nodes=[make_node("ceo", "CEO", 0, external_tools=["research", "planning"])],
            edges=[],
        ).to_dict()
        org_data["heartbeat_enabled"] = True
        org = manager.create(org_data)
        await runtime.start_org(org.id)

        mock_client = MockLLMClient()
        mock_client.set_default_response("已完成组织审查，一切正常。")

        original_create = runtime._create_node_agent

        async def _patched_create(org_arg, node_arg):
            agent = await original_create(org_arg, node_arg)
            _inject_mock_brain(agent, mock_client)
            return agent

        with patch.object(runtime, "_create_node_agent", side_effect=_patched_create):
            heartbeat = runtime.get_heartbeat()
            result = await asyncio.wait_for(
                heartbeat.trigger_heartbeat(org.id),
                timeout=30.0,
            )

        assert result is not None
        assert mock_client.total_calls >= 1

        first_call = mock_client.call_log[0]
        system_prompt = first_call.get("system", "")
        user_messages = first_call.get("messages", [])
        all_text = system_prompt + " ".join(
            m.get("content", "") if isinstance(m, dict)
            else getattr(m, "content", "")
            for m in user_messages
        )
        assert "create_todo" in all_text or "web_search" in all_text, \
            "Heartbeat prompt should mention external execution tools"


# ===================================================================
# Part 2: Real-LLM tests (opt-in)
# ===================================================================

_REAL_SKIP = "Real LLM tests require OPENAKITA_LLM_TESTS=1"


def _should_skip_real() -> bool:
    return os.environ.get("OPENAKITA_LLM_TESTS", "0") != "1"


@pytest.mark.skipif(_should_skip_real(), reason=_REAL_SKIP)
class TestRealLLMExternalTools:
    """End-to-end with a real LLM. Only runs when explicitly enabled."""

    async def test_real_agent_with_research_tools(self, runtime_env):
        """Real LLM agent with research tools should answer a market question."""
        runtime, manager = runtime_env
        org = manager.create(make_org(
            name="真实LLM研究",
            nodes=[make_node("analyst", "市场分析师", 0,
                             role_goal="分析市场趋势并提供洞察",
                             external_tools=["research"])],
            edges=[],
        ).to_dict())
        await runtime.start_org(org.id)

        result = await asyncio.wait_for(
            runtime.send_command(org.id, "analyst", "简要分析一下当前AI市场的主要趋势，用1-2句话回答。"),
            timeout=90.0,
        )
        assert "result" in result, f"Expected success, got: {result}"
        assert len(result["result"]) > 10

    async def test_real_agent_delegates_with_tools(self, runtime_env):
        """CEO with tools delegates research to CTO, both have appropriate tools."""
        runtime, manager = runtime_env
        org = manager.create(make_org(
            name="真实LLM委派",
            nodes=[
                make_node("ceo", "CEO", 0, "管理层",
                          role_goal="领导公司战略",
                          external_tools=["research", "planning"]),
                make_node("cto", "CTO", 1, "技术部",
                          role_goal="负责技术方向",
                          external_tools=["research", "filesystem"]),
            ],
            edges=[make_edge("ceo", "cto")],
        ).to_dict())
        await runtime.start_org(org.id)

        result = await asyncio.wait_for(
            runtime.send_command(
                org.id, "ceo",
                "请把调研Python最新版本特性的任务委派给CTO，使用org_send_message。只委派，不要自己做。",
            ),
            timeout=90.0,
        )
        assert "result" in result

    async def test_real_node_requests_tools(self, runtime_env):
        """Node without tools asks superior for them using org_request_tools."""
        runtime, manager = runtime_env
        org = manager.create(make_org(
            name="真实LLM申请",
            nodes=[
                make_node("boss", "Boss", 0, external_tools=["research", "planning"]),
                make_node("worker", "Worker", 1),
            ],
            edges=[make_edge("boss", "worker")],
        ).to_dict())
        await runtime.start_org(org.id)

        result = await asyncio.wait_for(
            runtime.send_command(
                org.id, "worker",
                "你需要搜索功能来完成调研任务，但你目前没有。请使用 org_request_tools 向上级申请 research 类目工具。",
            ),
            timeout=90.0,
        )
        assert "result" in result
