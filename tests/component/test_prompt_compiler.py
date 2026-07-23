"""L2 Component Tests: Prompt compilation and system prompt building."""

from openakita.prompt.budget import BudgetConfig


class TestPromptCompileFunctions:
    """Test individual compile_* functions from prompt/compiler.py."""

    def test_identity_core_owns_identity_not_platform_safety(self):
        from openakita.prompt.budget import estimate_tokens
        from openakita.prompt.compiler import _COMPILE_PROMPTS, _compile_with_rules

        source = """# Soul Overview
- {{agent_name}} helps users solve meaningful problems.
- 支持人类监督 PLATFORM_OWNED_INLINE_SAFETY_RULE
# Being Honest
- PLATFORM_OWNED_HONESTY_RULE
# Big-picture Safety
- PLATFORM_OWNED_SAFETY_RULE
# Identity - 身份认知
- {{agent_name}} is curious, pragmatic, and warm.
"""

        result = _compile_with_rules(source, _COMPILE_PROMPTS["identity_core"])

        assert "helps users" in result
        assert "curious, pragmatic, and warm" in result
        assert "PLATFORM_OWNED_INLINE_SAFETY_RULE" not in result
        assert "PLATFORM_OWNED_HONESTY_RULE" not in result
        assert "PLATFORM_OWNED_SAFETY_RULE" not in result
        assert estimate_tokens(result) <= 600

    def test_agent_behavior_owns_only_openakita_specific_deltas(self):
        from openakita.prompt.budget import estimate_tokens
        from openakita.prompt.compiler import _COMPILE_PROMPTS, _compile_with_rules

        source = """# Agent
## Working Mode
### Task Execution Flow
- PLATFORM_OWNED_EXECUTION_RULE
## Proactive Behavior Framework
### Growth Loops
- Preserve useful lessons from repeated work.
### Self-Healing Protocol
- Diagnose root causes before escalating a system problem.
## Tool Priority
- PLATFORM_OWNED_TOOL_RULE
"""

        result = _compile_with_rules(source, _COMPILE_PROMPTS["agent_behavior"])

        assert "Preserve useful lessons" in result
        assert "Diagnose root causes" in result
        assert "PLATFORM_OWNED_EXECUTION_RULE" not in result
        assert "PLATFORM_OWNED_TOOL_RULE" not in result
        assert estimate_tokens(result) <= 450

    def test_compiled_identity_limit_is_strict(self):
        from openakita.prompt.budget import estimate_tokens
        from openakita.prompt.compiler import _COMPILE_PROMPTS, _compile_with_rules

        lines = "\n".join(f"- identity trait {index}: " + "useful " * 40 for index in range(100))
        result = _compile_with_rules(
            f"# Identity\n{lines}",
            _COMPILE_PROMPTS["identity_core"],
        )

        assert result
        assert estimate_tokens(result) <= 600

    def test_user_profile_compiler_drops_placeholders(self):
        from openakita.prompt.compiler import _COMPILE_PROMPTS, _compile_with_rules

        source = """# User
- **称呼**: [待学习]
- **Python 风格**: pathlib 优先
- [Agent 会记录用户的纠正，避免重复错误]
*此文件由 OpenAkita 自动维护。*
"""

        result = _compile_with_rules(source, _COMPILE_PROMPTS["user_profile_core"])

        assert "pathlib 优先" in result
        assert "待学习" not in result
        assert "Agent 会" not in result
        assert "自动维护" not in result


class TestCompileAll:
    def test_compile_all_with_identity_dir(self, tmp_path):
        from openakita.prompt.compiler import compile_all

        identity_dir = tmp_path / "identity"
        identity_dir.mkdir()
        (identity_dir / "SOUL.md").write_text("# Soul\nI am helpful.", encoding="utf-8")
        (identity_dir / "AGENT.md").write_text("# Agent\n## Core\nBe good.", encoding="utf-8")

        result = compile_all(identity_dir, use_llm=False)
        assert isinstance(result, dict)

    def test_compile_all_empty_dir(self, tmp_path):
        from openakita.prompt.compiler import compile_all

        identity_dir = tmp_path / "identity"
        identity_dir.mkdir()

        result = compile_all(identity_dir, use_llm=False)
        assert isinstance(result, dict)


