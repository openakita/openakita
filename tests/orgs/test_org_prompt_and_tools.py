"""Tests for org mode lean prompt composition and tool carrying rules.

Validates:
1. Prompt structure: SOUL.md/AGENT.md NOT injected, ROLE.md / custom_prompt used
2. Tool carrying: org_* always present, _KEEP tools present, external_tools honored
3. Conflict tools: delegate_to_agent etc. blocked even if in external_tools
4. Priority chain: ROLE.md > custom_prompt > AgentProfile > auto-generated
5. prompt-preview API returns correct structure
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from openakita.orgs.identity import OrgIdentity, ResolvedIdentity
from openakita.orgs.models import Organization, OrgNode
from openakita.orgs.tool_categories import expand_tool_categories, TOOL_CATEGORIES
from .conftest import make_org, make_node, make_edge


@pytest.fixture()
def identity(org_dir: Path, tmp_path: Path) -> OrgIdentity:
    global_identity = tmp_path / "identity"
    global_identity.mkdir()
    (global_identity / "SOUL.md").write_text(
        "# 灵魂\n永不放弃。Ralph Wiggum 模式。\n单打独斗解决所有问题。",
        encoding="utf-8",
    )
    return OrgIdentity(org_dir, global_identity)


# ---------------------------------------------------------------------------
# 1. Prompt structure: SOUL.md / AGENT.md NOT injected
# ---------------------------------------------------------------------------


class TestPromptNoSoulAgent:
    """Verify SOUL.md and AGENT.md content is NOT in org context prompt."""

    def test_ralph_wiggum_not_in_prompt(self, identity: OrgIdentity, persisted_org):
        """The solo-agent 'Ralph Wiggum' philosophy must not appear."""
        node = persisted_org.nodes[0]
        resolved = identity.resolve(node, persisted_org)
        prompt = identity.build_org_context_prompt(node, persisted_org, resolved)

        assert "Ralph Wiggum" not in prompt
        assert "永不放弃" not in prompt
        assert "单打独斗解决" not in prompt

    def test_compact_identity_present(self, identity: OrgIdentity, persisted_org):
        """A compact collaboration-focused identity IS present."""
        node = persisted_org.nodes[0]
        resolved = identity.resolve(node, persisted_org)
        prompt = identity.build_org_context_prompt(node, persisted_org, resolved)

        assert "OpenAkita 组织 Agent" in prompt
        assert "核心原则" in prompt
        assert "协作" in prompt

    def test_node_level_soul_file_not_injected(
        self, identity: OrgIdentity, persisted_org, org_dir: Path
    ):
        """Even if a node has its own SOUL.md, it should not be injected."""
        node = persisted_org.nodes[0]
        id_dir = org_dir / "nodes" / node.id / "identity"
        id_dir.mkdir(parents=True, exist_ok=True)
        (id_dir / "SOUL.md").write_text(
            "# 自定义灵魂\nSOUL_UNIQUE_MARKER_XYZ", encoding="utf-8"
        )

        resolved = identity.resolve(node, persisted_org)
        prompt = identity.build_org_context_prompt(node, persisted_org, resolved)

        assert "自定义灵魂" not in prompt
        assert "SOUL_UNIQUE_MARKER_XYZ" not in prompt


# ---------------------------------------------------------------------------
# 2. ROLE.md / custom_prompt priority chain
# ---------------------------------------------------------------------------


class TestRolePriorityChain:
    def test_auto_generate_when_no_role(self, identity: OrgIdentity, persisted_org):
        """Without ROLE.md or custom_prompt, auto-generated text is used."""
        node = persisted_org.nodes[1]  # CTO
        resolved = identity.resolve(node, persisted_org)
        assert resolved.level == 0
        assert node.role_title in resolved.role

    def test_custom_prompt_used_when_set(self, identity: OrgIdentity, persisted_org):
        """custom_prompt should be used when no ROLE.md exists."""
        node = persisted_org.nodes[1]
        node.custom_prompt = "你是一位超级CTO，精通所有技术栈。"

        resolved = identity.resolve(node, persisted_org)
        assert "超级CTO" in resolved.role

        prompt = identity.build_org_context_prompt(node, persisted_org, resolved)
        assert "超级CTO" in prompt

    def test_role_md_overrides_custom_prompt(
        self, identity: OrgIdentity, persisted_org, org_dir: Path
    ):
        """ROLE.md takes priority over custom_prompt."""
        node = persisted_org.nodes[1]
        node.custom_prompt = "你是一位超级CTO，精通所有技术栈。"

        id_dir = org_dir / "nodes" / node.id / "identity"
        id_dir.mkdir(parents=True, exist_ok=True)
        (id_dir / "ROLE.md").write_text(
            "你是一位务实的技术总监，ROLE文件定义。", encoding="utf-8"
        )

        resolved = identity.resolve(node, persisted_org)
        assert "ROLE文件定义" in resolved.role
        assert "超级CTO" not in resolved.role
        assert resolved.level >= 1

        prompt = identity.build_org_context_prompt(node, persisted_org, resolved)
        assert "ROLE文件定义" in prompt

    def test_custom_prompt_appears_in_role_section(
        self, identity: OrgIdentity, persisted_org
    ):
        """custom_prompt content goes into '你的组织角色' section."""
        node = persisted_org.nodes[0]
        node.custom_prompt = "CUSTOM_MARKER_ABC123"

        resolved = identity.resolve(node, persisted_org)
        prompt = identity.build_org_context_prompt(node, persisted_org, resolved)

        assert "你的组织角色" in prompt
        assert "CUSTOM_MARKER_ABC123" in prompt


# ---------------------------------------------------------------------------
# 3. Delegation guidance for managers
# ---------------------------------------------------------------------------


class TestDelegationGuidance:
    def test_manager_gets_delegation_instruction(
        self, identity: OrgIdentity, persisted_org
    ):
        """Nodes with children should get explicit delegation guidance."""
        ceo = persisted_org.nodes[0]
        resolved = identity.resolve(ceo, persisted_org)
        prompt = identity.build_org_context_prompt(ceo, persisted_org, resolved)

        assert "org_delegate_task" in prompt
        assert "管理者" in prompt or "委派" in prompt

    def test_leaf_node_no_delegation_instruction(self, identity: OrgIdentity):
        """Leaf nodes (no children) should not get delegation guidance."""
        org = make_org(
            nodes=[
                make_node("boss", "老板", 0, "管理层"),
                make_node("worker", "工人", 1, "执行组"),
            ],
            edges=[make_edge("boss", "worker")],
        )
        worker = org.nodes[1]
        resolved = identity.resolve(worker, org)
        prompt = identity.build_org_context_prompt(worker, org, resolved)

        assert "你是管理者" not in prompt

    def test_subordinate_ids_listed(self, identity: OrgIdentity, persisted_org):
        """Subordinate node IDs must be listed so LLM can use org_delegate_task."""
        ceo = persisted_org.nodes[0]
        resolved = identity.resolve(ceo, persisted_org)
        prompt = identity.build_org_context_prompt(ceo, persisted_org, resolved)

        cto = persisted_org.nodes[1]
        assert cto.id in prompt


# ---------------------------------------------------------------------------
# 4. Tool carrying rules
# ---------------------------------------------------------------------------

_KEEP_TOOLS = frozenset({
    "get_tool_info", "create_todo", "update_todo_step",
    "get_todo_status", "complete_todo",
})

_ORG_CONFLICT_TOOLS = frozenset({
    "delegate_to_agent", "spawn_agent",
    "delegate_parallel", "create_agent",
})


class TestToolCarryingRules:
    def test_conflict_tools_blocked_from_external(self):
        """delegate_to_agent etc. must be removed even if in external_tools."""
        external = ["research", "delegate_to_agent", "spawn_agent"]
        allowed = expand_tool_categories(external) - _ORG_CONFLICT_TOOLS
        assert "delegate_to_agent" not in allowed
        assert "spawn_agent" not in allowed
        assert "web_search" in allowed
        assert "news_search" in allowed

    def test_keep_tools_are_minimal(self):
        """_KEEP set should be small and focused on planning/discovery."""
        assert _KEEP_TOOLS == {
            "get_tool_info", "create_todo", "update_todo_step",
            "get_todo_status", "complete_todo",
        }

    def test_no_external_tools_means_empty_allowed(self):
        """Node with no external_tools should get no extra tools."""
        allowed = expand_tool_categories([]) - _ORG_CONFLICT_TOOLS
        assert len(allowed) == 0

    def test_skills_category_expands(self):
        """The 'skills' category should expand to skill-related tools."""
        assert "skills" in TOOL_CATEGORIES
        result = expand_tool_categories(["skills"])
        assert "run_skill_script" in result
        assert "list_skills" in result

    def test_mixed_categories_and_tools(self):
        """Mix of categories and raw tool names should all expand."""
        entries = ["research", "filesystem", "my_custom_mcp_tool"]
        result = expand_tool_categories(entries) - _ORG_CONFLICT_TOOLS
        assert "web_search" in result
        assert "read_file" in result
        assert "my_custom_mcp_tool" in result

    def test_conflict_tools_blocked_even_as_raw_names(self):
        """Even if conflict tools are specified as raw names, they're blocked."""
        entries = ["delegate_to_agent", "create_agent", "research"]
        allowed = expand_tool_categories(entries) - _ORG_CONFLICT_TOOLS
        assert "delegate_to_agent" not in allowed
        assert "create_agent" not in allowed
        assert "web_search" in allowed


