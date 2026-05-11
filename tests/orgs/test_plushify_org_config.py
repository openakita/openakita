"""PlushifyStudio organization configuration acceptance tests."""

from __future__ import annotations

import json
from pathlib import Path

from openakita.agents.profile import AgentProfile
from openakita.orgs.models import EdgeType, Organization

ORG_PATH = Path("data/orgs/org_864ab23c9a2c/org.json")
PROFILE_DIR = Path("data/agents/profiles")

EXPECTED_PROFILE_IDS = {
    "plushify-lead",
    "plushify-architect",
    "plushify-planner",
    "plushify-context-scout",
    "plushify-reviewer",
    "plushify-delivery",
    "plushify-shared-contracts",
    "plushify-server-engineer",
    "plushify-client-main",
    "plushify-renderer-ui",
    "plushify-viewport-geometry",
    "plushify-business-algorithm",
    "plushify-sync-storage",
    "plushify-implementer",
    "plushify-test-unit",
    "plushify-test-e2e",
    "plushify-build-release",
    "plushify-docs-rfc",
    "plushify-security-qa",
    "plushify-verifier",
}

EXPECTED_DEPARTMENTS = {
    "研发管理",
    "架构治理",
    "上下文",
    "审查",
    "交付",
    "Shared 契约",
    "服务端",
    "客户端",
    "视口几何",
    "业务算法",
    "同步存储",
    "实现",
    "验证",
    "发布",
    "文档",
    "质量安全",
}

EXPECTED_SKILLS_BY_PROFILE = {
    "plushify-lead": {
        "obra/superpowers@dispatching-parallel-agents",
        "obra/superpowers@subagent-driven-development",
        "obra/superpowers@writing-plans",
    },
    "plushify-architect": {
        "obra/superpowers@brainstorming",
        "obra/superpowers@writing-plans",
        "openakita/skills@code-review",
    },
    "plushify-planner": {
        "obra/superpowers@writing-plans",
        "obra/superpowers@executing-plans",
        "obra/superpowers@subagent-driven-development",
    },
    "plushify-context-scout": {"obra/superpowers@brainstorming", "technical-blog-writing"},
    "plushify-reviewer": {"openakita/skills@code-review", "obra/superpowers@requesting-code-review"},
    "plushify-delivery": {"openakita/skills@changelog-generator", "openakita/skills@github-automation"},
    "plushify-shared-contracts": {"obra/superpowers@test-driven-development", "openakita/skills@code-review"},
    "plushify-server-engineer": {"obra/superpowers@systematic-debugging", "openakita/skills@code-review"},
    "plushify-client-main": {"obra/superpowers@systematic-debugging", "openakita/skills@code-review"},
    "plushify-renderer-ui": {"openakita/skills@frontend-design", "openakita/skills@webapp-testing"},
    "plushify-viewport-geometry": {"openakita/skills@webapp-testing", "openakita/skills@code-review"},
    "plushify-business-algorithm": {"obra/superpowers@test-driven-development", "obra/superpowers@systematic-debugging"},
    "plushify-sync-storage": {"obra/superpowers@systematic-debugging", "openakita/skills@code-review"},
    "plushify-implementer": {"obra/superpowers@subagent-driven-development", "obra/superpowers@test-driven-development"},
    "plushify-test-unit": {"obra/superpowers@test-driven-development", "obra/superpowers@verification-before-completion"},
    "plushify-test-e2e": {"openakita/skills@webapp-testing", "obra/superpowers@systematic-debugging"},
    "plushify-build-release": {"openakita/skills@github-automation", "openakita/skills@changelog-generator"},
    "plushify-docs-rfc": {"technical-blog-writing", "openakita/skills@changelog-generator"},
    "plushify-security-qa": {"openakita/skills@code-review", "obra/superpowers@requesting-code-review"},
    "plushify-verifier": {"obra/superpowers@verification-before-completion", "openakita/skills@webapp-testing"},
}


def _load_org() -> Organization:
    return Organization.from_dict(json.loads(ORG_PATH.read_text(encoding="utf-8")))


def _load_profile(profile_id: str) -> AgentProfile:
    path = PROFILE_DIR / f"{profile_id}.json"
    return AgentProfile.from_dict(json.loads(path.read_text(encoding="utf-8")))


class TestPlushifyOrgConfiguration:
    def test_expands_to_twenty_specialized_members(self):
        org = _load_org()
        profile_ids = {node.agent_profile_id for node in org.nodes}

        assert len(org.nodes) == 20
        assert profile_ids == EXPECTED_PROFILE_IDS
        assert set(org.get_departments()) == EXPECTED_DEPARTMENTS
        assert org.core_business == "PlushifyStudio BIZ-5+ 业务算法、三仓边界、验证、审查与发布交付"

    def test_workflow_routes_from_lead_to_planner_and_specialists(self):
        org = _load_org()
        lead_children = {node.id for node in org.get_children("node_plushify_lead")}
        planner_children = {node.id for node in org.get_children("node_plushify_planner")}

        assert {
            "node_plushify_architect",
            "node_plushify_planner",
            "node_plushify_context_scout",
            "node_plushify_reviewer",
            "node_plushify_delivery",
            "node_plushify_implementer",
            "node_plushify_verifier",
        } <= lead_children
        assert {
            "node_plushify_shared_contracts",
            "node_plushify_server_engineer",
            "node_plushify_client_main",
            "node_plushify_renderer_ui",
            "node_plushify_viewport_geometry",
            "node_plushify_business_algorithm",
            "node_plushify_sync_storage",
            "node_plushify_test_unit",
            "node_plushify_test_e2e",
            "node_plushify_build_release",
            "node_plushify_docs_rfc",
            "node_plushify_security_qa",
        } <= planner_children
        assert any(
            edge.edge_type == EdgeType.ESCALATE
            and edge.source == "node_plushify_delivery"
            and edge.target == "node_plushify_lead"
            for edge in org.edges
        )

    def test_every_member_has_99_concurrency_and_threshold_two_autoclone(self):
        org = _load_org()

        assert org.scaling_enabled is True
        assert org.auto_scale_enabled is True
        assert org.scaling_approval == "auto"
        assert org.max_nodes >= 20 + (20 * 99)
        for node in org.nodes:
            assert node.max_concurrent_tasks == 99
            assert node.auto_clone_enabled is True
            assert node.auto_clone_threshold == 2
            assert node.auto_clone_max == 99

    def test_profiles_enable_role_specific_skills(self):
        lead = _load_profile("plushify-lead")
        assert lead.role == "coordinator"

        for profile_id, expected_skills in EXPECTED_SKILLS_BY_PROFILE.items():
            profile = _load_profile(profile_id)
            assert profile.skills_mode.value == "inclusive"
            assert expected_skills <= set(profile.skills)
            assert profile.tools, profile_id
            assert "filesystem" in profile.tools or profile_id == "plushify-lead"
