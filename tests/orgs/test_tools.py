"""Tests for tools.py — validate ORG_NODE_TOOLS constant and handler correspondence."""

from __future__ import annotations

import pytest

from openakita.orgs.models import EdgeType, OrgEdge, OrgNode, Organization
from openakita.orgs.tool_handler import OrgToolHandler
from openakita.orgs.tools import ORG_NODE_TOOLS, build_org_node_tools


class TestOrgNodeToolsConstant:
    def test_is_list(self):
        assert isinstance(ORG_NODE_TOOLS, list)
        assert len(ORG_NODE_TOOLS) > 0

    def test_each_tool_has_required_fields(self):
        for tool in ORG_NODE_TOOLS:
            assert "name" in tool, f"Tool missing 'name': {tool}"
            assert "description" in tool, f"Tool {tool.get('name')} missing 'description'"
            assert "input_schema" in tool, f"Tool {tool['name']} missing 'input_schema'"

    def test_tool_names_are_unique(self):
        names = [t["name"] for t in ORG_NODE_TOOLS]
        assert len(names) == len(set(names)), f"Duplicate tool names: {[n for n in names if names.count(n) > 1]}"

    def test_all_tool_names_start_with_org(self):
        for tool in ORG_NODE_TOOLS:
            assert tool["name"].startswith("org_"), f"Tool name should start with 'org_': {tool['name']}"

    def test_parameters_are_valid_json_schema(self):
        for tool in ORG_NODE_TOOLS:
            params = tool["input_schema"]
            assert params.get("type") == "object", f"{tool['name']}: params type should be 'object'"
            assert "properties" in params, f"{tool['name']}: missing 'properties'"

    def test_required_fields_exist_in_properties(self):
        for tool in ORG_NODE_TOOLS:
            params = tool["input_schema"]
            props = params.get("properties", {})
            required = params.get("required", [])
            for req_field in required:
                assert req_field in props, (
                    f"{tool['name']}: required field '{req_field}' not in properties"
                )


class TestToolHandlerCorrespondence:
    """Verify every tool defined in ORG_NODE_TOOLS has a handler method."""

    def test_all_tools_have_handlers(self):
        handler_cls = OrgToolHandler
        tool_names = {t["name"] for t in ORG_NODE_TOOLS}

        for name in tool_names:
            method_name = f"_handle_{name}"
            assert hasattr(handler_cls, method_name), (
                f"Tool '{name}' has no handler method '{method_name}' in OrgToolHandler"
            )

    def test_handler_methods_are_callable(self):
        handler_cls = OrgToolHandler
        for tool in ORG_NODE_TOOLS:
            method_name = f"_handle_{tool['name']}"
            method = getattr(handler_cls, method_name, None)
            assert callable(method), f"Handler '{method_name}' is not callable"


def _make_three_level_org() -> Organization:
    """Build a minimal 3-level org: ceo -> cpo -> {pm, ui}."""
    org = Organization(id="org_test", name="Test")
    org.nodes = [
        OrgNode(id="ceo", role_title="CEO"),
        OrgNode(id="cpo", role_title="产品总监"),
        OrgNode(id="pm", role_title="产品经理"),
        OrgNode(id="ui", role_title="UI设计师"),
    ]
    org.edges = [
        OrgEdge(source="ceo", target="cpo", edge_type=EdgeType.HIERARCHY),
        OrgEdge(source="cpo", target="pm", edge_type=EdgeType.HIERARCHY),
        OrgEdge(source="cpo", target="ui", edge_type=EdgeType.HIERARCHY),
    ]
    return org


class TestBuildOrgNodeTools:
    """Per-node customization of ORG_NODE_TOOLS via build_org_node_tools()."""

    def test_enum_restricts_delegate_to_direct_children(self):
        org = _make_three_level_org()
        cpo = org.get_node("cpo")

        tools = build_org_node_tools(org, cpo)
        by_name = {t["name"]: t for t in tools}

        assert "org_delegate_task" in by_name, "cpo has children, should keep the tool"
        to_node = by_name["org_delegate_task"]["input_schema"]["properties"]["to_node"]
        assert "enum" in to_node
        assert set(to_node["enum"]) == {"pm", "ui"}, (
            f"CPO's to_node enum should be its direct children only, got {to_node['enum']}"
        )
        assert "cpo" not in to_node["enum"], "CPO must not be able to delegate to itself"
        assert "ceo" not in to_node["enum"], "CPO must not delegate upward"

    def test_leaf_node_has_no_delegate_tool(self):
        org = _make_three_level_org()
        pm = org.get_node("pm")

        tools = build_org_node_tools(org, pm)
        names = {t["name"] for t in tools}

        assert "org_delegate_task" not in names, (
            "Leaf node must not expose org_delegate_task (no possible target)"
        )
        assert "org_submit_deliverable" in names, (
            "Leaf node must still have org_submit_deliverable to hand results upward"
        )

    def test_does_not_mutate_static_template(self):
        """Calling build_org_node_tools twice must not pollute ORG_NODE_TOOLS."""
        org = _make_three_level_org()
        ceo = org.get_node("ceo")

        template = next(
            t for t in ORG_NODE_TOOLS if t["name"] == "org_delegate_task"
        )
        original_to_node = dict(template["input_schema"]["properties"]["to_node"])
        assert "enum" not in original_to_node, "Sanity: template must not start with enum"

        _ = build_org_node_tools(org, ceo)
        _ = build_org_node_tools(org, ceo)

        template_after = next(
            t for t in ORG_NODE_TOOLS if t["name"] == "org_delegate_task"
        )
        to_node_after = template_after["input_schema"]["properties"]["to_node"]
        assert "enum" not in to_node_after, (
            "ORG_NODE_TOOLS template must remain untouched after per-node builds"
        )
        assert to_node_after["description"] == original_to_node["description"], (
            "ORG_NODE_TOOLS template description must remain untouched"
        )

    def test_single_child_description_hint(self):
        """The enum hint is appended to the description for LLM guidance."""
        org = _make_three_level_org()
        ceo = org.get_node("ceo")

        tools = build_org_node_tools(org, ceo)
        by_name = {t["name"]: t for t in tools}

        to_node = by_name["org_delegate_task"]["input_schema"]["properties"]["to_node"]
        assert to_node["enum"] == ["cpo"]
        assert "`cpo`" in to_node["description"], (
            f"Description should mention the allowed id, got: {to_node['description']}"
        )