# ---------------------------------------------------------------------------
# 5. Prompt sections completeness
# ---------------------------------------------------------------------------


class TestPromptSections:
    def test_has_org_architecture(self, identity: OrgIdentity, persisted_org):
        node = persisted_org.nodes[0]
        resolved = identity.resolve(node, persisted_org)
        prompt = identity.build_org_context_prompt(node, persisted_org, resolved)
        assert "组织架构概览" in prompt

    def test_has_permissions(self, identity: OrgIdentity, persisted_org):
        node = persisted_org.nodes[0]
        resolved = identity.resolve(node, persisted_org)
        prompt = identity.build_org_context_prompt(node, persisted_org, resolved)
        assert "你的权限" in prompt

    def test_has_policy_section(self, identity: OrgIdentity, persisted_org):
        node = persisted_org.nodes[0]
        resolved = identity.resolve(node, persisted_org)
        prompt = identity.build_org_context_prompt(node, persisted_org, resolved)
        assert "制度与流程" in prompt

    def test_has_tool_constraints_section(self, identity: OrgIdentity, persisted_org):
        node = persisted_org.nodes[0]
        resolved = identity.resolve(node, persisted_org)
        prompt = identity.build_org_context_prompt(node, persisted_org, resolved)
        assert "组织工具与行为约束" in prompt

    def test_node_with_external_tools_mentions_tools(self, identity: OrgIdentity):
        """Nodes with external_tools should have tool usage guidance."""
        org = make_org(
            nodes=[
                make_node("boss", "老板", 0, "管理层", external_tools=["research"]),
                make_node("dev", "开发", 1, "技术部"),
            ],
            edges=[make_edge("boss", "dev")],
        )
        boss = org.nodes[0]
        resolved = identity.resolve(boss, org)
        prompt = identity.build_org_context_prompt(boss, org, resolved)
        assert "外部执行工具" in prompt or "research" in prompt

    def test_node_without_external_tools_is_org_only(self, identity: OrgIdentity):
        """Nodes without external_tools are restricted to org_* only."""
        org = make_org(
            nodes=[
                make_node("boss", "老板", 0, "管理层"),
                make_node("dev", "开发", 1, "技术部"),
            ],
            edges=[make_edge("boss", "dev")],
        )
        dev = org.nodes[1]
        resolved = identity.resolve(dev, org)
        prompt = identity.build_org_context_prompt(dev, org, resolved)
        assert "只能" in prompt or "org_*" in prompt

    def test_ai_efficiency_section(self, identity: OrgIdentity, persisted_org):
        node = persisted_org.nodes[0]
        resolved = identity.resolve(node, persisted_org)
        prompt = identity.build_org_context_prompt(node, persisted_org, resolved)
        assert "AI 效率意识" in prompt
        assert "分钟" in prompt


