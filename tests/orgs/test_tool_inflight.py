"""ToolHandler 在途锁测试

覆盖 LLM 在同一 ReAct iter emit 多个相同 tool_use（如 5 次 org_delegate_task
对同一 chain 同一目标）时，OrgRuntime._tool_inflight_keys 应该只放行第一次，
后续返回明确的"[去重]" 文案，避免下游 mailbox 被多次入队、附件出现 N 份。

涵盖：
  * org_delegate_task：同 (org, caller, to_node, chain) 5s 内只执行一次
  * org_submit_deliverable：同 (org, caller, to_node, chain) 5s 内只执行一次
  * 不同 chain / 不同 to_node 不被影响
  * 锁释放后允许再次执行（模拟时间走过窗口）

测试基于 OrgRuntime 的 _try_acquire_tool_inflight / _release_tool_inflight 真实
方法，不 mock。
"""

from __future__ import annotations

from collections import OrderedDict
from unittest.mock import AsyncMock, MagicMock

import pytest

from openakita.orgs.runtime import OrgRuntime
from openakita.orgs.tool_handler import OrgToolHandler

from .conftest import make_edge, make_node, make_org


def _two_level_org(org_manager):
    nodes = [
        make_node("editor-in-chief", "主编", 0, "编辑部"),
        make_node("planner", "策划编辑", 1, "编辑部"),
        make_node("writer-a", "文案写手A", 2, "创作组"),
        make_node("writer-b", "文案写手B", 2, "创作组"),
    ]
    edges = [
        make_edge("editor-in-chief", "planner"),
        make_edge("planner", "writer-a"),
        make_edge("planner", "writer-b"),
    ]
    return org_manager.create(
        make_org(id="org_inflight", name="测试组织", nodes=nodes, edges=edges).to_dict()
    )


@pytest.fixture()
def org(org_manager):
    return _two_level_org(org_manager)


@pytest.fixture()
def runtime_with_locks(org, org_manager):
    """直接复用 OrgRuntime 实例的锁方法，其他依赖用 MagicMock 兜住。"""
    org_dir = org_manager._org_dir(org.id)
    rt = MagicMock(spec=OrgRuntime)
    real_rt = OrgRuntime.__new__(OrgRuntime)
    real_rt._tool_inflight_keys = {}
    real_rt._tool_inflight_window_secs = 5.0
    rt._tool_inflight_keys = real_rt._tool_inflight_keys
    rt._tool_inflight_window_secs = real_rt._tool_inflight_window_secs
    rt._try_acquire_tool_inflight = real_rt._try_acquire_tool_inflight.__get__(real_rt)
    rt._release_tool_inflight = real_rt._release_tool_inflight.__get__(real_rt)

    rt._manager = org_manager
    rt.get_org = MagicMock(return_value=org)
    rt._active_orgs = {org.id: org}
    rt._chain_delegation_depth = {}
    rt._chain_parent = {}
    rt._chain_events = OrderedDict()
    rt._max_chain_events = 256
    rt._node_inbox_events = {}
    rt._closed_chains = {}
    rt.is_chain_closed = MagicMock(return_value=False)
    rt.get_current_chain_id = MagicMock(return_value=None)
    rt._cleanup_accepted_chain = MagicMock(return_value=None)
    rt._touch_trackers_for_org = MagicMock()
    rt._mark_effective_action = MagicMock()
    rt._on_inbound_for_node = MagicMock()
    rt._tracker_register_chain = MagicMock()
    rt._link_project_task = MagicMock()
    rt._append_execution_log = MagicMock()
    rt._recalc_parent_progress = MagicMock()

    from openakita.orgs.blackboard import OrgBlackboard
    from openakita.orgs.event_store import OrgEventStore
    from openakita.orgs.messenger import OrgMessenger

    es = OrgEventStore(org_dir, org.id)
    bb = OrgBlackboard(org_dir, org.id)
    messenger = OrgMessenger(org, org_dir)

    rt.get_event_store = MagicMock(return_value=es)
    rt.get_blackboard = MagicMock(return_value=bb)
    rt.get_messenger = MagicMock(return_value=messenger)
    rt._broadcast_ws = AsyncMock()
    rt._save_org = AsyncMock()
    rt._resolve_org_workspace = MagicMock(return_value=None)
    rt._register_file_output = MagicMock(return_value=None)

    scaler_mock = MagicMock()
    scaler_mock.try_reclaim_idle_clones = AsyncMock(return_value=[])
    rt.get_scaler = MagicMock(return_value=scaler_mock)

    return rt


def _is_dedupe_drop(result: str) -> bool:
    return isinstance(result, str) and result.startswith("[去重]")


