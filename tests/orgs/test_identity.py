"""Tests for OrgIdentity — prompt building, layered inheritance."""

from __future__ import annotations

from pathlib import Path

import pytest

from openakita.orgs.identity import OrgIdentity, ResolvedIdentity
from .conftest import make_org, make_node, make_edge


@pytest.fixture()
def identity(org_dir: Path, tmp_path: Path) -> OrgIdentity:
    global_identity = tmp_path / "identity"
    global_identity.mkdir()
    return OrgIdentity(org_dir, global_identity)


class TestResolve:
    def test_returns_resolved_identity(self, identity: OrgIdentity, persisted_org):
        node = persisted_org.nodes[0]
        resolved = identity.resolve(node, persisted_org)
        assert isinstance(resolved, ResolvedIdentity)
        assert resolved.level >= 0
        assert isinstance(resolved.soul, str)
        assert isinstance(resolved.agent, str)
        assert isinstance(resolved.role, str)

    def test_node_with_identity_files(self, identity: OrgIdentity, persisted_org, org_dir: Path):
        node = persisted_org.nodes[0]
        id_dir = org_dir / "nodes" / node.id / "identity"
        id_dir.mkdir(parents=True, exist_ok=True)
        (id_dir / "SOUL.md").write_text("# 灵魂\n我是CEO的灵魂文件", encoding="utf-8")
        (id_dir / "ROLE.md").write_text("# 角色\n首席执行官", encoding="utf-8")

        resolved = identity.resolve(node, persisted_org)
        assert "灵魂" in resolved.soul or "CEO" in resolved.soul
        assert resolved.role != ""

    def test_global_identity_fallback(self, identity: OrgIdentity, persisted_org, tmp_path: Path):
        global_dir = tmp_path / "identity"
        (global_dir / "SOUL.md").write_text("# 全局灵魂\n默认灵魂", encoding="utf-8")

        node = persisted_org.nodes[0]
        resolved = identity.resolve(node, persisted_org)
        assert "默认灵魂" in resolved.soul or resolved.soul != ""


class TestBuildOrgContextPrompt:
    def test_contains_org_info(self, identity: OrgIdentity, persisted_org):
        node = persisted_org.nodes[0]
        resolved = identity.resolve(node, persisted_org)
        prompt = identity.build_org_context_prompt(
            node, persisted_org, resolved,
            blackboard_summary="- 决策: 使用Python",
        )
        assert persisted_org.name in prompt
        assert node.role_title in prompt

    def test_includes_blackboard(self, identity: OrgIdentity, persisted_org):
        node = persisted_org.nodes[0]
        resolved = identity.resolve(node, persisted_org)
        prompt = identity.build_org_context_prompt(
            node, persisted_org, resolved,
            blackboard_summary="- 重要决策: 采用微服务",
        )
        assert "微服务" in prompt

    def test_includes_dept_summary(self, identity: OrgIdentity, persisted_org):
        node = persisted_org.nodes[1]
        resolved = identity.resolve(node, persisted_org)
        prompt = identity.build_org_context_prompt(
            node, persisted_org, resolved,
            dept_summary="- 技术部会议纪要",
        )
        assert "技术部会议纪要" in prompt

    def test_includes_policy_index(self, identity: OrgIdentity, persisted_org):
        node = persisted_org.nodes[0]
        resolved = identity.resolve(node, persisted_org)
        prompt = identity.build_org_context_prompt(
            node, persisted_org, resolved,
            policy_index="- 沟通规范.md\n- 任务管理.md",
        )
        assert "沟通规范" in prompt