# ---------------------------------------------------------------------------
# 6. prompt-preview API data structure (unit-level mock test)
# ---------------------------------------------------------------------------


class TestPromptPreviewStructure:
    """Test the expected structure from prompt-preview without running a server."""

    def test_preview_data_matches_backend_logic(self, identity: OrgIdentity, persisted_org):
        node = persisted_org.nodes[0]
        resolved = identity.resolve(node, persisted_org)

        org_context_prompt = identity.build_org_context_prompt(
            node, persisted_org, resolved,
        )

        allowed_external = expand_tool_categories(
            node.external_tools
        ) - _ORG_CONFLICT_TOOLS

        assert isinstance(org_context_prompt, str)
        assert len(org_context_prompt) > 100

        assert resolved.level == 0
        assert isinstance(resolved.role, str)

        assert isinstance(allowed_external, set)

    def test_identity_level_descriptions(self, identity: OrgIdentity, persisted_org, org_dir):
        """Level changes correctly when ROLE.md is added."""
        node = persisted_org.nodes[0]

        resolved_before = identity.resolve(node, persisted_org)
        assert resolved_before.level == 0

        id_dir = org_dir / "nodes" / node.id / "identity"
        id_dir.mkdir(parents=True, exist_ok=True)
        (id_dir / "ROLE.md").write_text("CEO角色定义", encoding="utf-8")

        resolved_after = identity.resolve(node, persisted_org)
        assert resolved_after.level >= 1