class TestDelegateInflightLock:
    @pytest.mark.asyncio
    async def test_concurrent_delegate_same_chain_target_no_double_enqueue(
        self, runtime_with_locks, org,
    ):
        """实战场景下两道防线一起生效：
          * 第一次 delegate 成功，让 ProjectStore 落盘 in_progress
          * 第二次 delegate 会被 ProjectStore 早期拦截（"已在处理此任务链"）
        即使 ProjectStore 拦截在前，inflight 锁仍是必要兜底（ProjectStore
        还未落盘的真并发窗口），下游 mailbox 必然只入队一条。
        """
        handler = OrgToolHandler(runtime_with_locks)
        first = await handler.handle(
            "org_delegate_task",
            {"to_node": "writer-a", "task": "产出 A", "task_chain_id": "chain-X"},
            org.id, "planner",
        )
        assert not _is_dedupe_drop(first), f"first call wrongly dropped: {first!r}"
        second = await handler.handle(
            "org_delegate_task",
            {"to_node": "writer-a", "task": "产出 A", "task_chain_id": "chain-X"},
            org.id, "planner",
        )
        # 第二次应被拦截，但拦截源可能是 ProjectStore 也可能是 inflight 锁，
        # 关键不变量：下游 mailbox 不会被重复入队。
        messenger = runtime_with_locks.get_messenger(org.id)
        mb = messenger.get_mailbox("writer-a")
        assert mb is not None and mb.pending_count == 1, (
            f"writer-a mailbox should have exactly 1 task, got {mb.pending_count}; "
            f"first={first!r} second={second!r}"
        )

    @pytest.mark.asyncio
    async def test_inflight_lock_blocks_when_projectstore_bypassed(
        self, runtime_with_locks, org,
    ):
        """直接验证 inflight 锁单点功能：手动预占同 key 后，handle() 必须返回 [去重]。

        模拟"两次 tool_use 真并发，第一次 ProjectStore 还没落盘"的窗口。
        """
        handler = OrgToolHandler(runtime_with_locks)
        # 手动占用 inflight key（模拟"第一次 delegate 在飞行中"）
        key = f"delegate:{org.id}:planner:writer-a:chain-Y"
        runtime_with_locks._tool_inflight_keys[key] = __import__("time").time()
        result = await handler.handle(
            "org_delegate_task",
            {"to_node": "writer-a", "task": "产出 Y", "task_chain_id": "chain-Y"},
            org.id, "planner",
        )
        assert _is_dedupe_drop(result), (
            f"handle should hit inflight lock and return dedupe drop: {result!r}"
        )

    @pytest.mark.asyncio
    async def test_different_target_not_locked(self, runtime_with_locks, org):
        handler = OrgToolHandler(runtime_with_locks)
        r1 = await handler.handle(
            "org_delegate_task",
            {"to_node": "writer-a", "task": "A", "task_chain_id": "c1"},
            org.id, "planner",
        )
        r2 = await handler.handle(
            "org_delegate_task",
            {"to_node": "writer-b", "task": "B", "task_chain_id": "c1"},
            org.id, "planner",
        )
        assert not _is_dedupe_drop(r1)
        assert not _is_dedupe_drop(r2)

    @pytest.mark.asyncio
    async def test_window_expiry_allows_redelegate(self, runtime_with_locks, org):
        import time as _t
        handler = OrgToolHandler(runtime_with_locks)
        first = await handler.handle(
            "org_delegate_task",
            {"to_node": "writer-a", "task": "A", "task_chain_id": "c-expiry"},
            org.id, "planner",
        )
        assert not _is_dedupe_drop(first)
        # 把锁的时间戳手动倒退到窗口外
        for k in list(runtime_with_locks._tool_inflight_keys.keys()):
            runtime_with_locks._tool_inflight_keys[k] = _t.time() - 100.0
        third = await handler.handle(
            "org_delegate_task",
            {"to_node": "writer-a", "task": "A", "task_chain_id": "c-expiry"},
            org.id, "planner",
        )
        assert not _is_dedupe_drop(third), f"after expiry should pass: {third!r}"


class TestSubmitDeliverableInflightLock:
    @pytest.mark.asyncio
    async def test_concurrent_submit_same_chain_dropped(
        self, runtime_with_locks, org,
    ):
        handler = OrgToolHandler(runtime_with_locks)
        # writer-a -> planner 同一 chain 的两次 submit
        runtime_with_locks.get_current_chain_id = MagicMock(return_value="chain-S")
        first = await handler.handle(
            "org_submit_deliverable",
            {
                "to_node": "planner",
                "deliverable": "稿子 A",
                "summary": "v1",
                "task_chain_id": "chain-S",
            },
            org.id, "writer-a",
        )
        assert not _is_dedupe_drop(first), f"first submit wrongly dropped: {first!r}"
        second = await handler.handle(
            "org_submit_deliverable",
            {
                "to_node": "planner",
                "deliverable": "稿子 A",
                "summary": "v1",
                "task_chain_id": "chain-S",
            },
            org.id, "writer-a",
        )
        assert _is_dedupe_drop(second), f"second submit should be deduped: {second!r}"
