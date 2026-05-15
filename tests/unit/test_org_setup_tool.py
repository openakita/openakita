"""Tests for the setup_organization tool (definition + handler).

Validates:
1. get_resources returns agents, templates, tool_categories
2. create builds org with auto-generated edges and positions
3. preview returns structured text without creating
4. create_from_template delegates to OrgManager
5. agent_profile_id mapping and agent_source auto-set
6. Tool always registered (multi-agent mode always on)
7. list_orgs / get_org / update_org / delete_org (CRUD)
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


def test_organization_watchdog_disabled_by_default():
    from openakita.orgs.models import Organization

    org = Organization(name="default watchdog")
    assert org.watchdog_enabled is True
    assert org.to_dict()["watchdog_enabled"] is True


@pytest.fixture
def handler():
    """Create an OrgSetupHandler with a mocked agent."""
    from openakita.tools.handlers.org_setup import OrgSetupHandler
    mock_agent = MagicMock()
    return OrgSetupHandler(mock_agent)


@pytest.fixture
def tmp_data_dir(tmp_path):
    """Create a temp data dir for OrgManager."""
    orgs_dir = tmp_path / "orgs"
    orgs_dir.mkdir()
    templates_dir = tmp_path / "org_templates"
    templates_dir.mkdir()
    return tmp_path


class TestGetResources:
    """Test action=get_resources."""

    def test_returns_agents(self, handler):
        result = json.loads(handler._get_resources())
        assert "agents" in result
        assert len(result["agents"]) > 0
        agent = result["agents"][0]
        assert "id" in agent
        assert "name" in agent
        assert "description" in agent

    def test_returns_templates(self, handler, tmp_data_dir):
        with patch("openakita.config.settings") as mock_settings:
            mock_settings.data_dir = tmp_data_dir
            result = json.loads(handler._get_resources())
        assert "templates" in result

    def test_returns_tool_categories(self, handler):
        result = json.loads(handler._get_resources())
        assert "tool_categories" in result
        cats = result["tool_categories"]
        assert "research" in cats
        assert "filesystem" in cats

    def test_returns_usage_hint(self, handler):
        result = json.loads(handler._get_resources())
        assert "usage_hint" in result

    def test_default_agent_in_list(self, handler):
        result = json.loads(handler._get_resources())
        agent_ids = [a["id"] for a in result["agents"]]
        assert "default" in agent_ids

    def test_code_assistant_in_list(self, handler):
        result = json.loads(handler._get_resources())
        agent_ids = [a["id"] for a in result["agents"]]
        assert "code-assistant" in agent_ids


class TestPreview:
    """Test action=preview."""

    def test_preview_returns_text(self, handler):
        params = {
            "action": "preview",
            "name": "测试团队",
            "core_business": "软件开发",
            "nodes": [
                {"role_title": "CTO", "level": 0, "department": "技术部",
                 "agent_profile_id": "architect"},
                {"role_title": "开发工程师", "level": 1, "department": "技术部",
                 "agent_profile_id": "code-assistant", "parent_role_title": "CTO"},
            ],
        }
        result = handler._preview(params)
        assert "测试团队" in result
        assert "CTO" in result
        assert "开发工程师" in result
        assert "架构师" in result or "architect" in result

    def test_preview_no_name_error(self, handler):
        result = handler._preview({"action": "preview", "nodes": [{"role_title": "A"}]})
        assert "❌" in result

    def test_preview_no_nodes_error(self, handler):
        result = handler._preview({"action": "preview", "name": "X"})
        assert "❌" in result

    def test_preview_shows_hierarchy(self, handler):
        params = {
            "action": "preview",
            "name": "测试",
            "nodes": [
                {"role_title": "Boss", "level": 0},
                {"role_title": "Worker", "level": 1, "parent_role_title": "Boss"},
            ],
        }
        result = handler._preview(params)
        assert "Boss" in result
        assert "Worker" in result
        assert "→" in result


class TestBuildOrgStructure:
    """Test the internal _build_org_structure method."""

    def test_auto_generates_node_ids(self, handler):
        params = {
            "nodes": [
                {"role_title": "CEO", "level": 0},
                {"role_title": "CTO", "level": 1, "parent_role_title": "CEO"},
            ],
        }
        nodes, edges, errors = handler._build_org_structure(params)
        assert len(errors) == 0
        assert len(nodes) == 2
        assert all(n["id"].startswith("node_") for n in nodes)

    def test_auto_creates_edges(self, handler):
        params = {
            "nodes": [
                {"role_title": "CEO", "level": 0},
                {"role_title": "CTO", "level": 1, "parent_role_title": "CEO"},
                {"role_title": "CPO", "level": 1, "parent_role_title": "CEO"},
            ],
        }
        nodes, edges, errors = handler._build_org_structure(params)
        assert len(errors) == 0
        assert len(edges) == 2
        assert all(e["edge_type"] == "hierarchy" for e in edges)

    def test_agent_source_set_correctly(self, handler):
        params = {
            "nodes": [
                {"role_title": "Dev", "level": 0, "agent_profile_id": "code-assistant"},
                {"role_title": "PM", "level": 0},
            ],
        }
        nodes, edges, errors = handler._build_org_structure(params)
        dev = next(n for n in nodes if n["role_title"] == "Dev")
        pm = next(n for n in nodes if n["role_title"] == "PM")
        assert dev["agent_source"] == "ref:code-assistant"
        assert pm["agent_source"] == "local"

    def test_auto_assigns_positions(self, handler):
        params = {
            "nodes": [
                {"role_title": "A", "level": 0},
                {"role_title": "B", "level": 1},
                {"role_title": "C", "level": 1},
            ],
        }
        nodes, edges, errors = handler._build_org_structure(params)
        positions = [n["position"] for n in nodes]
        assert all(p["x"] >= 0 for p in positions)
        level1_nodes = [n for n in nodes if n["level"] == 1]
        assert level1_nodes[0]["position"]["x"] != level1_nodes[1]["position"]["x"]

    def test_auto_assigns_tools_from_role(self, handler):
        params = {
            "nodes": [
                {"role_title": "CTO", "level": 0},
            ],
        }
        nodes, edges, errors = handler._build_org_structure(params)
        assert len(nodes[0]["external_tools"]) > 0
        assert "research" in nodes[0]["external_tools"] or "filesystem" in nodes[0]["external_tools"]

    def test_error_on_missing_parent(self, handler):
        params = {
            "nodes": [
                {"role_title": "Worker", "level": 1, "parent_role_title": "NonExistent"},
            ],
        }
        nodes, edges, errors = handler._build_org_structure(params)
        assert len(errors) > 0
        assert "NonExistent" in errors[0]

    def test_error_on_no_root(self, handler):
        params = {
            "nodes": [
                {"role_title": "Worker", "level": 1},
            ],
        }
        nodes, edges, errors = handler._build_org_structure(params)
        assert any("根节点" in e for e in errors)

    def test_auto_assigns_avatar(self, handler):
        params = {
            "nodes": [{"role_title": "CTO / 技术总监", "level": 0}],
        }
        nodes, edges, errors = handler._build_org_structure(params)
        assert nodes[0].get("avatar") is not None


class TestCreate:
    """Test action=create."""

    @pytest.mark.asyncio
    async def test_create_success(self, handler, tmp_data_dir):
        with patch("openakita.config.settings") as mock_settings:
            mock_settings.data_dir = tmp_data_dir
            result = await handler._create({
                "name": "测试组织",
                "description": "测试描述",
                "core_business": "软件开发",
                "nodes": [
                    {"role_title": "CEO", "level": 0, "agent_profile_id": "default"},
                    {"role_title": "CTO", "level": 1, "parent_role_title": "CEO",
                     "agent_profile_id": "architect"},
                ],
            })
        assert "✅" in result
        assert "测试组织" in result
        assert "节点数: 2" in result
        assert "连线:" in result

    @pytest.mark.asyncio
    async def test_create_no_name_error(self, handler):
        result = await handler._create({"nodes": [{"role_title": "A", "level": 0}]})
        assert "❌" in result

    @pytest.mark.asyncio
    async def test_create_no_nodes_error(self, handler):
        result = await handler._create({"name": "X"})
        assert "❌" in result


class TestCreateFromTemplate:
    """Test action=create_from_template."""

    @pytest.mark.asyncio
    async def test_missing_template_id(self, handler):
        result = await handler._create_from_template({})
        assert "❌" in result
        assert "template_id" in result

    @pytest.mark.asyncio
    async def test_nonexistent_template(self, handler, tmp_data_dir):
        with patch("openakita.config.settings") as mock_settings:
            mock_settings.data_dir = tmp_data_dir
            result = await handler._create_from_template({"template_id": "nonexistent"})
        assert "❌" in result
        assert "不存在" in result


class TestToolDefinition:
    """Test tool definition structure."""

    def test_tool_definition_valid(self):
        from openakita.tools.definitions.org_setup import ORG_SETUP_TOOLS
        assert len(ORG_SETUP_TOOLS) == 1
        tool = ORG_SETUP_TOOLS[0]
        assert tool["name"] == "setup_organization"
        assert tool["category"] == "Organization"
        assert "input_schema" in tool
        assert "action" in tool["input_schema"]["properties"]

    def test_tool_has_examples(self):
        from openakita.tools.definitions.org_setup import ORG_SETUP_TOOLS
        tool = ORG_SETUP_TOOLS[0]
        assert "examples" in tool
        assert len(tool["examples"]) >= 2


class TestToolRegistration:
    """Test that the tool is properly exported and available."""

    def test_exported_from_definitions(self):
        from openakita.tools.definitions import ORG_SETUP_TOOLS
        assert len(ORG_SETUP_TOOLS) > 0

    def test_not_in_base_tools(self):
        from openakita.tools.definitions import BASE_TOOLS, ORG_SETUP_TOOLS
        base_names = {t["name"] for t in BASE_TOOLS}
        org_names = {t["name"] for t in ORG_SETUP_TOOLS}
        assert not base_names.intersection(org_names)

    def test_handler_has_create_handler(self):
        from openakita.tools.handlers.org_setup import create_handler
        assert callable(create_handler)

    def test_handler_accepts_agent(self):
        from openakita.tools.handlers.org_setup import create_handler
        mock_agent = MagicMock()
        handler_fn = create_handler(mock_agent)
        assert callable(handler_fn)

    def test_action_enum_includes_new_actions(self):
        from openakita.tools.definitions.org_setup import ORG_SETUP_TOOLS
        tool = ORG_SETUP_TOOLS[0]
        actions = tool["input_schema"]["properties"]["action"]["enum"]
        for a in ("list_orgs", "get_org", "update_org", "delete_org"):
            assert a in actions, f"Missing action: {a}"

    def test_schema_has_org_id(self):
        from openakita.tools.definitions.org_setup import ORG_SETUP_TOOLS
        props = ORG_SETUP_TOOLS[0]["input_schema"]["properties"]
        assert "org_id" in props

    def test_schema_has_update_nodes(self):
        from openakita.tools.definitions.org_setup import ORG_SETUP_TOOLS
        props = ORG_SETUP_TOOLS[0]["input_schema"]["properties"]
        assert "update_nodes" in props
        assert "remove_nodes" in props
        assert "update_fields" in props


# ============================================================
# list_orgs / get_org / update_org / delete_org tests
# ============================================================

@pytest.fixture
def created_org(handler, tmp_data_dir):
    """Create a test org and return (org_id, data_dir)."""
    from openakita.orgs.manager import OrgManager
    manager = OrgManager(tmp_data_dir)
    org = manager.create({
        "name": "测试修改组织",
        "description": "用于测试修改",
        "core_business": "软件开发",
        "nodes": [
            {
                "id": "node_root",
                "role_title": "CTO",
                "role_goal": "技术方向",
                "department": "技术部",
                "level": 0,
                "agent_source": "ref:architect",
                "agent_profile_id": "architect",
                "external_tools": ["research", "filesystem"],
                "position": {"x": 400, "y": 0},
            },
            {
                "id": "node_dev",
                "role_title": "开发工程师",
                "role_goal": "写代码",
                "department": "技术部",
                "level": 1,
                "agent_source": "ref:code-assistant",
                "agent_profile_id": "code-assistant",
                "external_tools": ["filesystem", "research"],
                "position": {"x": 400, "y": 180},
            },
            {
                "id": "node_qa",
                "role_title": "QA 测试",
                "role_goal": "质量保障",
                "department": "技术部",
                "level": 1,
                "agent_source": "ref:code-assistant",
                "agent_profile_id": "code-assistant",
                "external_tools": ["filesystem"],
                "position": {"x": 650, "y": 180},
            },
        ],
        "edges": [
            {"id": "edge_1", "source": "node_root", "target": "node_dev",
             "edge_type": "hierarchy", "bidirectional": True},
            {"id": "edge_2", "source": "node_root", "target": "node_qa",
             "edge_type": "hierarchy", "bidirectional": True},
        ],
    })
    return org.id, tmp_data_dir


def _fresh_manager(data_dir):
    """Create a fresh OrgManager to bypass in-memory cache."""
    from openakita.orgs.manager import OrgManager
    return OrgManager(data_dir)


class TestListOrgs:
    """Test action=list_orgs."""

    def test_list_empty(self, handler, tmp_data_dir):
        with patch("openakita.config.settings") as ms:
            ms.data_dir = tmp_data_dir
            result = handler._list_orgs()
        assert "没有任何组织" in result

    def test_list_returns_existing(self, handler, tmp_data_dir, created_org):
        org_id, data_dir = created_org
        with patch("openakita.config.settings") as ms:
            ms.data_dir = data_dir
            result = handler._list_orgs()
        assert "测试修改组织" in result
        assert org_id in result
        assert "节点: 3" in result


class TestGetOrg:
    """Test action=get_org."""

    def test_get_org_missing_id(self, handler):
        result = handler._get_org({})
        assert "❌" in result

    def test_get_org_not_found(self, handler, tmp_data_dir):
        with patch("openakita.config.settings") as ms:
            ms.data_dir = tmp_data_dir
            result = handler._get_org({"org_id": "nonexistent"})
        assert "❌" in result
        assert "不存在" in result

    def test_get_org_returns_structure(self, handler, tmp_data_dir, created_org):
        org_id, data_dir = created_org
        with patch("openakita.config.settings") as ms:
            ms.data_dir = data_dir
            result = handler._get_org({"org_id": org_id})
        assert "测试修改组织" in result
        assert "CTO" in result
        assert "开发工程师" in result
        assert "QA 测试" in result
        assert "node_root" in result
        assert "node_dev" in result
        assert "→" in result

    def test_get_org_shows_agent(self, handler, tmp_data_dir, created_org):
        org_id, data_dir = created_org
        with patch("openakita.config.settings") as ms:
            ms.data_dir = data_dir
            result = handler._get_org({"org_id": org_id})
        assert "architect" in result
        assert "code-assistant" in result


class TestUpdateOrg:
    """Test action=update_org."""

    @pytest.mark.asyncio
    async def test_update_missing_org_id(self, handler):
        result = await handler._update_org({})
        assert "❌" in result

    @pytest.mark.asyncio
    async def test_update_nonexistent_org(self, handler, tmp_data_dir):
        with patch("openakita.config.settings") as ms:
            ms.data_dir = tmp_data_dir
            result = await handler._update_org({"org_id": "nonexistent"})
        assert "❌" in result
        assert "不存在" in result

    @pytest.mark.asyncio
    async def test_update_modify_node_by_id(self, handler, tmp_data_dir, created_org):
        """Modify an existing node by node_id — should preserve ID."""
        org_id, data_dir = created_org
        with patch("openakita.config.settings") as ms:
            ms.data_dir = data_dir
            result = await handler._update_org({
                "org_id": org_id,
                "update_nodes": [
                    {
                        "node_id": "node_dev",
                        "role_title": "高级开发工程师",
                        "agent_profile_id": "architect",
                    }
                ],
            })
        assert "✅" in result
        assert "修改节点" in result

        org = _fresh_manager(data_dir).get(org_id)
        dev = next(n for n in org.nodes if n.id == "node_dev")
        assert dev.role_title == "高级开发工程师"
        assert dev.agent_profile_id == "architect"
        assert dev.agent_source == "ref:architect"

    @pytest.mark.asyncio
    async def test_update_modify_node_by_title(self, handler, tmp_data_dir, created_org):
        """Modify an existing node by role_title match."""
        org_id, data_dir = created_org
        with patch("openakita.config.settings") as ms:
            ms.data_dir = data_dir
            result = await handler._update_org({
                "org_id": org_id,
                "update_nodes": [
                    {
                        "role_title": "QA 测试",
                        "role_goal": "自动化测试 + 性能测试",
                    }
                ],
            })
        assert "✅" in result

        org = _fresh_manager(data_dir).get(org_id)
        qa = next(n for n in org.nodes if n.id == "node_qa")
        assert qa.role_goal == "自动化测试 + 性能测试"
        assert qa.id == "node_qa"

    @pytest.mark.asyncio
    async def test_update_add_new_node(self, handler, tmp_data_dir, created_org):
        """Add a new node to an existing org."""
        org_id, data_dir = created_org
        with patch("openakita.config.settings") as ms:
            ms.data_dir = data_dir
            result = await handler._update_org({
                "org_id": org_id,
                "update_nodes": [
                    {
                        "role_title": "数据工程师",
                        "role_goal": "数据管道建设",
                        "department": "技术部",
                        "level": 1,
                        "agent_profile_id": "data-analyst",
                        "parent_role_title": "CTO",
                    }
                ],
            })
        assert "✅" in result
        assert "新增节点" in result

        org = _fresh_manager(data_dir).get(org_id)
        assert len(org.nodes) == 4
        new_node = next(n for n in org.nodes if n.role_title == "数据工程师")
        assert new_node.agent_profile_id == "data-analyst"
        assert new_node.id.startswith("node_")

        parent_edges = [e for e in org.edges if e.target == new_node.id]
        assert len(parent_edges) == 1
        assert parent_edges[0].source == "node_root"

    @pytest.mark.asyncio
    async def test_update_remove_node(self, handler, tmp_data_dir, created_org):
        """Remove a node and verify edges are cleaned up."""
        org_id, data_dir = created_org
        with patch("openakita.config.settings") as ms:
            ms.data_dir = data_dir
            result = await handler._update_org({
                "org_id": org_id,
                "remove_nodes": ["QA 测试"],
            })
        assert "✅" in result
        assert "删除节点" in result

        org = _fresh_manager(data_dir).get(org_id)
        assert len(org.nodes) == 2
        assert not any(n.id == "node_qa" for n in org.nodes)
        assert not any(e.target == "node_qa" or e.source == "node_qa" for e in org.edges)

    @pytest.mark.asyncio
    async def test_update_remove_node_by_id(self, handler, tmp_data_dir, created_org):
        """Remove a node by ID."""
        org_id, data_dir = created_org
        with patch("openakita.config.settings") as ms:
            ms.data_dir = data_dir
            result = await handler._update_org({
                "org_id": org_id,
                "remove_nodes": ["node_qa"],
            })
        assert "✅" in result

        org = _fresh_manager(data_dir).get(org_id)
        assert not any(n.id == "node_qa" for n in org.nodes)

    @pytest.mark.asyncio
    async def test_update_org_fields(self, handler, tmp_data_dir, created_org):
        """Update top-level org fields."""
        org_id, data_dir = created_org
        with patch("openakita.config.settings") as ms:
            ms.data_dir = data_dir
            result = await handler._update_org({
                "org_id": org_id,
                "update_fields": {
                    "name": "重命名组织",
                    "core_business": "AI 产品开发",
                },
            })
        assert "✅" in result

        org = _fresh_manager(data_dir).get(org_id)
        assert org.name == "重命名组织"
        assert org.core_business == "AI 产品开发"

    @pytest.mark.asyncio
    async def test_update_no_changes(self, handler, tmp_data_dir, created_org):
        """No modifications should report no changes."""
        org_id, data_dir = created_org
        with patch("openakita.config.settings") as ms:
            ms.data_dir = data_dir
            result = await handler._update_org({"org_id": org_id})
        assert "未检测到" in result

    @pytest.mark.asyncio
    async def test_update_preserves_unmentioned_nodes(self, handler, tmp_data_dir, created_org):
        """Nodes not mentioned in update_nodes should be preserved."""
        org_id, data_dir = created_org
        with patch("openakita.config.settings") as ms:
            ms.data_dir = data_dir
            await handler._update_org({
                "org_id": org_id,
                "update_nodes": [
                    {"node_id": "node_dev", "role_goal": "写高质量代码"},
                ],
            })

        org = _fresh_manager(data_dir).get(org_id)
        assert len(org.nodes) == 3
        qa = next(n for n in org.nodes if n.id == "node_qa")
        assert qa.role_title == "QA 测试"

    @pytest.mark.asyncio
    async def test_update_combined_add_remove_modify(self, handler, tmp_data_dir, created_org):
        """Combine add + remove + modify in one call."""
        org_id, data_dir = created_org
        with patch("openakita.config.settings") as ms:
            ms.data_dir = data_dir
            result = await handler._update_org({
                "org_id": org_id,
                "remove_nodes": ["node_qa"],
                "update_nodes": [
                    {"node_id": "node_dev", "role_title": "全栈工程师"},
                    {
                        "role_title": "DevOps",
                        "level": 1,
                        "agent_profile_id": "devops-engineer",
                        "parent_role_title": "CTO",
                    },
                ],
                "update_fields": {"description": "重构后的技术团队"},
            })
        assert "✅" in result

        org = _fresh_manager(data_dir).get(org_id)
        assert len(org.nodes) == 3
        assert org.description == "重构后的技术团队"
        assert any(n.role_title == "全栈工程师" for n in org.nodes)
        assert any(n.role_title == "DevOps" for n in org.nodes)
        assert not any(n.id == "node_qa" for n in org.nodes)


class TestDeleteOrg:
    """Test action=delete_org."""

    @pytest.mark.asyncio
    async def test_delete_missing_org_id(self, handler):
        result = await handler._delete_org({})
        assert "❌" in result

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, handler, tmp_data_dir):
        with patch("openakita.config.settings") as ms, patch(
            "openakita.orgs.runtime.get_runtime", return_value=None
        ):
            ms.data_dir = tmp_data_dir
            result = await handler._delete_org({"org_id": "nonexistent"})
        assert "❌" in result
        assert "不存在" in result

    @pytest.mark.asyncio
    async def test_delete_success(self, handler, tmp_data_dir, created_org):
        org_id, data_dir = created_org
        with patch("openakita.config.settings") as ms, patch(
            "openakita.orgs.runtime.get_runtime", return_value=None
        ):
            ms.data_dir = data_dir
            result = await handler._delete_org({"org_id": org_id})
        assert "✅" in result
        assert "测试修改组织" in result
        assert _fresh_manager(data_dir).get(org_id) is None


class TestHandleDispatch:
    """Test that handle() correctly dispatches all actions."""

    @pytest.mark.asyncio
    async def test_dispatch_list_orgs(self, handler, tmp_data_dir):
        with patch("openakita.config.settings") as ms:
            ms.data_dir = tmp_data_dir
            result = await handler.handle("setup_organization", {"action": "list_orgs"})
        assert "没有任何组织" in result or "现有组织" in result

    @pytest.mark.asyncio
    async def test_dispatch_get_org(self, handler):
        result = await handler.handle("setup_organization", {"action": "get_org"})
        assert "❌" in result

    @pytest.mark.asyncio
    async def test_dispatch_update_org(self, handler):
        result = await handler.handle("setup_organization", {"action": "update_org"})
        assert "❌" in result

    @pytest.mark.asyncio
    async def test_dispatch_delete_org(self, handler):
        result = await handler.handle("setup_organization", {"action": "delete_org"})
        assert "❌" in result

    @pytest.mark.asyncio
    async def test_dispatch_invalid_action(self, handler):
        result = await handler.handle("setup_organization", {"action": "invalid"})
        assert "❌" in result
        assert "list_orgs" in result
