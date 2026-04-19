"""复现尝试：用户日志里 content_team 模板下 planner → writer-a/b/visual
全部被 ``org_delegate_task`` 报错"你就是 planner，不能把任务委派给自己"
的场景。

如果这些测试**全部通过**，说明真因不在 ``_handle_org_delegate_task`` 的
静态代码路径里（resolve_reference / affinity / strict 改写），而是某种
动态状态下才会触发；此时 A 的修复以 A2 的"生产级 trace + UX 兜底"为主。

如果某个测试失败（即被 _handle_org_delegate_task 拦截为自指），就锁定
了一个最小复现，直接定位到失败的代码路径修真因。

Setup：
- ``content_team`` 模板里的 7 个节点（editor-in-chief / planner /
  writer-a / writer-b / seo-opt / visual / data-analyst）+ 完整 edges
- mock_runtime_full：复用 affinity_attach_fix.py 的 fixture 形状

Scenarios：
- planner 在新 chain 下派给 writer-a / writer-b / visual
- planner 在 editor 派下来的 chain 上继续派给 writer-a（最贴近日志）
- planner 在已 affinity 绑定到 planner 自己的 chain 下派给 writer-a
"""

from __future__ import annotations

from collections import OrderedDict
from unittest.mock import AsyncMock, MagicMock

import pytest

from openakita.config import settings
from openakita.orgs.tool_handler import OrgToolHandler

from .conftest import make_edge, make_node, make_org


def _content_team_org(org_manager):
    """精确复刻 templates.py CONTENT_TEAM 的 7 节点 + edges 拓扑。"""
    nodes = [
        make_node("editor-in-chief", "主编", 0, "编辑部"),
        make_node("planner", "策划编辑", 1, "编辑部"),
        make_node("writer-a", "文案写手A", 2, "创作组"),
        make_node("writer-b", "文案写手B", 2, "创作组"),
        make_node("seo-opt", "SEO优化师", 1, "运营组"),
        make_node("visual", "视觉设计", 2, "创作组"),
        make_node("data-analyst", "数据分析", 1, "运营组"),
    ]
    edges = [
        make_edge("editor-in-chief", "planner"),
        make_edge("editor-in-chief", "seo-opt"),
        make_edge("editor-in-chief", "data-analyst"),
        make_edge("planner", "writer-a"),
        make_edge("planner", "writer-b"),
        make_edge("planner", "visual"),
    ]
    return org_manager.create(
        make_org(id="org_content_team", name="内容团队", nodes=nodes, edges=edges).to_dict()
    )


@pytest.fixture()
def content_team_org(org_manager):
    return _content_team_org(org_manager)


@pytest.fixture()
def mock_runtime_ct(content_team_org, org_manager):
    """与 affinity_attach_fix.py 的 mock_runtime_full 同形态，但绑定 content_team。"""
    org_dir = org_manager._org_dir(content_team_org.id)
    rt = MagicMock()
    rt._manager = org_manager
    rt.get_org = MagicMock(return_value=content_team_org)
    rt._active_orgs = {content_team_org.id: content_team_org}
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

    from openakita.orgs.blackboard import OrgBlackboard
    from openakita.orgs.event_store import OrgEventStore
    from openakita.orgs.messenger import OrgMessenger

    es = OrgEventStore(org_dir, content_team_org.id)
    bb = OrgBlackboard(org_dir, content_team_org.id)
    messenger = OrgMessenger(content_team_org, org_dir)

    rt.get_event_store = MagicMock(return_value=es)
    rt.get_blackboard = MagicMock(return_value=bb)
    rt.get_messenger = MagicMock(return_value=messenger)
    rt._broadcast_ws = AsyncMock()
    rt._save_org = AsyncMock()
    rt._mark_effective_action = MagicMock()
    rt._on_inbound_for_node = MagicMock()

    scaler_mock = MagicMock()
    scaler_mock.try_reclaim_idle_clones = AsyncMock(return_value=[])
    rt.get_scaler = MagicMock(return_value=scaler_mock)

    return rt


def _ok(result: str) -> bool:
    """delegate 成功的简单判定：返回串里含'已分配'/'已派' 或不含'失败'/'委派给自己'。"""
    if not isinstance(result, str):
        return False
    if "委派给自己" in result:
        return False
    if result.startswith("[org_delegate_task 失败]"):
        return False
    return "任务已分配" in result or "已分配" in result or "task_chain_id" in result


