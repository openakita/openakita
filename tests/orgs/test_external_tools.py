"""Tests for external tool capabilities: categories, model, filtering, and tool request mechanism."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, AsyncMock

import pytest

from openakita.orgs.tool_categories import (
    TOOL_CATEGORIES,
    ROLE_TOOL_PRESETS,
    expand_tool_categories,
    get_preset_for_role,
    list_categories,
)
from openakita.orgs.models import OrgNode, Organization, OrgEdge, EdgeType
from openakita.orgs.tool_handler import OrgToolHandler


# ---------------------------------------------------------------------------
# tool_categories module
# ---------------------------------------------------------------------------


class TestToolCategories:
    def test_expand_empty(self):
        assert expand_tool_categories(None) == set()
        assert expand_tool_categories([]) == set()

    def test_expand_skips_blank_entries(self):
        result = expand_tool_categories(["research", "", "  ", "run_shell"])
        assert "" not in result
        assert "  " not in result
        assert "web_search" in result
        assert "run_shell" in result

    def test_expand_single_category(self):
        result = expand_tool_categories(["research"])
        assert result == {"web_search", "news_search"}

    def test_expand_multiple_categories(self):
        result = expand_tool_categories(["research", "planning"])
        assert "web_search" in result
        assert "create_todo" in result

    def test_expand_mixed_category_and_tool(self):
        result = expand_tool_categories(["research", "run_shell"])
        assert "web_search" in result
        assert "news_search" in result
        assert "run_shell" in result

    def test_expand_raw_tool_name(self):
        result = expand_tool_categories(["my_custom_tool"])
        assert result == {"my_custom_tool"}

    def test_all_categories_defined(self):
        for cat_name, tools in TOOL_CATEGORIES.items():
            assert isinstance(tools, list)
            assert len(tools) > 0, f"Category {cat_name} is empty"

    def test_role_presets_have_defaults(self):
        assert "default" in ROLE_TOOL_PRESETS
        assert "ceo" in ROLE_TOOL_PRESETS

    def test_get_preset_for_role_matches(self):
        assert "planning" in get_preset_for_role("CEO / 首席执行官")
        assert "filesystem" in get_preset_for_role("CTO / 技术总监")
        assert "filesystem" in get_preset_for_role("全栈工程师A")

    def test_get_preset_for_role_fallback(self):
        result = get_preset_for_role("未知角色XYZ")
        assert result == list(ROLE_TOOL_PRESETS["default"])

    def test_list_categories(self):
        cats = list_categories()
        assert len(cats) == len(TOOL_CATEGORIES)
        names = {c["name"] for c in cats}
        assert "research" in names
        assert "filesystem" in names


# ---------------------------------------------------------------------------
# OrgNode model
# ---------------------------------------------------------------------------


class TestOrgNodeExternalTools:
    def test_default_empty(self):
        node = OrgNode(id="n1")
        assert node.external_tools == []

    def test_to_dict_includes_field(self):
        node = OrgNode(id="n1", external_tools=["research", "planning"])
        d = node.to_dict()
        assert d["external_tools"] == ["research", "planning"]

    def test_to_dict_empty_list(self):
        node = OrgNode(id="n1")
        d = node.to_dict()
        assert d["external_tools"] == []

    def test_from_dict_with_external_tools(self):
        node = OrgNode.from_dict({
            "id": "n2",
            "external_tools": ["filesystem", "memory"],
        })
        assert node.external_tools == ["filesystem", "memory"]

    def test_from_dict_without_external_tools(self):
        node = OrgNode.from_dict({"id": "n3"})
        assert node.external_tools == []

    def test_round_trip(self):
        original = OrgNode(id="n4", external_tools=["research", "browser", "mcp"])
        rebuilt = OrgNode.from_dict(original.to_dict())
        assert rebuilt.external_tools == original.external_tools


# ---------------------------------------------------------------------------
# Tool request / grant / revoke handlers
# ---------------------------------------------------------------------------


@pytest.fixture()
def org_with_tools():
    from tests.orgs.conftest import make_node, make_edge
    nodes = [
        make_node("ceo", "CEO", 0, "管理层", external_tools=["research", "planning"]),
        make_node("cto", "CTO", 1, "技术部"),
        make_node("dev", "开发", 2, "技术部"),
    ]
    edges = [
        make_edge("ceo", "cto"),
        make_edge("cto", "dev"),
    ]
    return Organization(id="org_tools", name="工具测试", nodes=nodes, edges=edges)


@pytest.fixture()
def tool_handler_with_org(org_with_tools, tmp_path):
    from openakita.orgs.event_store import OrgEventStore
    from openakita.orgs.messenger import OrgMessenger
    from openakita.orgs.manager import OrgManager

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    mgr = OrgManager(data_dir)
    created = mgr.create(org_with_tools.to_dict())

    rt = MagicMock()
    rt._manager = mgr
    rt.get_org = MagicMock(return_value=created)
    rt._active_orgs = {created.id: created}

    org_dir = mgr._org_dir(created.id)
    es = OrgEventStore(org_dir, created.id)
    messenger = OrgMessenger(created, org_dir)

    rt.get_event_store = MagicMock(return_value=es)
    rt.get_messenger = MagicMock(return_value=messenger)
    rt._save_org = AsyncMock()
    rt.evict_node_agent = MagicMock()

    handler = OrgToolHandler(rt)
    return handler, rt, created


class TestToolRequestMechanism:
    async def test_request_tools_sends_to_superior(self, tool_handler_with_org):
        handler, rt, org = tool_handler_with_org
        result = await handler.handle(
            "org_request_tools",
            {"tools": ["filesystem"], "reason": "需要读写文件"},
            org.id, "cto",
        )
        assert "申请已发送" in result or "ceo" in result.lower() or "CEO" in result

    async def test_request_tools_empty_list_rejected(self, tool_handler_with_org):
        handler, rt, org = tool_handler_with_org
        result = await handler.handle(
            "org_request_tools",
            {"tools": [], "reason": "测试"},
            org.id, "cto",
        )
        assert "参数不完整" in result or "工具列表" in result

    async def test_request_tools_root_node_rejected(self, tool_handler_with_org):
        handler, rt, org = tool_handler_with_org
        result = await handler.handle(
            "org_request_tools",
            {"tools": ["filesystem"], "reason": "需要"},
            org.id, "ceo",
        )
        assert "最高级" in result or "无法" in result

    async def test_grant_tools_to_subordinate(self, tool_handler_with_org):
        handler, rt, org = tool_handler_with_org
        result = await handler.handle(
            "org_grant_tools",
            {"node_id": "cto", "tools": ["filesystem", "memory"]},
            org.id, "ceo",
        )
        assert "已授权" in result
        cto_node = org.get_node("cto")
        assert "filesystem" in cto_node.external_tools
        assert "memory" in cto_node.external_tools
        rt.evict_node_agent.assert_called_once_with(org.id, "cto")

    async def test_grant_tools_dedup(self, tool_handler_with_org):
        handler, rt, org = tool_handler_with_org
        await handler.handle(
            "org_grant_tools",
            {"node_id": "cto", "tools": ["research"]},
            org.id, "ceo",
        )
        await handler.handle(
            "org_grant_tools",
            {"node_id": "cto", "tools": ["research"]},
            org.id, "ceo",
        )
        cto_node = org.get_node("cto")
        assert cto_node.external_tools.count("research") == 1

    async def test_grant_tools_single_call_dedup(self, tool_handler_with_org):
        """Single grant call with duplicate entries should not create duplicates."""
        handler, rt, org = tool_handler_with_org
        await handler.handle(
            "org_grant_tools",
            {"node_id": "cto", "tools": ["research", "filesystem", "research"]},
            org.id, "ceo",
        )
        cto_node = org.get_node("cto")
        assert cto_node.external_tools.count("research") == 1
        assert "filesystem" in cto_node.external_tools

    async def test_grant_tools_not_subordinate(self, tool_handler_with_org):
        handler, rt, org = tool_handler_with_org
        result = await handler.handle(
            "org_grant_tools",
            {"node_id": "ceo", "tools": ["filesystem"]},
            org.id, "cto",
        )
        assert "不是你的直属下级" in result

    async def test_revoke_tools(self, tool_handler_with_org):
        handler, rt, org = tool_handler_with_org
        await handler.handle(
            "org_grant_tools",
            {"node_id": "cto", "tools": ["filesystem", "research"]},
            org.id, "ceo",
        )
        rt.evict_node_agent.reset_mock()

        result = await handler.handle(
            "org_revoke_tools",
            {"node_id": "cto", "tools": ["filesystem"]},
            org.id, "ceo",
        )
        assert "已收回" in result
        cto_node = org.get_node("cto")
        assert "filesystem" not in cto_node.external_tools
        assert "research" in cto_node.external_tools
        rt.evict_node_agent.assert_called_once()

    async def test_revoke_nonexistent_tools(self, tool_handler_with_org):
        handler, rt, org = tool_handler_with_org
        result = await handler.handle(
            "org_revoke_tools",
            {"node_id": "cto", "tools": ["browser"]},
            org.id, "ceo",
        )
        assert "没有" in result


# ---------------------------------------------------------------------------
# Templates include external_tools
# ---------------------------------------------------------------------------


class TestTemplatesHaveExternalTools:
    def test_startup_template(self):
        from openakita.orgs.templates import STARTUP_COMPANY
        for node in STARTUP_COMPANY["nodes"]:
            assert "external_tools" in node, f"Node {node['id']} missing external_tools"
            assert isinstance(node["external_tools"], list)
            assert len(node["external_tools"]) > 0

    def test_software_team_template(self):
        from openakita.orgs.templates import SOFTWARE_TEAM
        for node in SOFTWARE_TEAM["nodes"]:
            assert "external_tools" in node, f"Node {node['id']} missing external_tools"

    def test_content_ops_template(self):
        from openakita.orgs.templates import CONTENT_OPS
        for node in CONTENT_OPS["nodes"]:
            assert "external_tools" in node, f"Node {node['id']} missing external_tools"