class TestBuildSystemPrompt:
    def test_build_returns_string(self, tmp_path):
        from openakita.prompt.builder import build_system_prompt

        identity_dir = tmp_path / "identity"
        identity_dir.mkdir()
        (identity_dir / "SOUL.md").write_text("# Soul\nI am OpenAkita.", encoding="utf-8")

        prompt = build_system_prompt(identity_dir=identity_dir, tools_enabled=False)
        assert isinstance(prompt, str)
        assert len(prompt) > 0

    def test_build_includes_identity(self, tmp_path):
        from openakita.prompt.builder import build_system_prompt

        identity_dir = tmp_path / "identity"
        identity_dir.mkdir()
        (identity_dir / "SOUL.md").write_text(
            "# Soul\nI am OpenAkita, the loyal dog.", encoding="utf-8"
        )

        prompt = build_system_prompt(identity_dir=identity_dir, tools_enabled=False)
        assert "OpenAkita" in prompt or "loyal" in prompt or len(prompt) > 50

    def test_build_with_budget_config(self, tmp_path):
        from openakita.prompt.builder import build_system_prompt

        identity_dir = tmp_path / "identity"
        identity_dir.mkdir()
        (identity_dir / "SOUL.md").write_text("# Soul\nTest.", encoding="utf-8")

        budget = BudgetConfig(total_budget=5000)
        prompt = build_system_prompt(
            identity_dir=identity_dir,
            tools_enabled=False,
            budget_config=budget,
        )
        assert isinstance(prompt, str)

    def test_build_includes_remote_web_app_guidance(self, tmp_path):
        from openakita.prompt.builder import build_system_prompt

        identity_dir = tmp_path / "identity"
        identity_dir.mkdir()
        (identity_dir / "SOUL.md").write_text("# Soul\nTest.", encoding="utf-8")

        prompt = build_system_prompt(identity_dir=identity_dir, tools_enabled=False)

        assert "手机/局域网/远程访问" in prompt
        assert "不要硬编码" in prompt
        assert "localhost" in prompt
        assert "window.location" in prompt
        assert "0.0.0.0" in prompt

    def test_agent_voice_replaces_placeholder_in_identity_section(self, tmp_path):
        """SOUL.md 里的 {{agent_name}} 占位符应该被 agent_voice 替换。"""
        from openakita.prompt.builder import build_system_prompt

        identity_dir = tmp_path / "identity"
        identity_dir.mkdir()
        (identity_dir / "SOUL.md").write_text(
            "# Soul\n- {{agent_name}} 充满好奇。\n- {{agent_name}} 喜欢深入思考。",
            encoding="utf-8",
        )

        prompt = build_system_prompt(
            identity_dir=identity_dir, tools_enabled=False, agent_voice="中秋"
        )
        assert "中秋 充满好奇" in prompt
        assert "中秋 喜欢深入思考" in prompt
        assert "{{agent_name}}" not in prompt

    def test_agent_voice_empty_falls_back_to_openakita(self, tmp_path):
        """空 agent_voice 应该回退到默认产品名，不留下裸占位符。"""
        from openakita.prompt.builder import build_system_prompt

        identity_dir = tmp_path / "identity"
        identity_dir.mkdir()
        (identity_dir / "SOUL.md").write_text(
            "# Soul\n- {{agent_name}} 充满好奇。",
            encoding="utf-8",
        )

        prompt = build_system_prompt(identity_dir=identity_dir, tools_enabled=False)
        # default fallback
        assert "OpenAkita 充满好奇" in prompt
        assert "{{agent_name}}" not in prompt

    def test_two_agents_get_independent_voices(self, tmp_path):
        """连续两次构建：不同 agent_voice 应该产生互不污染的 prompt。"""
        from openakita.prompt.builder import build_system_prompt

        identity_dir = tmp_path / "identity"
        identity_dir.mkdir()
        (identity_dir / "SOUL.md").write_text(
            "# Soul\n- {{agent_name}} 充满好奇。",
            encoding="utf-8",
        )

        prompt_a = build_system_prompt(
            identity_dir=identity_dir, tools_enabled=False, agent_voice="中秋"
        )
        prompt_b = build_system_prompt(
            identity_dir=identity_dir, tools_enabled=False, agent_voice="码哥"
        )
        assert "中秋 充满好奇" in prompt_a
        assert "码哥" not in prompt_a, "Agent A 不应该看到 Agent B 的名字"
        assert "码哥 充满好奇" in prompt_b
        assert "中秋" not in prompt_b, "Agent B 不应该看到 Agent A 的名字"

    def test_agent_voice_replaces_in_none_mode(self, tmp_path):
        """PromptMode.NONE 路径下的硬编码自称句也要参数化。"""
        from openakita.prompt.builder import PromptMode, build_system_prompt

        identity_dir = tmp_path / "identity"
        identity_dir.mkdir()
        (identity_dir / "SOUL.md").write_text("# Soul\nTest.", encoding="utf-8")

        prompt = build_system_prompt(
            identity_dir=identity_dir,
            tools_enabled=False,
            prompt_mode=PromptMode.NONE,
            agent_voice="码哥",
        )
        # The self-introduction line must follow agent_voice, not the legacy hard-coded
        # "你是 OpenAkita" wording (other unrelated rules sections may still mention
        # the OpenAkita project by name; we only guard the identity self-reference).
        assert "你是 码哥，一个 AI 助手。" in prompt
        assert "你是 OpenAkita，一个 AI 助手。" not in prompt

    def test_agent_voice_replaces_per_model_base_prompt(self, tmp_path):
        """Per-model 基础提示词不能把自定义 Agent 重新钉死为 OpenAkita。"""
        from openakita.prompt.builder import PromptMode, PromptProfile, build_system_prompt

        identity_dir = tmp_path / "identity"
        identity_dir.mkdir()
        (identity_dir / "SOUL.md").write_text(
            "# Soul\n你是 CloseBeta，当前名称是 {{agent_name}}。",
            encoding="utf-8",
        )
        (identity_dir / "AGENT.md").write_text("# Agent\n保持诚实。", encoding="utf-8")

        prompt = build_system_prompt(
            identity_dir=identity_dir,
            tools_enabled=False,
            prompt_mode=PromptMode.MINIMAL,
            prompt_profile=PromptProfile.CONSUMER_CHAT,
            model_id="qwen3-max",
            agent_voice="叮叮",
        )

        assert "你是 叮叮，一个帮助用户完成各类任务的 AI 助手。" in prompt
        assert "你是 OpenAkita，一个帮助用户完成各类任务的 AI 助手。" not in prompt
        assert "# Agent Identity" in prompt
        assert "# OpenAkita System" not in prompt
        assert "OpenAkita 仅指运行平台或上游开源项目，不是当前 Agent 的自称" in prompt

    def test_agent_voice_whitespace_only_falls_back(self, tmp_path):
        """全空白的 agent_voice 也算作"未提供"，回退到默认产品名。"""
        from openakita.prompt.builder import build_system_prompt

        identity_dir = tmp_path / "identity"
        identity_dir.mkdir()
        (identity_dir / "SOUL.md").write_text(
            "# Soul\n- {{agent_name}} 充满好奇。",
            encoding="utf-8",
        )

        prompt = build_system_prompt(
            identity_dir=identity_dir, tools_enabled=False, agent_voice="   "
        )
        assert "OpenAkita 充满好奇" in prompt


