"""单元测试：多层级组织指挥治理（org-orchestration-fix）。

覆盖以下子修复：
  - J: chain 父子关系登记 + tracker 子树意识
  - A: submit_deliverable 强制 chain；send_message question heuristic guard
  - B: Supervisor poll-friendly 白名单
  - C: org_wait_for_deliverable 工具的多事件 wait 与 fall-through
  - F: UserCommandTracker 的 awaiting_root_summary 状态机
  - L: failure_diagnoser 新增 args_raw_truncated 字段
  - 灰度 flag：关闭后回退旧行为
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from unittest.mock import MagicMock

import pytest

from openakita.config import settings
from openakita.core.supervisor import (
    POLL_FRIENDLY_TOOLS,
    InterventionLevel,
    RuntimeSupervisor,
)
from openakita.orgs.failure_diagnoser import _extract_evidence
from openakita.orgs.runtime import UserCommandTracker
from openakita.orgs.tool_handler import OrgToolHandler

# ---------------------------------------------------------------------------
# 共用 mock runtime（在 conftest.mock_runtime 之上补全 chain 字段）
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_runtime_full(mock_runtime):
    mock_runtime._chain_parent = {}
    mock_runtime._chain_events = OrderedDict()
    mock_runtime._max_chain_events = 256
    mock_runtime._node_inbox_events = {}
    mock_runtime._closed_chains = {}
    mock_runtime._touch_trackers_for_org = MagicMock()
    return mock_runtime


# ---------------------------------------------------------------------------
# A.1 / J: chain 父子关系登记 + submit 强制 chain
# ---------------------------------------------------------------------------


class TestChainParentPropagation:
    @pytest.mark.asyncio
    async def test_delegate_creates_child_chain_under_caller(
        self, mock_runtime_full, persisted_org,
    ):
        handler = OrgToolHandler(mock_runtime_full)
        # caller (CTO) 已经在 chain X 下处理任务
        mock_runtime_full.get_current_chain_id = MagicMock(return_value="chain_X")

        # CTO 把任务派给 dev
        result = await handler._handle_org_delegate_task(
            {"to_node": "node_dev", "task": "实现登录"},
            persisted_org.id, "node_cto",
        )
        assert "任务已分配" in result
        # 应至少存在一条以 chain_X 为父的子 chain
        children = [
            c for c, p in mock_runtime_full._chain_parent.items()
            if p == "chain_X"
        ]
        assert len(children) == 1
        new_chain = children[0]
        assert new_chain != "chain_X"
        # chain 关闭事件已经登记
        assert new_chain in mock_runtime_full._chain_events

    @pytest.mark.asyncio
    async def test_delegate_root_with_no_chain_creates_top_level_chain(
        self, mock_runtime_full, persisted_org,
    ):
        handler = OrgToolHandler(mock_runtime_full)
        mock_runtime_full.get_current_chain_id = MagicMock(return_value=None)
        await handler._handle_org_delegate_task(
            {"to_node": "node_cto", "task": "做技术规划"},
            persisted_org.id, "node_ceo",
        )
        # 顶层 chain 的 parent 应为 None
        assert any(
            p is None for p in mock_runtime_full._chain_parent.values()
        )

    @pytest.mark.asyncio
    async def test_delegate_flag_off_falls_back_to_legacy(
        self, mock_runtime_full, persisted_org, monkeypatch,
    ):
        monkeypatch.setattr(settings, "org_chain_parent_enforced", False)
        handler = OrgToolHandler(mock_runtime_full)
        mock_runtime_full.get_current_chain_id = MagicMock(return_value="chain_X")
        await handler._handle_org_delegate_task(
            {"to_node": "node_dev", "task": "测试"},
            persisted_org.id, "node_cto",
        )
        # 旧行为：复用 caller chain，不应在 _chain_parent 里挂新条目
        assert all(p is None for p in mock_runtime_full._chain_parent.values())


# ---------------------------------------------------------------------------
# A.1: submit_deliverable 强制 caller current chain
# ---------------------------------------------------------------------------


class TestSubmitChainOverride:
    @pytest.mark.asyncio
    async def test_submit_overrides_wrong_chain_id_from_llm(
        self, mock_runtime_full, persisted_org, caplog,
    ):
        handler = OrgToolHandler(mock_runtime_full)
        # caller (dev) 的 incoming chain 是 chain_X
        mock_runtime_full.get_current_chain_id = MagicMock(return_value="chain_X")
        # mock 一个 _link_project_task 之类避免 ProjectStore 副作用
        handler._link_project_task = MagicMock()
        handler._append_execution_log = MagicMock()
        handler._recalc_parent_progress = MagicMock()

        with caplog.at_level("WARNING", logger="openakita.orgs.tool_handler"):
            await handler._handle_org_submit_deliverable(
                {
                    # LLM 漏传 / 传错，比如自创了一个 timestamp
                    "task_chain_id": "chain_WRONG_NEW",
                    "deliverable": "我做完了" * 50,  # >200 字
                },
                persisted_org.id, "node_dev",
            )

        # 应有 warning 提示 chain_id mismatch
        assert any(
            "chain_id mismatch" in rec.message for rec in caplog.records
        )
        # _link_project_task 应该用 caller current chain（X），而不是 LLM 的 WRONG
        assert handler._link_project_task.called
        called_chain = handler._link_project_task.call_args.args[1]
        assert called_chain == "chain_X"

    @pytest.mark.asyncio
    async def test_submit_uses_current_chain_when_llm_omits(
        self, mock_runtime_full, persisted_org,
    ):
        handler = OrgToolHandler(mock_runtime_full)
        mock_runtime_full.get_current_chain_id = MagicMock(return_value="chain_X")
        handler._link_project_task = MagicMock()
        handler._append_execution_log = MagicMock()
        handler._recalc_parent_progress = MagicMock()

        await handler._handle_org_submit_deliverable(
            {"deliverable": "x" * 250},
            persisted_org.id, "node_dev",
        )
        called_chain = handler._link_project_task.call_args.args[1]
        assert called_chain == "chain_X"


# ---------------------------------------------------------------------------
# A.2: send_message question-as-task heuristic guard
# ---------------------------------------------------------------------------


class TestQuestionTaskGuard:
    @pytest.mark.asyncio
    async def test_block_question_with_task_intent_from_coordinator(
        self, mock_runtime_full, persisted_org,
    ):
        handler = OrgToolHandler(mock_runtime_full)
        # CEO 是有下属的协调者；CTO/dev 是它的下属
        result = await handler._handle_org_send_message(
            {
                "to_node": "node_cto",
                "msg_type": "question",
                "content": "请撰写一份服务器选型方案，下午前给我",
            },
            persisted_org.id, "node_ceo",
        )
        assert "拦截" in result
        assert "org_delegate_task" in result

    @pytest.mark.asyncio
    async def test_allow_pure_question_without_task_intent(
        self, mock_runtime_full, persisted_org,
    ):
        handler = OrgToolHandler(mock_runtime_full)
        result = await handler._handle_org_send_message(
            {
                "to_node": "node_cto",
                "msg_type": "question",
                "content": "你最近忙吗？",
            },
            persisted_org.id, "node_ceo",
        )
        assert "拦截" not in result

    @pytest.mark.asyncio
    async def test_allow_leaf_node_question_with_task_intent(
        self, mock_runtime_full, persisted_org,
    ):
        handler = OrgToolHandler(mock_runtime_full)
        # node_dev 是叶子节点，没下属——guard 不应触发
        result = await handler._handle_org_send_message(
            {
                "to_node": "node_cto",
                "msg_type": "question",
                "content": "请撰写一份测试计划给我",
            },
            persisted_org.id, "node_dev",
        )
        assert "拦截" not in result

    @pytest.mark.asyncio
    async def test_flag_off_disables_guard(
        self, mock_runtime_full, persisted_org, monkeypatch,
    ):
        monkeypatch.setattr(settings, "org_question_task_guard", False)
        handler = OrgToolHandler(mock_runtime_full)
        result = await handler._handle_org_send_message(
            {
                "to_node": "node_cto",
                "msg_type": "question",
                "content": "请撰写一份服务器选型方案",
            },
            persisted_org.id, "node_ceo",
        )
        assert "拦截" not in result


# ---------------------------------------------------------------------------
# B: Supervisor poll-friendly 白名单
# ---------------------------------------------------------------------------


class TestSupervisorPollWhitelist:
    def test_poll_friendly_set_includes_wait_tool(self):
        assert "org_wait_for_deliverable" in POLL_FRIENDLY_TOOLS
        assert "org_list_delegated_tasks" in POLL_FRIENDLY_TOOLS

    def test_poll_friendly_never_terminates(self, monkeypatch):
        monkeypatch.setattr(
            settings, "org_supervisor_poll_whitelist", True,
        )
        sup = RuntimeSupervisor(enabled=True)
        # 让同一个 poll 工具签名重复 10 次（超过 normal terminate 阈值，
        # 但低于 poll 翻倍后阈值）
        for _ in range(8):
            sup.record_tool_signature("org_list_delegated_tasks(status='in_progress')")
        out = sup._check_signature_repeat(iteration=8)
        # 应该是 NUDGE 或 None，绝不能 TERMINATE / STRATEGY_SWITCH
        if out is not None:
            assert out.level == InterventionLevel.NUDGE
            assert "wait" in (out.prompt_injection or "").lower()

    def test_poll_friendly_extreme_repeats_still_capped_at_nudge(
        self, monkeypatch,
    ):
        monkeypatch.setattr(
            settings, "org_supervisor_poll_whitelist", True,
        )
        sup = RuntimeSupervisor(enabled=True)
        # 远远超过 poll 翻倍阈值
        for _ in range(40):
            sup.record_tool_signature("org_wait_for_deliverable()")
        out = sup._check_signature_repeat(iteration=40)
        assert out is not None
        assert out.level == InterventionLevel.NUDGE

    def test_normal_tool_still_terminates_after_threshold(self):
        sup = RuntimeSupervisor(enabled=True)
        for _ in range(8):
            sup.record_tool_signature("custom_evil_tool(x=1)")
        out = sup._check_signature_repeat(iteration=8)
        assert out is not None
        assert out.level == InterventionLevel.TERMINATE

    def test_flag_off_disables_whitelist(self, monkeypatch):
        monkeypatch.setattr(
            settings, "org_supervisor_poll_whitelist", False,
        )
        sup = RuntimeSupervisor(enabled=True)
        for _ in range(8):
            sup.record_tool_signature("org_list_delegated_tasks(status='x')")
        out = sup._check_signature_repeat(iteration=8)
        # flag off → poll-friendly 不豁免 → 走原 TERMINATE 路径
        assert out is not None
        assert out.level == InterventionLevel.TERMINATE


# ---------------------------------------------------------------------------
# C: org_wait_for_deliverable 多事件 wait
# ---------------------------------------------------------------------------


class TestWaitForDeliverable:
    @pytest.mark.asyncio
    async def test_wait_returns_when_chain_closes(
        self, mock_runtime_full, persisted_org,
    ):
        handler = OrgToolHandler(mock_runtime_full)
        mock_runtime_full.get_current_chain_id = MagicMock(return_value="parent_X")
        mock_runtime_full.is_chain_closed = MagicMock(return_value=False)
        # 制造两条以 parent_X 为父的子 chain
        mock_runtime_full._chain_parent["child_A"] = "parent_X"
        mock_runtime_full._chain_parent["child_B"] = "parent_X"
        mock_runtime_full._chain_events["child_A"] = asyncio.Event()
        mock_runtime_full._chain_events["child_B"] = asyncio.Event()

        async def _close_one():
            await asyncio.sleep(0.05)
            mock_runtime_full._chain_events["child_A"].set()
            # 让 wait 内的 is_chain_closed re-check 看到 child_A 已关
            mock_runtime_full.is_chain_closed = MagicMock(
                side_effect=lambda _o, c: c == "child_A",
            )

        closer = asyncio.create_task(_close_one())
        result = await handler._handle_org_wait_for_deliverable(
            {"timeout": 5}, persisted_org.id, "node_cto",
        )
        await closer
        assert "child_A" in result
        assert "已关闭" in result

    @pytest.mark.asyncio
    async def test_wait_returns_on_inbox_event(
        self, mock_runtime_full, persisted_org,
    ):
        handler = OrgToolHandler(mock_runtime_full)
        mock_runtime_full.get_current_chain_id = MagicMock(return_value="parent_X")
        mock_runtime_full.is_chain_closed = MagicMock(return_value=False)
        mock_runtime_full._chain_parent["child_A"] = "parent_X"
        mock_runtime_full._chain_events["child_A"] = asyncio.Event()

        inbox_key = f"{persisted_org.id}:node_cto"

        async def _push_inbox():
            await asyncio.sleep(0.05)
            ev = mock_runtime_full._node_inbox_events.get(inbox_key)
            assert ev is not None
            ev.set()

        pusher = asyncio.create_task(_push_inbox())
        result = await handler._handle_org_wait_for_deliverable(
            {"timeout": 5}, persisted_org.id, "node_cto",
        )
        await pusher
        assert "新消息" in result

    @pytest.mark.asyncio
    async def test_wait_timeout_returns_useful_hint(
        self, mock_runtime_full, persisted_org,
    ):
        handler = OrgToolHandler(mock_runtime_full)
        mock_runtime_full.get_current_chain_id = MagicMock(return_value="parent_X")
        mock_runtime_full.is_chain_closed = MagicMock(return_value=False)
        mock_runtime_full._chain_parent["child_A"] = "parent_X"
        mock_runtime_full._chain_events["child_A"] = asyncio.Event()

        result = await handler._handle_org_wait_for_deliverable(
            {"timeout": 1}, persisted_org.id, "node_cto",
        )
        assert "等待超时" in result
        assert "org_list_delegated_tasks" in result

    @pytest.mark.asyncio
    async def test_wait_returns_immediately_when_no_open_chains(
        self, mock_runtime_full, persisted_org,
    ):
        handler = OrgToolHandler(mock_runtime_full)
        mock_runtime_full.get_current_chain_id = MagicMock(return_value="parent_X")
        mock_runtime_full.is_chain_closed = MagicMock(return_value=True)
        mock_runtime_full._chain_parent["child_A"] = "parent_X"

        result = await handler._handle_org_wait_for_deliverable(
            {"timeout": 5}, persisted_org.id, "node_cto",
        )
        assert "没有需要等待" in result

    @pytest.mark.asyncio
    async def test_wait_disabled_when_flag_off(
        self, mock_runtime_full, persisted_org, monkeypatch,
    ):
        monkeypatch.setattr(settings, "org_wait_primitive_enabled", False)
        handler = OrgToolHandler(mock_runtime_full)
        result = await handler._handle_org_wait_for_deliverable(
            {"timeout": 5}, persisted_org.id, "node_cto",
        )
        assert "已禁用" in result


# ---------------------------------------------------------------------------
# F + 子树意识：UserCommandTracker 状态机
# ---------------------------------------------------------------------------


class TestUserCommandTrackerSubtree:
    def test_register_chain_sets_root_chain_id_first_time(self):
        t = UserCommandTracker("org", "node_root", command_id="c1")
        t.register_chain("chain_root")
        assert t.root_chain_id == "chain_root"
        t.register_chain("chain_other")
        # root_chain_id 不应被覆盖
        assert t.root_chain_id == "chain_root"

    def test_unregister_chain_drops_from_open_set(self):
        t = UserCommandTracker("org", "node_root")
        t.register_chain("chain_X")
        t.unregister_chain("chain_X")
        assert "chain_X" not in t.open_chains


# ---------------------------------------------------------------------------
# L: failure_diagnoser 新增 args_raw_truncated 字段
# ---------------------------------------------------------------------------


class TestArgsRawTruncated:
    def test_evidence_includes_args_raw_truncated(self):
        trace = [
            {
                "iteration": 1,
                "tool_calls": [
                    {
                        "id": "t1",
                        "name": "org_delegate_task",
                        "input": {
                            "to_node": "content-op",
                            "task": "写文章",
                            "task_chain_id": "abc123",
                        },
                    },
                ],
                "tool_results": [
                    {
                        "tool_use_id": "t1",
                        "result_content": "[失败] 不存在的节点",
                        "is_error": True,
                    },
                ],
            },
        ]
        evidence = _extract_evidence(trace)
        assert len(evidence) == 1
        ev = evidence[0]
        assert "args_raw_truncated" in ev
        # 应该是 JSON 字符串，包含 to_node 字段
        assert "content-op" in ev["args_raw_truncated"]
        assert "task_chain_id" in ev["args_raw_truncated"]

    def test_args_raw_truncated_caps_length(self):
        long_payload = "x" * 5000
        trace = [
            {
                "iteration": 1,
                "tool_calls": [
                    {"id": "t1", "name": "any", "input": {"big": long_payload}},
                ],
                "tool_results": [
                    {"tool_use_id": "t1", "result_content": "boom", "is_error": True},
                ],
            },
        ]
        evidence = _extract_evidence(trace)
        assert len(evidence[0]["args_raw_truncated"]) <= 1024 + len("…")
