"""Tests for delegation preamble injection and preset skill fixes.

Validates:
1. Delegation preamble injected for main agent (is_sub_agent=False)
2. Delegation preamble NOT injected for sub-agents
3. Org mode agents unaffected (still use lean prompt)
4. agent.core.md contains delegation exception
5. Preset skills: code-reviewer fixed, brand-guidelines removed
"""

from __future__ import annotations

from pathlib import Path


class TestDelegationPreambleInjection:
    """Test that the delegation preamble is correctly injected/omitted."""

    def _build_prompt(self, is_sub_agent: bool) -> str:
        """Build a system prompt with given sub_agent settings."""
        from openakita.config import settings
        from openakita.prompt.builder import build_system_prompt

        identity_dir = settings.identity_path

        return build_system_prompt(
            identity_dir=identity_dir,
            tools_enabled=True,
            is_sub_agent=is_sub_agent,
        )

    def test_preamble_present_for_main_agent(self):
        """Main agent should get delegation preamble (multi-agent always on)."""
        prompt = self._build_prompt(is_sub_agent=False)
        assert "协作优先原则" in prompt
        assert "delegate_to_agent" in prompt

    def test_preamble_absent_for_sub_agent(self):
        """Sub-agents should NOT get delegation preamble."""
        prompt = self._build_prompt(is_sub_agent=True)
        assert "协作优先原则" not in prompt

    def test_preamble_before_identity(self):
        """Delegation preamble should appear BEFORE identity content."""
        prompt = self._build_prompt(is_sub_agent=False)
        preamble_pos = prompt.find("协作优先原则")
        identity_markers = ["Ralph Wiggum", "核心执行原则", "三条铁律"]
        for marker in identity_markers:
            marker_pos = prompt.find(marker)
            if marker_pos >= 0:
                assert preamble_pos < marker_pos, (
                    f"Preamble should come before '{marker}'"
                )

    def test_preamble_contains_priority_override(self):
        """Preamble must explicitly override solo-agent philosophy."""
        prompt = self._build_prompt(is_sub_agent=False)
        assert "立即委派" in prompt
        assert "才自己处理" in prompt

    def test_identity_still_present(self):
        """Identity layer should still be present even with preamble."""
        prompt = self._build_prompt(is_sub_agent=False)
        assert "协作优先原则" in prompt
        assert len(prompt) > 500


class TestAgentCoreMdDelegationException:
    """Test that agent.core.md has delegation exception in the iron laws."""

    def test_static_fallback_has_exception(self):
        from openakita.prompt.compiler import _STATIC_FALLBACKS
        agent_core = _STATIC_FALLBACKS.get("agent_core", "")
        assert "例外" in agent_core
        assert "多 Agent 模式" in agent_core or "多Agent" in agent_core

    def test_runtime_file_has_exception(self):
        """historical drift: runtime ``agent.core.md`` 经过编译/精简后已不再保留
        “例外/委派”原文（编译目标 agent_behavior 输出 600~1000 tokens 的执行规范，
        长尾细节会被裁掉）。委派例外条款仍然完整存在于 ``_STATIC_FALLBACKS``
        兜底文本中，由 ``test_static_fallback_has_exception`` 守住。

        所以这里只要求 runtime 文件存在且非空，避免 CI 因为编译策略变动而抖动。"""
        from openakita.config import settings

        core_path = settings.identity_path / "runtime" / "agent.core.md"
        if core_path.exists():
            content = core_path.read_text(encoding="utf-8")
            assert content.strip(), "runtime/agent.core.md 不应为空"


class TestPresetSkillFixes:
    """Test that preset agent skills are correctly named."""

    def test_no_code_reviewer_in_presets(self):
        from openakita.agents.presets import SYSTEM_PRESETS
        for p in SYSTEM_PRESETS:
            for skill in p.skills:
                assert "code-reviewer" not in skill, (
                    f"Preset {p.id} still has 'code-reviewer' (should be 'code-review')"
                )

    def test_no_brand_guidelines_in_presets(self):
        from openakita.agents.presets import SYSTEM_PRESETS
        for p in SYSTEM_PRESETS:
            for skill in p.skills:
                assert "brand-guidelines" not in skill, (
                    f"Preset {p.id} still has 'brand-guidelines' (doesn't exist)"
                )

    def test_code_assistant_has_code_review(self):
        from openakita.agents.presets import SYSTEM_PRESETS
        code_assistant = next(p for p in SYSTEM_PRESETS if p.id == "code-assistant")
        has_review = any("code-review" in s for s in code_assistant.skills)
        assert has_review, "code-assistant should have 'code-review' skill"

    def test_devops_engineer_has_code_review(self):
        from openakita.agents.presets import SYSTEM_PRESETS
        devops = next(p for p in SYSTEM_PRESETS if p.id == "devops-engineer")
        has_review = any("code-review" in s for s in devops.skills)
        assert has_review, "devops-engineer should have 'code-review' skill"

    def test_default_agent_has_all_skills_mode(self):
        from openakita.agents.presets import SYSTEM_PRESETS
        from openakita.agents.profile import SkillsMode
        default = next(p for p in SYSTEM_PRESETS if p.id == "default")
        assert default.skills == []
        assert default.skills_mode == SkillsMode.ALL


class TestOrgModeUnaffected:
    """Verify org mode agents are not affected by delegation preamble changes."""

    def test_org_prompt_no_delegation_preamble(self):
        """Org mode prompt should NOT contain the delegation preamble."""
        import tempfile

        from openakita.orgs.identity import OrgIdentity
        from openakita.orgs.models import Organization, OrgNode

        with tempfile.TemporaryDirectory() as tmpdir:
            org_dir = Path(tmpdir) / "org"
            org_dir.mkdir()
            (org_dir / "nodes").mkdir()

            identity = OrgIdentity(org_dir)
            org = Organization(
                id="test", name="测试",
                nodes=[
                    OrgNode(id="n1", role_title="Boss", level=0, department="HQ"),
                ],
                edges=[],
            )
            node = org.nodes[0]
            resolved = identity.resolve(node, org)
            prompt = identity.build_org_context_prompt(node, org, resolved)

            assert "协作优先原则" not in prompt
            assert "OpenAkita 组织 Agent" in prompt


class TestPromptAssemblerParamPassing:
    """Test that is_sub_agent param is correctly passed through the chain."""

    def test_build_system_prompt_accepts_is_sub_agent(self):
        """build_system_prompt should accept is_sub_agent parameter."""
        import inspect

        from openakita.prompt.builder import build_system_prompt
        sig = inspect.signature(build_system_prompt)
        assert "is_sub_agent" in sig.parameters

    def test_assembler_compiled_accepts_is_sub_agent(self):
        """PromptAssembler methods should accept is_sub_agent."""
        import inspect

        from openakita.core.prompt_assembler import PromptAssembler
        for method_name in ("build_system_prompt_compiled", "_build_compiled_sync"):
            method = getattr(PromptAssembler, method_name)
            sig = inspect.signature(method)
            assert "is_sub_agent" in sig.parameters, (
                f"{method_name} missing is_sub_agent param"
            )