class TestPlannerToContentTeamMembers:
    """日志一比一复刻：planner 派给 writer-a / writer-b / visual。

    LLM 在 args 里直接传精确节点 id（与日志中 tool_use 完全一致）。
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("target", ["writer-a", "writer-b", "visual"])
    async def test_planner_delegate_to_direct_subordinate_no_self_loop(
        self, mock_runtime_ct, content_team_org, target,
    ):
        handler = OrgToolHandler(mock_runtime_ct)
        # handle() 是真实入口（会跑 _resolve_aliases / _resolve_node_refs）；
        # 这是日志路径上 LLM 调用的实际形态。
        result = await handler.handle(
            "org_delegate_task",
            {"to_node": target, "task": f"产出 {target} 任务"},
            content_team_org.id,
            "planner",
        )
        assert _ok(result), f"unexpected result for {target}: {result!r}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("target", ["writer-a", "writer-b", "visual"])
    async def test_planner_delegate_inherits_editor_chain(
        self, mock_runtime_ct, content_team_org, target,
    ):
        # editor 先把 chain X 派给 planner，affinity[X]=planner。
        messenger = mock_runtime_ct.get_messenger(content_team_org.id)
        messenger.bind_task_affinity("chain_X", "planner")
        mock_runtime_ct.get_current_chain_id = MagicMock(return_value="chain_X")

        handler = OrgToolHandler(mock_runtime_ct)
        result = await handler.handle(
            "org_delegate_task",
            {"to_node": target, "task": f"基于 chain X 的子任务"},
            content_team_org.id,
            "planner",
        )
        assert _ok(result), (
            f"affinity-on-self chain should NOT cause self-misjudgment "
            f"(target={target}): {result!r}"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("target", ["writer-a", "writer-b", "visual"])
    async def test_planner_delegate_with_explicit_chain_id_eq_inherited(
        self, mock_runtime_ct, content_team_org, target,
    ):
        """LLM 显式回传上级 chain_id（日志里 planner 也确实这么干过）。"""
        messenger = mock_runtime_ct.get_messenger(content_team_org.id)
        messenger.bind_task_affinity("chain_X", "planner")
        mock_runtime_ct.get_current_chain_id = MagicMock(return_value="chain_X")

        handler = OrgToolHandler(mock_runtime_ct)
        result = await handler.handle(
            "org_delegate_task",
            {
                "to_node": target,
                "task": f"显式带 chain_X 派 {target}",
                "task_chain_id": "chain_X",
            },
            content_team_org.id,
            "planner",
        )
        assert _ok(result), (
            f"explicit task_chain_id matching parent should pass-through "
            f"(target={target}): {result!r}"
        )

    @pytest.mark.asyncio
    async def test_planner_delegate_to_role_title(
        self, mock_runtime_ct, content_team_org,
    ):
        """LLM 偶尔会传角色名（"文案写手A"）而不是 id；strict 模式下应通过
        exact_title 改写到 writer-a，不应误判自指。"""
        handler = OrgToolHandler(mock_runtime_ct)
        result = await handler.handle(
            "org_delegate_task",
            {"to_node": "文案写手A", "task": "用角色名派"},
            content_team_org.id,
            "planner",
        )
        assert _ok(result), f"role-title delegation regression: {result!r}"


class TestPlannerNotMisidentifiedAsSelf:
    """额外探测：把可能触发自指的边界条件遍一遍，定位真因。"""

    @pytest.mark.asyncio
    async def test_resolve_reference_writer_a_returns_writer_a_node(
        self, content_team_org,
    ):
        node, _candidates, status = content_team_org.resolve_reference("writer-a")
        assert status == "exact_id"
        assert node is not None and node.id == "writer-a"

    @pytest.mark.asyncio
    async def test_planner_get_children_includes_writer_a(self, content_team_org):
        children = content_team_org.get_children("planner")
        ids = {c.id for c in children}
        assert ids == {"writer-a", "writer-b", "visual"}, ids

    @pytest.mark.asyncio
    async def test_planner_delegate_with_chain_already_open(
        self, mock_runtime_ct, content_team_org,
    ):
        """模拟一种已有 chain_delegation_depth 状态：planner 已经派过一轮
        writer-a，再来一轮 visual。两轮都不应自指。"""
        handler = OrgToolHandler(mock_runtime_ct)

        r1 = await handler.handle(
            "org_delegate_task",
            {"to_node": "writer-a", "task": "第一篇"},
            content_team_org.id, "planner",
        )
        assert _ok(r1), r1

        r2 = await handler.handle(
            "org_delegate_task",
            {"to_node": "visual", "task": "配图"},
            content_team_org.id, "planner",
        )
        assert _ok(r2), r2
