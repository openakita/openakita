"""L2 Component Tests: Prompt compilation and system prompt building."""

from openakita.prompt.budget import BudgetConfig


class TestPromptCompileFunctions:
    """Test individual compile_* functions from prompt/compiler.py."""

    def test_compile_soul(self):
        from openakita.prompt.compiler import compile_soul
        result = compile_soul("You are OpenAkita, a loyal AI assistant.")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_compile_soul_empty(self):
        from openakita.prompt.compiler import compile_soul
        result = compile_soul("")
        assert isinstance(result, str)

    def test_compile_agent_core(self):
        from openakita.prompt.compiler import compile_agent_core
        result = compile_agent_core("## Core Behaviors\n- Never give up\n- Be honest")
        assert isinstance(result, str)

    def test_compile_user(self):
        from openakita.prompt.compiler import compile_user
        result = compile_user("User prefers Chinese. Name: 小明")
        assert isinstance(result, str)


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
        (identity_dir / "SOUL.md").write_text("# Soul\nI am OpenAkita, the loyal dog.", encoding="utf-8")

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
            "# Soul\n- {{agent_name}} 应该诚实。\n- {{agent_name}} 不放弃。",
            encoding="utf-8",
        )

        prompt = build_system_prompt(
            identity_dir=identity_dir, tools_enabled=False, agent_voice="中秋"
        )
        assert "中秋 应该诚实" in prompt
        assert "中秋 不放弃" in prompt
        assert "{{agent_name}}" not in prompt

    def test_agent_voice_empty_falls_back_to_openakita(self, tmp_path):
        """空 agent_voice 应该回退到默认产品名，不留下裸占位符。"""
        from openakita.prompt.builder import build_system_prompt

        identity_dir = tmp_path / "identity"
        identity_dir.mkdir()
        (identity_dir / "SOUL.md").write_text(
            "# Soul\n- {{agent_name}} 应该诚实。",
            encoding="utf-8",
        )

        prompt = build_system_prompt(identity_dir=identity_dir, tools_enabled=False)
        # default fallback
        assert "OpenAkita 应该诚实" in prompt
        assert "{{agent_name}}" not in prompt

    def test_two_agents_get_independent_voices(self, tmp_path):
        """连续两次构建：不同 agent_voice 应该产生互不污染的 prompt。"""
        from openakita.prompt.builder import build_system_prompt

        identity_dir = tmp_path / "identity"
        identity_dir.mkdir()
        (identity_dir / "SOUL.md").write_text(
            "# Soul\n- {{agent_name}} 应该诚实。",
            encoding="utf-8",
        )

        prompt_a = build_system_prompt(
            identity_dir=identity_dir, tools_enabled=False, agent_voice="中秋"
        )
        prompt_b = build_system_prompt(
            identity_dir=identity_dir, tools_enabled=False, agent_voice="码哥"
        )
        assert "中秋 应该诚实" in prompt_a
        assert "码哥" not in prompt_a, "Agent A 不应该看到 Agent B 的名字"
        assert "码哥 应该诚实" in prompt_b
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

    def test_agent_voice_whitespace_only_falls_back(self, tmp_path):
        """全空白的 agent_voice 也算作"未提供"，回退到默认产品名。"""
        from openakita.prompt.builder import build_system_prompt

        identity_dir = tmp_path / "identity"
        identity_dir.mkdir()
        (identity_dir / "SOUL.md").write_text(
            "# Soul\n- {{agent_name}} 应该诚实。",
            encoding="utf-8",
        )

        prompt = build_system_prompt(
            identity_dir=identity_dir, tools_enabled=False, agent_voice="   "
        )
        assert "OpenAkita 应该诚实" in prompt


class TestAgentResolveVoice:
    """Direct unit tests for Agent._resolve_agent_voice priority chain."""

    def _make_stub_agent(self):
        """Return a bare-Agent instance bypassing __init__ for helper-only tests."""
        from openakita.core.agent import Agent

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