class TestCoreBusiness:
    """Tests for core_business prompt injection (v1.3)."""

    def test_root_node_gets_continuous_duty(self, identity: OrgIdentity):
        org = make_org(core_business="做一个内容平台")
        root = org.nodes[0]  # level=0
        resolved = identity.resolve(root, org)
        prompt = identity.build_org_context_prompt(root, org, resolved)
        assert "核心业务" in prompt
        assert "连续工作职责" in prompt
        assert "最高负责人" in prompt
        assert "做一个内容平台" in prompt

    def test_non_root_node_gets_supporting_prompt(self, identity: OrgIdentity):
        org = make_org(core_business="做一个内容平台")
        non_root = org.nodes[1]  # level=1
        resolved = identity.resolve(non_root, org)
        prompt = identity.build_org_context_prompt(non_root, org, resolved)
        assert "核心业务" in prompt
        assert "做一个内容平台" in prompt
        assert "连续工作职责" not in prompt
        assert "主动配合上级" in prompt

    def test_no_core_business_no_section(self, identity: OrgIdentity):
        org = make_org(core_business="")
        root = org.nodes[0]
        resolved = identity.resolve(root, org)
        prompt = identity.build_org_context_prompt(root, org, resolved)
        assert "核心业务" not in prompt
        assert "连续工作职责" not in prompt

    def test_root_prompt_uses_dynamic_persona_label(self, identity: OrgIdentity):
        from openakita.orgs.models import UserPersona
        org = make_org(
            core_business="研发 AI 产品",
            user_persona=UserPersona(title="投资人", display_name="王总"),
        )
        root = org.nodes[0]
        resolved = identity.resolve(root, org)
        prompt = identity.build_org_context_prompt(root, org, resolved)
        assert "王总" in prompt
        assert "不需要等王总" in prompt or "等王总" in prompt

    def test_root_prompt_uses_default_persona(self, identity: OrgIdentity):
        org = make_org(core_business="项目开发")
        root = org.nodes[0]
        resolved = identity.resolve(root, org)
        prompt = identity.build_org_context_prompt(root, org, resolved)
        assert "负责人" in prompt

    def test_root_prompt_no_hardcoded_ceo(self, identity: OrgIdentity):
        """Ensure generic prompt templates don't hardcode 'CEO' — use a custom org with no CEO/CTO."""
        org = make_org(
            core_business="内容运营",
            nodes=[
                make_node("node_chief", "主编", 0, "编辑部"),
                make_node("node_writer", "写手", 1, "创作组"),
            ],
            edges=[make_edge("node_chief", "node_writer")],
        )
        root = org.nodes[0]
        resolved = identity.resolve(root, org)
        prompt = identity.build_org_context_prompt(root, org, resolved)
        template_sections = ["核心业务", "连续工作职责", "组织工具与行为约束",
                             "你的权限", "制度与流程", "行为准则"]
        for section_name in template_sections:
            idx = prompt.find(section_name)
            if idx < 0:
                continue
            section_end = prompt.find("\n## ", idx + 1)
            section_text = prompt[idx:section_end] if section_end > 0 else prompt[idx:]
            assert "CEO" not in section_text, (
                f"Found hardcoded 'CEO' in template section '{section_name}'"
            )


class TestUserPersonaInPrompt:
    """Tests for user_persona in org prompt (v1.1)."""

    def test_root_shows_persona_as_superior(self, identity: OrgIdentity):
        from openakita.orgs.models import UserPersona
        org = make_org(user_persona=UserPersona(title="甲方", display_name="客户A"))
        root = org.nodes[0]
        resolved = identity.resolve(root, org)
        prompt = identity.build_org_context_prompt(root, org, resolved)
        assert "客户A" in prompt
        assert "指挥者" in prompt

    def test_non_root_shows_parent_as_superior(self, identity: OrgIdentity):
        org = make_org()
        non_root = org.nodes[1]
        resolved = identity.resolve(non_root, org)
        prompt = identity.build_org_context_prompt(non_root, org, resolved)
        assert "直属上级" in prompt
        assert org.nodes[0].role_title in prompt


class TestMCPConfig:
    def test_resolve_mcp_inherit_mode(self, identity: OrgIdentity, persisted_org, org_dir: Path):
        node = persisted_org.nodes[0]
        config = identity.resolve_mcp_config(node)
        assert isinstance(config, dict)
