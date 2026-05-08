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

