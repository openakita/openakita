"""C10: MCP ``tool.annotations`` → ApprovalClass lookup（``MCPClient.get_tool_class``）.

测试维度：
- D1：MCPTool 接受 annotations 字段
- D2：``annotations.approval_class`` / ``annotations.risk_class`` 显式声明
- D3：``destructiveHint=True`` → DESTRUCTIVE
- D4：``openWorldHint=True`` + ``readOnlyHint!=True`` → MUTATING_GLOBAL
- D5：``readOnlyHint=True`` → READONLY_SCOPED
- D6：tool name 必须经过 ``_format_tool_name`` 归一（``mcp_<server>_<tool>``）
- D7：未声明 annotations → 返回 None
- D8：非法 approval_class 字符串 → 回退到 hint 模式
"""

from __future__ import annotations

import pytest

from openakita.core.policy_v2.enums import ApprovalClass, DecisionSource
from openakita.tools.mcp import MCPClient, MCPTool


@pytest.fixture
def client_with_tools():
    client = MCPClient()

    def add(server: str, tool_name: str, **annotations):
        client._tools[f"{server}:{tool_name}"] = MCPTool(
            name=tool_name,
            description="t",
            input_schema={},
            annotations=dict(annotations),
        )

    yield client, add


class TestExplicitAnnotation:
    def test_approval_class_field(self, client_with_tools):
        client, add = client_with_tools
        add("srv", "ddl", approval_class="destructive")
        result = client.get_tool_class("mcp_srv_ddl")
        assert result == (ApprovalClass.DESTRUCTIVE, DecisionSource.MCP_ANNOTATION)

    def test_risk_class_alias(self, client_with_tools):
        client, add = client_with_tools
        add("srv", "ddl", risk_class="mutating_global")
        result = client.get_tool_class("mcp_srv_ddl")
        assert result == (
            ApprovalClass.MUTATING_GLOBAL,
            DecisionSource.MCP_ANNOTATION,
        )

    def test_hyphen_in_tool_name_normalized(self, client_with_tools):
        client, add = client_with_tools
        add("srv", "delete-row", approval_class="destructive")
        # _format_tool_name converts hyphens → underscores
        assert client.get_tool_class("mcp_srv_delete_row") is not None

    def test_invalid_explicit_falls_back_to_hint(self, client_with_tools, caplog):
        client, add = client_with_tools
        add(
            "srv",
            "weird",
            approval_class="not_a_class",
            destructiveHint=True,
        )
        with caplog.at_level("WARNING", logger="openakita.tools.mcp"):
            result = client.get_tool_class("mcp_srv_weird")
        assert result == (ApprovalClass.DESTRUCTIVE, DecisionSource.MCP_ANNOTATION)
        assert any(
            "unknown approval_class" in rec.message for rec in caplog.records
        )


class TestHintInference:
    def test_destructive_hint(self, client_with_tools):
        client, add = client_with_tools
        add("srv", "rm", destructiveHint=True)
        result = client.get_tool_class("mcp_srv_rm")
        assert result == (ApprovalClass.DESTRUCTIVE, DecisionSource.MCP_ANNOTATION)

    def test_open_world_hint_mutating_global(self, client_with_tools):
        client, add = client_with_tools
        add("srv", "post", openWorldHint=True, readOnlyHint=False)
        result = client.get_tool_class("mcp_srv_post")
        assert result == (
            ApprovalClass.MUTATING_GLOBAL,
            DecisionSource.MCP_ANNOTATION,
        )

    def test_read_only_hint(self, client_with_tools):
        client, add = client_with_tools
        add("srv", "list", readOnlyHint=True)
        result = client.get_tool_class("mcp_srv_list")
        assert result == (
            ApprovalClass.READONLY_SCOPED,
            DecisionSource.MCP_ANNOTATION,
        )

    def test_destructive_takes_priority_over_other_hints(self, client_with_tools):
        client, add = client_with_tools
        add(
            "srv",
            "drop",
            destructiveHint=True,
            openWorldHint=True,
            readOnlyHint=False,
        )
        # destructive checked first; openWorldHint also fires; most_strict wins
        result = client.get_tool_class("mcp_srv_drop")
        assert result is not None
        klass, _ = result
        assert klass == ApprovalClass.DESTRUCTIVE


class TestFallback:
    def test_no_annotations_returns_none(self, client_with_tools):
        client, add = client_with_tools
        add("srv", "untagged")
        assert client.get_tool_class("mcp_srv_untagged") is None

    def test_unknown_tool_returns_none(self):
        client = MCPClient()
        assert client.get_tool_class("mcp_anything_else") is None

    def test_format_tool_name_consistent_with_get_tool_schemas(
        self, client_with_tools
    ):
        client, add = client_with_tools
        add("srv-with-dash", "tool-x", readOnlyHint=True)
        schemas = client.get_tool_schemas()
        # The tool name surfaced to the LLM by ``get_tool_schemas`` MUST be the
        # same key used by ``get_tool_class`` — drift here was the C7 bug.
        assert any(
            client.get_tool_class(s["name"])
            == (ApprovalClass.READONLY_SCOPED, DecisionSource.MCP_ANNOTATION)
            for s in schemas
        )
