"""L1 Unit Tests: OrgRuntime root chain 去重机制。

回归 2026-04-28 _134209 失败链：root 节点主任务结束后，mailbox 残留的同
chain TASK_DELIVERED 被 drain 出来又触发空跑「补汇总」ReAct（每次约
150K tokens 浪费）。

通过 _root_processed_chains 集合在 root 主任务 task_completed 时登记已
验收过的 chain，使 _on_node_message / _drain_node_pending 中的
_root_delivery_bypass 在重复到达时返回 False（走「closed chain skip」分支），
首次到达仍正常激活——保留 P0-1「root 节点最后总结」修复。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from openakita.agents.profile import AgentProfile
from openakita.orgs.models import OrgNode
from openakita.orgs.runtime import OrgRuntime


@pytest.fixture
def runtime() -> OrgRuntime:
    """构造一个最小化 OrgRuntime 实例。

    只测试 root chain 去重相关的纯逻辑方法（_mark / _is / _extract /
    _deactivate_org 清理钩子），不依赖完整调度链。"""
    mock_manager = MagicMock()
    return OrgRuntime(manager=mock_manager)


class TestMarkAndQueryProcessedChain:
    """_mark_chain_processed_by_root / _is_chain_processed_by_root 基础行为。"""

    def test_unmarked_chain_returns_false(self, runtime: OrgRuntime):
        assert runtime._is_chain_processed_by_root("org_a", "chain_x") is False

    def test_marked_chain_returns_true(self, runtime: OrgRuntime):
        runtime._mark_chain_processed_by_root("org_a", "chain_x")
        assert runtime._is_chain_processed_by_root("org_a", "chain_x") is True

    def test_other_org_chain_not_visible(self, runtime: OrgRuntime):
        """不同 org 之间集合互相隔离，避免跨组织误吞。"""
        runtime._mark_chain_processed_by_root("org_a", "chain_x")
        assert runtime._is_chain_processed_by_root("org_b", "chain_x") is False

    def test_empty_chain_id_returns_false(self, runtime: OrgRuntime):
        """None / 空字符串都不应误判为已处理。"""
        runtime._mark_chain_processed_by_root("org_a", "chain_x")
        assert runtime._is_chain_processed_by_root("org_a", None) is False
        assert runtime._is_chain_processed_by_root("org_a", "") is False

    def test_empty_org_id_does_not_crash(self, runtime: OrgRuntime):
        """空 org_id 应当安静返回 / 不写入，绝不抛异常。"""
        runtime._mark_chain_processed_by_root("", "chain_x")
        runtime._mark_chain_processed_by_root("org_a", "")
        assert runtime._is_chain_processed_by_root("", "chain_x") is False
        assert runtime._is_chain_processed_by_root("org_a", "") is False
        # 集合不应被空 org/chain 污染
        assert runtime._root_processed_chains == {} or all(
            len(b) == 0 for b in runtime._root_processed_chains.values()
        )

    def test_repeated_mark_does_not_duplicate(self, runtime: OrgRuntime):
        """重复 mark 同一 chain 不应膨胀集合，应触发 LRU touch。"""
        runtime._mark_chain_processed_by_root("org_a", "chain_x")
        runtime._mark_chain_processed_by_root("org_a", "chain_x")
        runtime._mark_chain_processed_by_root("org_a", "chain_x")
        assert len(runtime._root_processed_chains["org_a"]) == 1


class TestRootProcessedChainLRU:
    """LRU 弹出行为：超过 _root_processed_chain_max_per_org 后最早条目被弹出，
    防止长跑组织内存膨胀。"""

    def test_lru_eviction_when_exceeding_limit(self, runtime: OrgRuntime):
        runtime._root_processed_chain_max_per_org = 5  # 调小便于测试
        for i in range(7):
            runtime._mark_chain_processed_by_root("org_a", f"chain_{i}")
        bucket = runtime._root_processed_chains["org_a"]
        assert len(bucket) == 5
        # chain_0, chain_1 被弹出（最早进入），chain_2..chain_6 保留
        assert "chain_0" not in bucket
        assert "chain_1" not in bucket
        assert "chain_6" in bucket
        # 是否处理过的查询同步反映
        assert runtime._is_chain_processed_by_root("org_a", "chain_0") is False
        assert runtime._is_chain_processed_by_root("org_a", "chain_6") is True


class TestExtractAcceptedChainIds:
    """_extract_accepted_chain_ids 的 trace 解析行为：仅成功的
    org_accept_deliverable 调用的 task_chain_id 才会被收集，
    任何失败 marker（is_error / 非 JSON 短句 / ok=false JSON）均跳过。"""

    def test_empty_trace_returns_empty(self, runtime: OrgRuntime):
        assert runtime._extract_accepted_chain_ids(None) == []
        assert runtime._extract_accepted_chain_ids([]) == []

    def test_successful_accept_extracted(self, runtime: OrgRuntime):
        trace = [
            {
                "tool_calls": [
                    {
                        "name": "org_accept_deliverable",
                        "id": "tc_1",
                        "input": {"task_chain_id": "chain_A", "from_node": "x"},
                    },
                ],
                "tool_results": [
                    {
                        "tool_use_id": "tc_1",
                        "is_error": False,
                        "result_content": '{"ok": true, "chain_id": "chain_A"}',
                    },
                ],
            },
        ]
        assert runtime._extract_accepted_chain_ids(trace) == ["chain_A"]

    def test_is_error_flag_skips_extraction(self, runtime: OrgRuntime):
        """tool_results.is_error=True 视为失败，对应 chain 不被登记。
        回归保护：避免把 root 的失败任务也污染集合。"""
        trace = [
            {
                "tool_calls": [
                    {
                        "name": "org_accept_deliverable",
                        "id": "tc_1",
                        "input": {"task_chain_id": "chain_A", "from_node": "x"},
                    },
                ],
                "tool_results": [
                    {"tool_use_id": "tc_1", "is_error": True, "result_content": "boom"},
                ],
            },
        ]
        assert runtime._extract_accepted_chain_ids(trace) == []

    def test_non_json_short_failure_skipped(self, runtime: OrgRuntime):
        """tool_handler 失败时返回中文短句（"组织未运行..." / "缺少 from_node" 等），
        is_error 可能未被显式置 True，但 result 不是 JSON，应保守跳过。"""
        trace = [
            {
                "tool_calls": [
                    {
                        "name": "org_accept_deliverable",
                        "id": "tc_1",
                        "input": {"task_chain_id": "chain_A", "from_node": "x"},
                    },
                ],
                "tool_results": [
                    {"tool_use_id": "tc_1", "is_error": False, "result_content": "缺少 from_node 参数"},
                ],
            },
        ]
        assert runtime._extract_accepted_chain_ids(trace) == []

    def test_ok_false_json_skipped(self, runtime: OrgRuntime):
        trace = [
            {
                "tool_calls": [
                    {
                        "name": "org_accept_deliverable",
                        "id": "tc_1",
                        "input": {"task_chain_id": "chain_A", "from_node": "x"},
                    },
                ],
                "tool_results": [
                    {"tool_use_id": "tc_1", "is_error": False, "result_content": '{"ok": false, "error": "x"}'},
                ],
            },
        ]
        assert runtime._extract_accepted_chain_ids(trace) == []

    def test_other_tool_calls_ignored(self, runtime: OrgRuntime):
        """非 org_accept_deliverable 的工具调用一律跳过。"""
        trace = [
            {
                "tool_calls": [
                    {
                        "name": "org_submit_deliverable",
                        "id": "tc_1",
                        "input": {"task_chain_id": "chain_A"},
                    },
                    {
                        "name": "write_file",
                        "id": "tc_2",
                        "input": {"path": "x.md"},
                    },
                ],
                "tool_results": [
                    {"tool_use_id": "tc_1", "is_error": False, "result_content": '{"ok": true}'},
                    {"tool_use_id": "tc_2", "is_error": False, "result_content": "ok"},
                ],
            },
        ]
        assert runtime._extract_accepted_chain_ids(trace) == []

    def test_multiple_chains_in_one_task(self, runtime: OrgRuntime):
        """root 主任务里 accept 多个下属交付的 chain 全部登记。"""
        trace = [
            {
                "tool_calls": [
                    {
                        "name": "org_accept_deliverable",
                        "id": "tc_1",
                        "input": {"task_chain_id": "chain_A", "from_node": "p"},
                    },
                    {
                        "name": "org_accept_deliverable",
                        "id": "tc_2",
                        "input": {"task_chain_id": "chain_B", "from_node": "q"},
                    },
                ],
                "tool_results": [
                    {"tool_use_id": "tc_1", "is_error": False, "result_content": '{"ok": true}'},
                    {"tool_use_id": "tc_2", "is_error": False, "result_content": '{"ok": true}'},
                ],
            },
        ]
        assert runtime._extract_accepted_chain_ids(trace) == ["chain_A", "chain_B"]

    def test_missing_input_field_skipped(self, runtime: OrgRuntime):
        """args 缺 task_chain_id 时不收集（避免空字符串污染集合）。"""
        trace = [
            {
                "tool_calls": [
                    {
                        "name": "org_accept_deliverable",
                        "id": "tc_1",
                        "input": {"from_node": "x"},  # 缺 task_chain_id
                    },
                ],
                "tool_results": [
                    {"tool_use_id": "tc_1", "is_error": False, "result_content": '{"ok": true}'},
                ],
            },
        ]
        assert runtime._extract_accepted_chain_ids(trace) == []


class TestDeactivateClearsProcessedChains:
    """_deactivate_org 必须清理 _root_processed_chains[org_id]，
    防止 stop/delete/reset 后内存泄漏，也避免重启同 org_id 时残留状态影响判定。"""

    @pytest.mark.asyncio
    async def test_deactivate_org_clears_processed_chains(self, runtime: OrgRuntime):
        runtime._mark_chain_processed_by_root("org_a", "chain_x")
        runtime._mark_chain_processed_by_root("org_a", "chain_y")
        assert runtime._is_chain_processed_by_root("org_a", "chain_x") is True

        # _deactivate_org 内部会调 messenger.stop_background_tasks 等，
        # 这里只关心末尾的 _root_processed_chains.pop——其它依赖在 mock
        # manager 下应保持安静（不抛异常即可）。
        await runtime._deactivate_org("org_a")

        assert "org_a" not in runtime._root_processed_chains
        assert runtime._is_chain_processed_by_root("org_a", "chain_x") is False

    @pytest.mark.asyncio
    async def test_deactivate_other_org_does_not_clear_self(self, runtime: OrgRuntime):
        """deactivate 一个 org 不影响其它 org 的集合。"""
        runtime._mark_chain_processed_by_root("org_a", "chain_x")
        runtime._mark_chain_processed_by_root("org_b", "chain_y")

        await runtime._deactivate_org("org_a")

        assert "org_a" not in runtime._root_processed_chains
        assert runtime._is_chain_processed_by_root("org_b", "chain_y") is True


class TestBypassNarrowingSemantics:
    """_root_delivery_bypass 在 _on_node_message 与 _drain_node_pending 两处的
    新判定语义：is_root + TASK_DELIVERED + 该 chain 未被 root 处理过 = True。

    这一层是结构性断言（调用 _is_chain_processed_by_root 的返回值），
    不构造完整 mailbox / messenger 调用链，但能直接锁定核心契约：
        - 首次到达 chain 不在集合中 -> bypass=True -> 走原激活路径（保留 P0-1）
        - 重复到达 chain 在集合中 -> bypass=False -> 走「closed chain skip」分支
    """

    def test_first_arrival_bypass_remains_true(self, runtime: OrgRuntime):
        # 简化为对 _is_chain_processed_by_root 的契约断言：
        # 集合空 -> 返回 False -> 在 _on_node_message 里
        # _root_delivery_bypass = is_root AND msg=TASK_DELIVERED AND not False = True
        assert runtime._is_chain_processed_by_root("org_a", "chain_x") is False

    def test_repeated_arrival_bypass_becomes_false(self, runtime: OrgRuntime):
        runtime._mark_chain_processed_by_root("org_a", "chain_x")
        # 集合命中 -> 返回 True -> 在 _on_node_message 里
        # _root_delivery_bypass = is_root AND msg=TASK_DELIVERED AND not True = False
        # -> 进入「closed chain skip」分支（mark_processed + 不激活 ReAct）
        assert runtime._is_chain_processed_by_root("org_a", "chain_x") is True


class TestSubAgentAcceptDoesNotPolluteRootSet:
    """子节点（非 root）的 accept_deliverable 调用绝不应登记到 _root_processed_chains。
    通过 trace 解析方法本身不区分 root/非 root（解析层中立），但调用点（
    register_chain_on_root_complete 步骤）只在 _is_root_node=True 时调用 mark。
    本测试锁这个调用契约。"""

    def test_extract_is_role_neutral_but_caller_must_check_root(
        self, runtime: OrgRuntime,
    ):
        """_extract_accepted_chain_ids 本身不区分 root 与子节点，由调用方
        负责仅在 root 节点 task_completed 时调用 _mark_chain_processed_by_root。"""
        trace = [
            {
                "tool_calls": [
                    {
                        "name": "org_accept_deliverable",
                        "id": "tc_1",
                        "input": {"task_chain_id": "chain_A", "from_node": "x"},
                    },
                ],
                "tool_results": [
                    {"tool_use_id": "tc_1", "is_error": False, "result_content": '{"ok": true}'},
                ],
            },
        ]
        # 即使是子节点的 trace 也能解析出 chain_id；但调用方在
        # _run_node_task 里有 `if _is_root_node and is_normal:` 守卫，
        # 子节点这一支不会调 _mark_chain_processed_by_root。
        assert runtime._extract_accepted_chain_ids(trace) == ["chain_A"]
        # 关键：未调用 mark 时集合保持空
        assert runtime._is_chain_processed_by_root("org_a", "chain_A") is False


class TestRootProcessedChainsInitialState:
    """构造时的初始状态：字段存在 + 空 dict + LRU 上限正确。"""

    def test_initial_state(self, runtime: OrgRuntime):
        assert isinstance(runtime._root_processed_chains, dict)
        assert runtime._root_processed_chains == {}
        assert runtime._root_processed_chain_max_per_org == 512


class TestNodeProfileIsolationInheritance:
    def test_node_profile_inherits_base_memory_isolation(self, runtime: OrgRuntime):
        base = AgentProfile(
            id="isolated-specialist",
            name="Isolated Specialist",
            memory_mode="isolated",
            memory_inherit_global=False,
            runtime_env_mode="agent",
            runtime_env_dependencies=["requests"],
        )
        runtime._get_shared_profile = MagicMock(return_value=base)
        node = OrgNode(
            id="node_a",
            role_title="Researcher",
            agent_profile_id="isolated-specialist",
        )

        profile = runtime._build_profile_for_node(node, "node prompt")

        assert profile.id == "org_node_node_a"
        assert profile.memory_mode == "isolated"
        assert profile.memory_inherit_global is False
        assert profile.runtime_env_mode == "agent"
        assert profile.runtime_env_dependencies == ["requests"]
