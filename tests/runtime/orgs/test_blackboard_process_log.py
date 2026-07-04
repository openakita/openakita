"""P3 (test11): the blackboard is a LIVE, tier-aware process log.

Root cause (org_34856abd2e8c): during a run the blackboard panel showed
"暂无记录" because the contract tap only wrote a single org-tier
"节点X完成交付" fact at the END of each node. This pins that the process
events (派单/审阅/退回/上报/异常) now publish tier-aware records (node +
department + org) and broadcast ``org:blackboard_update`` for live refresh.
"""

from __future__ import annotations

import types
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from openakita.orgs.blackboard import JsonFileBlackboardBackend, OrgBlackboard
from openakita.orgs.memory_models import MemoryScope
from openakita.orgs.runtime import OrgRuntime


class _Registry:
    def __init__(self, bb: OrgBlackboard) -> None:
        self._bb = bb

    def for_org(self, _org_id: str) -> OrgBlackboard:
        return self._bb


class _Node:
    def __init__(self, node_id: str, department: str) -> None:
        self.id = node_id
        self.department = department


class _Org:
    def __init__(self, nodes: dict[str, _Node]) -> None:
        self._nodes = nodes

    def get_node(self, node_id: str) -> _Node | None:
        return self._nodes.get(node_id)


def _make_stub(tmp_path: Path, nodes: dict[str, _Node]) -> tuple[Any, OrgBlackboard, AsyncMock]:
    backend = JsonFileBlackboardBackend(tmp_path / "bb", "org-1")
    bb = OrgBlackboard(tmp_path, "org-1", backend=backend)
    broadcast = AsyncMock()

    stub = types.SimpleNamespace()
    stub._contract_blackboard = _Registry(bb)
    stub.get_org = lambda _oid: _Org(nodes)
    stub._broadcast_ws_safe = broadcast
    stub._resolve_node_department = types.MethodType(
        OrgRuntime._resolve_node_department, stub
    )
    stub._publish_process_log = types.MethodType(OrgRuntime._publish_process_log, stub)
    stub._publish_process_event = types.MethodType(
        OrgRuntime._publish_process_event, stub
    )
    return stub, bb, broadcast


@pytest.mark.asyncio
async def test_dispatch_writes_node_dept_and_org_tiers(tmp_path: Path) -> None:
    nodes = {"writer-a": _Node("writer-a", "编辑部")}
    stub, bb, broadcast = _make_stub(tmp_path, nodes)

    await stub._publish_process_event(
        "subtask_assigned",
        "org-1",
        node_id=None,
        parent="planner",
        child="writer-a",
        preview="写一份粉丝沙龙活动流程大纲",
        payload={},
    )

    node_rows = bb.read_node("writer-a")
    dept_rows = bb.read_department("编辑部")
    org_rows = bb.read_org()
    assert any("派单" in r.content for r in node_rows)
    assert any("派单" in r.content for r in dept_rows)
    assert any("派单" in r.content for r in org_rows)  # dispatch is org-significant
    broadcast.assert_awaited()  # live refresh fired


@pytest.mark.asyncio
async def test_rework_and_escalation_are_logged(tmp_path: Path) -> None:
    nodes = {"writer-b": _Node("writer-b", "编辑部")}
    stub, bb, _ = _make_stub(tmp_path, nodes)

    await stub._publish_process_event(
        "node_rework_requested",
        "org-1",
        node_id="writer-b",
        parent="planner",
        child="writer-b",
        preview="",
        payload={"reason": "下级未产出任何内容（空产出），需重做并给出完整成果。"},
    )
    await stub._publish_process_event(
        "node_review_escalated",
        "org-1",
        node_id="writer-b",
        parent="planner",
        child="writer-b",
        preview="",
        payload={"reason": "空产出"},
    )
    rows = bb.read_node("writer-b")
    contents = " ".join(r.content for r in rows)
    assert "退回重做" in contents
    assert "上报" in contents


@pytest.mark.asyncio
async def test_tool_failure_logged_node_tier_only(tmp_path: Path) -> None:
    nodes = {"data-analyst": _Node("data-analyst", "运营部")}
    stub, bb, _ = _make_stub(tmp_path, nodes)

    await stub._publish_process_event(
        "node_tool_failed",
        "org-1",
        node_id="data-analyst",
        parent=None,
        child=None,
        preview="",
        payload={"tool_name": "web_search", "error": "搜索源无法访问"},
    )
    node_rows = bb.read_node("data-analyst")
    assert any("web_search" in r.content and "异常" in r.content for r in node_rows)
    # tool anomalies are node/department detail, not org-tier noise
    assert bb.read_org() == []


@pytest.mark.asyncio
async def test_node_without_department_still_logs_node_tier(tmp_path: Path) -> None:
    nodes = {"solo": _Node("solo", "")}
    stub, bb, _ = _make_stub(tmp_path, nodes)
    await stub._publish_process_event(
        "agent_run_started",
        "org-1",
        node_id="solo",
        parent=None,
        child=None,
        preview="",
        payload={},
    )
    assert len(bb.read_node("solo")) == 1
    assert bb.read_department("") == []