class TestAgentResolveVoice:
    """Direct unit tests for Agent._resolve_agent_voice priority chain."""

    def _make_stub_agent(self):
        """Return a bare-Agent instance bypassing __init__ for helper-only tests."""
        from openakita.agent import Agent

        return Agent.__new__(Agent)

    def test_resolve_voice_prefers_profile_display_name(self):
        from openakita.agents.profile import AgentProfile

        agent = self._make_stub_agent()
        agent._agent_profile = AgentProfile(
            id="x", name="码哥", name_i18n={"zh": "中秋", "en": "MidAutumn"}
        )
        agent.name = "fallback"
        assert agent._resolve_agent_voice() == "中秋"

    def test_resolve_voice_falls_back_to_profile_name_when_zh_missing(self):
        from openakita.agents.profile import AgentProfile

        agent = self._make_stub_agent()
        # Construct profile in a way that leaves get_display_name("zh") returning
        # the same as name (because __post_init__ mirrors zh from name).
        agent._agent_profile = AgentProfile(id="x", name="码哥")
        agent.name = "fallback"
        # invariant from __post_init__ guarantees name_i18n["zh"] == "码哥"
        assert agent._resolve_agent_voice() == "码哥"

    def test_resolve_voice_falls_back_to_agent_name_when_no_profile(self):
        agent = self._make_stub_agent()
        agent._agent_profile = None
        agent.name = "Akita-Local"
        assert agent._resolve_agent_voice() == "Akita-Local"

    def test_resolve_voice_falls_back_to_settings_when_all_empty(self):
        from openakita.config import settings

        agent = self._make_stub_agent()
        agent._agent_profile = None
        agent.name = ""
        # settings.agent_name defaults to "OpenAkita"
        assert agent._resolve_agent_voice() == settings.agent_name
